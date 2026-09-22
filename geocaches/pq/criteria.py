"""
Pocket Query criteria scraper — reads a PQ's actual search criteria from its
geocaching.com edit page.

The PQ list page (``pocket/default.aspx``) only exposes name/count; it has no
visibility into what a PQ actually searches for. This module scrapes the
classic ASP.NET edit form (``pocket/gcquery.aspx?guid=<GUID>``) to read
type/container/found-owned/difficulty-terrain/origin/date-range criteria, so
a group of "same search, different date range" PQs can be verified as
actually sharing the same non-date criteria, and so a date-range split
proposal can filter locally-known caches the same way GC would.

Field names and value tokens are documented in
``docs/reference/geocaching-com.md``, "Pocket Query edit form (gcquery.aspx)".

``apply_pq_date_range`` writes a new Placed/Between date range back into a
PQ (a read-modify-write postback, same shape as ``trigger.delete_pqs``)
— the only write path in this module; everything else here is read-only.
"""

import logging
from datetime import date

from bs4 import BeautifulSoup, Tag
from django.db.models import Q
from django.utils.translation import gettext_lazy as _

from geocaches.models import CacheStatus, IgnoreListEntry, IgnoreSource
from geocaches.pq.trigger import _fetch_page, _gc_today_pst, _pq_reset_session, _pq_session
from geocaches.filtering.query import mine_q
from geocaches.sync.gc_reference import _SIZE_MAP, _TYPE_MAP

logger = logging.getLogger("geocaches.pq")

_PQ_EDIT_URL_TMPL = "https://www.geocaching.com/pocket/gcquery.aspx?guid={guid}"
_PREFIX = "ctl00$ContentBody$"

# cbOptions checkbox `value` -> semantic slug.
_OPTION_SLUGS = {
    "2": "not_found", "1": "found",
    "6": "not_owned", "8": "owned",
    "17": "available_to_all", "4": "members_only",
    "16": "not_ignored", "9": "on_watchlist",
    "7": "found_last_7d", "5": "not_found_ever",
    "3": "has_travel_bugs", "10": "updated_last_7d",
    "12": "disabled", "11": "enabled",
}

# Criteria that can't be translated into a local queryset filter.
_UNSUPPORTED_OPTION_WARNINGS = {
    "on_watchlist": _("'Are on my watch list' can't be verified locally — result count may be off."),
    "found_last_7d": _("'Found in the last 7 days' is a moving window and can't be reproduced from a static snapshot."),
    "updated_last_7d": _("'Updated in the last 7 days' is a moving window and can't be reproduced from a static snapshot."),
    "has_travel_bugs": _("'Have Travel Bugs' can't be verified locally — result count may be off."),
}

_FIELD_LABELS = {
    "results_cap": "Caches Total",
    "type": "Cache Type",
    "container": "Container",
    "options": "Options (found/owned/ignore list/etc.)",
    "difficulty": "Difficulty",
    "terrain": "Terrain",
    "country_state": "Country/State",
    "origin": "Origin (location/radius)",
}

_CMP_LOOKUPS = {">=": "gte", "<=": "lte", "=": "exact"}


def _find(form: Tag, name: str) -> Tag | None:
    el = form.find(attrs={"name": _PREFIX + name})
    return el if isinstance(el, Tag) else None


def _value(form: Tag, name: str, default: str = "") -> str:
    el = _find(form, name)
    return (el.get("value") or default) if el is not None else default


def _checkbox_checked(form: Tag, name: str) -> bool:
    el = _find(form, name)
    return el is not None and el.has_attr("checked")


def _checked_radio_value(form: Tag, name: str) -> str:
    for el in form.find_all("input", {"type": "radio", "name": _PREFIX + name}):
        if el.has_attr("checked"):
            return el.get("value", "")
    return ""


def _checked_checkboxes(form: Tag, name_prefix: str, count: int) -> dict[str, bool]:
    out: dict[str, bool] = {}
    for i in range(count):
        el = _find(form, f"{name_prefix}${i}")
        if el is not None:
            out[el.get("value", "")] = el.has_attr("checked")
    return out


def _select_value(form: Tag, name: str, default: str = "") -> str:
    sel = _find(form, name)
    if sel is None:
        return default
    opt = sel.find("option", selected=True) or sel.find("option")
    return opt.get("value", default) if opt else default


def _parse_ymd(form: Tag, prefix: str) -> date | None:
    try:
        month = int(_select_value(form, f"{prefix}$Month"))
        day = int(_select_value(form, f"{prefix}$Day"))
        year = int(_select_value(form, f"{prefix}$Year"))
        return date(year, month, day)
    except (TypeError, ValueError):
        return None


def get_pq_criteria(guid: str) -> dict:
    """Scrape a PQ's full search criteria from its edit page.

    Raises RuntimeError if the session is expired or the form can't be found
    (same conventions as trigger.py).
    """
    url = _PQ_EDIT_URL_TMPL.format(guid=guid)
    soup = _fetch_page(url)
    return _parse_criteria_soup(soup)


def _parse_criteria_soup(soup: BeautifulSoup) -> dict:
    """Parse the criteria dict out of an already-fetched edit-page soup.

    Split out from get_pq_criteria() so the parsing logic can be unit tested
    against a saved fixture HTML without a live GC session.
    """
    form = soup.find("form", id="aspnetForm")
    if not isinstance(form, Tag):
        raise RuntimeError("Could not find the Pocket Query edit form on the page.")

    type_ids = sorted(
        int(v) for v, checked in _checked_checkboxes(form, "cbTaxonomy", 11).items()
        if checked and v
    )
    size_ids = sorted(
        int(v) for v, checked in _checked_checkboxes(form, "cbContainers", 7).items()
        if checked and v
    )

    options = {}
    for v, checked in _checked_checkboxes(form, "cbOptions", 14).items():
        slug = _OPTION_SLUGS.get(v)
        if slug:
            options[slug] = checked

    def _range_field(enable_name: str, cmp_name: str, score_name: str) -> dict:
        return {
            "enabled": _checkbox_checked(form, enable_name),
            "cmp": _select_value(form, cmp_name, ">="),
            "score": float(_select_value(form, score_name, "1") or 1),
        }

    origin_value = _checked_radio_value(form, "Origin")
    origin_mode = {
        "rbOriginNone": "none", "rbOriginHome": "home", "rbOriginGC": "gccode",
        "rbPostalCode": "postal", "rbOriginWpt": "coords",
    }.get(origin_value, "none")

    origin = {"mode": origin_mode, "lat": None, "lon": None, "radius_km": None}
    if origin_mode == "coords":
        try:
            lat = float(_value(form, "LatLong$_inputLatDegs", "0")) + \
                float(_value(form, "LatLong$_inputLatMins", "0")) / 60.0
            if _select_value(form, "LatLong:_selectNorthSouth", "1") == "2":
                lat = -lat
            lon = float(_value(form, "LatLong$_inputLongDegs", "0")) + \
                float(_value(form, "LatLong$_inputLongMins", "0")) / 60.0
            if _select_value(form, "LatLong:_selectEastWest", "1") == "2":
                lon = -lon
            radius = float(_value(form, "tbRadius", "0") or 0)
            unit = _checked_radio_value(form, "rbUnitType")
            radius_km = radius if unit == "km" else radius * 1.609344
            origin.update({"lat": lat, "lon": lon, "radius_km": radius_km})
        except ValueError:
            logger.warning("PQ criteria: could not parse coordinate origin fields")

    placed_value = _checked_radio_value(form, "Placed")
    placed_mode = {
        "rbPlacedNone": "none", "rbPlacedLast": "last", "rbPlacedBetween": "between",
    }.get(placed_value, "none")
    date_from = date_to = None
    if placed_mode == "between":
        date_from = _parse_ymd(form, "DateTimeBegin")
        date_to = _parse_ymd(form, "DateTimeEnd")

    try:
        results_cap = int(_value(form, "tbResults", "1000") or 1000)
    except ValueError:
        results_cap = 1000

    cs_value = _checked_radio_value(form, "CountryState")

    return {
        "name": _value(form, "tbName"),
        "results_cap": results_cap,
        "type": {
            "mode": "select" if _checked_radio_value(form, "Type") == "rbTypeSelect" else "any",
            "type_ids": type_ids,
        },
        "container": {
            "mode": "select" if _checked_radio_value(form, "Container") == "rbContainerSelect" else "any",
            "size_ids": size_ids,
        },
        "options": options,
        "difficulty": _range_field("cbDifficulty", "ddDifficulty", "ddDifficultyScore"),
        "terrain": _range_field("cbTerrain", "ddTerrain", "ddTerrainScore"),
        "country_state": {
            "mode": {"rbNone": "none", "rbCountries": "countries", "rbStates": "states"}.get(cs_value, "none"),
        },
        "origin": origin,
        "placed": {"mode": placed_mode, "date_from": date_from, "date_to": date_to},
    }


def diff_pq_criteria(a: dict, b: dict) -> list[str]:
    """Human-readable field differences, excluding name/placed (expected to differ)."""
    diffs = []
    for key, label in _FIELD_LABELS.items():
        if a.get(key) != b.get(key):
            diffs.append(f"{label} differs: {a.get(key)!r} vs {b.get(key)!r}")
    return diffs


def _range_q(field_name: str, cmp: str, score: float) -> Q:
    lookup = _CMP_LOOKUPS.get(cmp, "gte")
    return Q(**{f"{field_name}__{lookup}": score})


def criteria_to_filter(criteria: dict) -> tuple[Q, str | None, list[str]]:
    """Best-effort local Q filter (+ optional ``?geo=`` circle param) for the
    criteria fields we can map onto ``Geocache`` locally, plus warnings for
    anything that can't be verified locally.

    Always excludes archived caches, regardless of configured criteria —
    those never appear in a live PQ result.
    """
    warnings: list[str] = []
    q = Q()

    type_c = criteria["type"]
    if type_c["mode"] == "select":
        names = [_TYPE_MAP[i] for i in type_c["type_ids"] if i in _TYPE_MAP]
        q &= Q(cache_type__in=names)

    container_c = criteria["container"]
    if container_c["mode"] == "select":
        names = [_SIZE_MAP[i] for i in container_c["size_ids"] if i in _SIZE_MAP]
        # Respect size_override the same way filter_expr._size_q does.
        q &= (Q(size_override__in=names) | Q(size_override__isnull=True, size__in=names))

    opts = criteria["options"]
    if opts.get("not_found"):
        q &= Q(found=False)
    if opts.get("found"):
        q &= Q(found=True)
    if opts.get("not_owned"):
        q &= ~mine_q()
    if opts.get("owned"):
        q &= mine_q()
    if opts.get("members_only"):
        q &= Q(is_premium=True)
    if opts.get("available_to_all"):
        q &= Q(is_premium=False)
    if opts.get("disabled"):
        q &= Q(status=CacheStatus.DISABLED)
    if opts.get("enabled"):
        q &= Q(status=CacheStatus.ACTIVE)
    if opts.get("not_ignored"):
        ignored_codes = set(
            IgnoreListEntry.objects.filter(source=IgnoreSource.GC).values_list("code", flat=True)
        )
        if ignored_codes:
            q &= ~Q(gc_code__in=ignored_codes)
        warnings.append(
            _(
                "'Are not on my ignore list' uses GCForge's locally-synced GC ignore list — "
                "re-sync it in Settings if it hasn't been refreshed recently."
            )
        )

    for slug, msg in _UNSUPPORTED_OPTION_WARNINGS.items():
        if opts.get(slug):
            warnings.append(msg)

    for field_name in ("difficulty", "terrain"):
        field_c = criteria[field_name]
        if field_c["enabled"]:
            q &= _range_q(field_name, field_c["cmp"], field_c["score"])

    if criteria["country_state"]["mode"] != "none":
        warnings.append(_("Country/State restriction can't be verified locally — result count may be off."))

    origin = criteria["origin"]
    geo_param = None
    if origin["mode"] == "coords" and origin["lat"] is not None:
        geo_param = f"circle:{origin['lat']},{origin['lon']},{origin['radius_km'] * 1000}"
    elif origin["mode"] in ("home", "gccode", "postal"):
        warnings.append(
            _("Origin '%(mode)s' can't be resolved locally — result count may be off.")
            % {"mode": origin["mode"]}
        )

    q &= ~Q(status=CacheStatus.ARCHIVED)

    return q, geo_param, warnings


# ---------------------------------------------------------------------------
# Write path: apply a proposed date range back into a PQ.
# ---------------------------------------------------------------------------

_GENESIS_DATE = date(2000, 1, 1)


def _open_end_sentinel() -> date:
    """Furthest end-date GC's Year select currently allows (no blank/open
    option exists on the real form — every Between PQ needs a concrete end
    date, and the dropdown only goes up to next year)."""
    return date(_gc_today_pst().year + 1, 12, 31)


def _build_save_payload(
    form: Tag, date_from: date | None, date_to: date | None, new_name: str | None = None,
) -> dict[str, str]:
    """Serialize the form's current state the way a real browser submit
    would — every text/hidden/select field, only checked checkboxes/the
    selected radio per group, no submit buttons — then override Placed and
    the six date selects (and ``tbName`` if ``new_name`` is given). Pure/
    offline; the rest of the form round-trips unchanged so no other
    criterion is touched.
    """
    data: dict[str, str] = {}
    for el in form.find_all(["input", "select", "textarea"]):
        name = el.get("name")
        if not name:
            continue
        el_type = (el.get("type") or "").lower() if el.name == "input" else ""
        if el.name == "input" and el_type in ("checkbox", "radio"):
            if el.has_attr("checked"):
                data[name] = el.get("value", "on")
        elif el.name == "input" and el_type == "submit":
            continue
        elif el.name == "select":
            opt = el.find("option", selected=True) or el.find("option")
            data[name] = opt.get("value", "") if opt else ""
        else:
            data[name] = el.get("value", "")

    resolved_from = date_from or _GENESIS_DATE
    resolved_to = date_to or _open_end_sentinel()
    data[_PREFIX + "Placed"] = "rbPlacedBetween"
    data[_PREFIX + "DateTimeBegin$Month"] = str(resolved_from.month)
    data[_PREFIX + "DateTimeBegin$Day"] = str(resolved_from.day)
    data[_PREFIX + "DateTimeBegin$Year"] = str(resolved_from.year)
    data[_PREFIX + "DateTimeEnd$Month"] = str(resolved_to.month)
    data[_PREFIX + "DateTimeEnd$Day"] = str(resolved_to.day)
    data[_PREFIX + "DateTimeEnd$Year"] = str(resolved_to.year)
    if new_name:
        data[_PREFIX + "tbName"] = new_name
    data[_PREFIX + "btnSubmit"] = "Submit Information"
    return data


def apply_pq_date_range(
    guid: str, date_from: date | None, date_to: date | None, new_name: str | None = None,
) -> tuple[date, date, str]:
    """Write a new Placed/Between range into a PQ, preserving every other
    field exactly as currently configured. Optionally renames it at the same
    time (``new_name`` — e.g. "Kusterdingen 3", the sequential-numbering
    convention preferred over trying to regenerate a date-encoded name,
    which isn't robust across locales/date formats).

    ``date_from``/``date_to`` of ``None`` mean an open bound — GC's form has
    no blank option, so it's resolved to a sentinel (2000-01-01 for an open
    start; the furthest future date the Year select currently allows for an
    open end — see ``_open_end_sentinel``).

    Returns the (resolved_from, resolved_to, resolved_name) actually
    written. Raises RuntimeError if the session is expired, the form can't
    be found, or GC's response doesn't reflect the intended dates/name after
    saving.
    """
    url = _PQ_EDIT_URL_TMPL.format(guid=guid)
    soup = _fetch_page(url)
    form = soup.find("form", id="aspnetForm")
    if not isinstance(form, Tag):
        raise RuntimeError("Could not find the Pocket Query edit form on the page.")

    payload = _build_save_payload(form, date_from, date_to, new_name)

    session = _pq_session()
    r = session.post(
        url, data=payload, timeout=30,
        headers={"Referer": url, "Origin": "https://www.geocaching.com"},
    )
    r.raise_for_status()

    if "account/signin" in r.url or "login" in r.url.lower():
        _pq_reset_session()
        raise RuntimeError("Web session expired while saving — session has been reset, please try again.")

    resolved_from = date_from or _GENESIS_DATE
    resolved_to = date_to or _open_end_sentinel()
    result = _parse_criteria_soup(BeautifulSoup(r.text, "html.parser"))
    if result["placed"]["date_from"] != resolved_from or result["placed"]["date_to"] != resolved_to:
        raise RuntimeError(
            f"Save may not have taken effect — geocaching.com now shows "
            f"{result['placed']['date_from']} to {result['placed']['date_to']}."
        )
    if new_name and result["name"] != new_name:
        raise RuntimeError(
            f"Rename may not have taken effect — geocaching.com now shows the name {result['name']!r}."
        )
    return resolved_from, resolved_to, result["name"]
