"""GC Instant Notifications — web automation.

The GC API has no notification endpoints, so we drive the website's ASP.NET
WebForms pages (``notify/default.aspx`` + ``notify/edit.aspx``) directly via
the authenticated session from ``geocaches.sync.gc_web.session``.

Field-name catalogue (derived from recon in scripts/recon_notify.py):

  * Name           ``ctl00$ContentBody$LogNotify$tbName``
  * Cache type     ``ctl00$ContentBody$LogNotify$ddTypeList`` (numeric id)
  * Coord format   ``ctl00$ContentBody$LogNotify$LatLong`` (we always send 1 = MinDec)
  * Lat deg / min  ``ctl00$ContentBody$LogNotify$LatLong$_inputLatDegs``
                   ``ctl00$ContentBody$LogNotify$LatLong$_inputLatMins``
  * Lon deg / min  ``ctl00$ContentBody$LogNotify$LatLong$_inputLongDegs``
                   ``ctl00$ContentBody$LogNotify$LatLong$_inputLongMins``
  * N/S, E/W       ``…$LatLong:_selectNorthSouth`` (1=N / -1=S)
                   ``…$LatLong:_selectEastWest`` (1=E / -1=W)
  * Distance (km)  ``ctl00$ContentBody$LogNotify$tbDistance``
  * Enabled        ``ctl00$ContentBody$LogNotify$cbEnable`` (omit if disabled)
  * Log events     ``ctl00$ContentBody$LogNotify$cblLogTypeList$<i>`` (omit if not subscribed)
  * Recipient      ``ctl00$ContentBody$LogNotify$ddlAltEmails`` (the email string)
  * Submit         ``ctl00$ContentBody$LogNotify$btnGo``        ("Create"/"Edit")
                   ``ctl00$ContentBody$LogNotify$btnArchive``   ("Delete")

For reading values back, decimal lat/lon are easiest sourced from the
``lnkMap`` link (``…/map/?ll=<lat>,<lon>``) which the page renders.

Relocated from ``gcprivate/notify_web.py`` (2026-08-13) — public build, needs
only the user's own GC password. See ``docs/reference/geocaching-com-web.md``.
"""
from __future__ import annotations

import logging
import math
import re
from typing import Iterable
from urllib.parse import parse_qs, urlparse

from bs4 import BeautifulSoup, Tag

from geocaches.sync.gc_web.session import get_session, reset_session
from geocaches.sync.notify_constants import CACHE_TYPES, LOG_EVENTS

logger = logging.getLogger("geocaches.notify")

LIST_URL = "https://www.geocaching.com/notify/default.aspx"
EDIT_URL = "https://www.geocaching.com/notify/edit.aspx"


class NotificationFormUnavailable(RuntimeError):
    """Raised when edit.aspx won't render the create form.

    Most commonly the account is at GC's 40-notification cap; the page then
    shows only a warning and omits the form, so a create can't proceed.
    """

# Catalogues (CACHE_TYPES / LOG_EVENTS / LOG_EVENT_NAMES / PUBLISH_EVENT_ID) live
# in the public geocaches/sync/notify_constants.py and are imported above, so the
# read-only notifications UI can share them without pulling in gcprivate.

_FIELD_PREFIX = "ctl00$ContentBody$LogNotify$"

# Names ASP.NET infrastructure fields we forward verbatim from a prior GET.
_ASPNET_HIDDEN_FIELDS = (
    "__VIEWSTATE",
    "__VIEWSTATEGENERATOR",
    "__EVENTTARGET",
    "__EVENTARGUMENT",
    "__LASTFOCUS",
    "__RequestVerificationToken",
)


# ---------------------------------------------------------------------------
# Low-level helpers
# ---------------------------------------------------------------------------

def _check_login_redirect(html: str, url: str) -> None:
    if "account/signin" in url or "<title>Sign In" in html:
        reset_session()
        raise RuntimeError(
            "Web session expired while loading a notification page — session "
            "has been reset, please try again."
        )


def _get(url: str) -> BeautifulSoup:
    session = get_session()
    r = session.get(url, timeout=30)
    r.raise_for_status()
    _check_login_redirect(r.text, r.url)
    return BeautifulSoup(r.text, "html.parser")


def _post(url: str, data: dict[str, str]) -> BeautifulSoup:
    session = get_session()
    r = session.post(url, data=data, timeout=30, headers={"Referer": url})
    r.raise_for_status()
    _check_login_redirect(r.text, r.url)
    return BeautifulSoup(r.text, "html.parser")


def _extract_aspnet_state(soup: BeautifulSoup) -> dict[str, str]:
    """Pull all ASP.NET infrastructure hidden inputs + the CSRF token.

    Forwarded verbatim in every subsequent POST.
    """
    state: dict[str, str] = {}
    form = soup.find("form")
    if not isinstance(form, Tag):
        return state
    for inp in form.find_all("input"):
        if not isinstance(inp, Tag):
            continue
        name = inp.get("name", "")
        if name in _ASPNET_HIDDEN_FIELDS or (
            name.startswith("__VIEWSTATE") and name[len("__VIEWSTATE"):].isdigit()
        ):
            state[name] = inp.get("value", "") or ""
    return state


def get_alt_emails() -> list[str]:
    """Return the alt-email options visible on the create form. Primary first."""
    soup = _get(EDIT_URL)
    out: list[str] = []
    for opt in soup.find_all("option"):
        if not isinstance(opt, Tag):
            continue
        # Parent <select> name ends in 'ddlAltEmails'
        parent = opt.find_parent("select")
        if not isinstance(parent, Tag):
            continue
        if "ddlAltEmails" not in (parent.get("name") or ""):
            continue
        v = opt.get("value", "")
        if v:
            out.append(v)
    return out


_primary_email_cache: str | None = None


def get_primary_email() -> str:
    """Return the user's primary GC email (cached for the session).

    Used to default the recipient on a new notification when the user
    didn't pick one explicitly — the server uses primary in that case, and
    storing the same value locally keeps the local DB in sync with the
    server state.
    """
    global _primary_email_cache
    if _primary_email_cache is None:
        emails = get_alt_emails()
        _primary_email_cache = emails[0] if emails else ""
    return _primary_email_cache


# ---------------------------------------------------------------------------
# List page
# ---------------------------------------------------------------------------

def _parse_list_row(tr: Tag) -> dict | None:
    """Parse one row of the notifications table."""
    # Edit link → NID
    edit_link = None
    for a in tr.find_all("a", href=True):
        if not isinstance(a, Tag):
            continue
        if "edit.aspx" in a.get("href", ""):
            edit_link = a
            break
    if edit_link is None:
        return None
    try:
        nid = parse_qs(urlparse(edit_link["href"]).query).get("NID", [""])[0]
    except Exception:
        return None
    if not nid:
        return None

    # Toggle link (?did=<nid>) → enabled bool, derived from the image alt text.
    enabled = True
    for a in tr.find_all("a", href=True):
        if not isinstance(a, Tag):
            continue
        if a.get("href", "").startswith("?did="):
            img = a.find("img")
            if isinstance(img, Tag):
                alt = (img.get("alt") or "").lower()
                enabled = alt == "checked"
            break

    # Type icon → numeric id from `/images/WptTypes/sm/<id>.gif`, or fall back
    # to icon basename (e.g. "earthcache").
    type_id = 0
    type_name = ""
    for img in tr.find_all("img"):
        if not isinstance(img, Tag):
            continue
        src = img.get("src", "")
        m = re.search(r"/WptTypes/sm/([^./]+)\.\w+$", src)
        if m:
            raw = m.group(1)
            type_name = (img.get("alt") or "").strip()
            if raw.isdigit():
                type_id = int(raw)
            else:
                # Map alt-text back to a type id from our catalogue.
                type_id = next(
                    (tid for tid, name in CACHE_TYPES.items() if name.lower() == type_name.lower()),
                    0,
                )
            break

    # Name + log type summary in the strong-tag cell.
    name = ""
    log_summary = ""
    for strong in tr.find_all(["strong", "b"]):
        if not isinstance(strong, Tag):
            continue
        name = strong.get_text(" ", strip=True)
        # Sibling text inside the same <td> is the "Log type(s): …" hint.
        cell = strong.find_parent("td")
        if isinstance(cell, Tag):
            cell_text = cell.get_text(" ", strip=True)
            after = cell_text[len(name):].strip()
            log_summary = re.sub(r"^Log type\(s\):\s*", "", after).strip()
        break

    return {
        "nid": nid,
        "name": name,
        "type_id": type_id,
        "type_name": type_name,
        "enabled": enabled,
        "log_event_summary": log_summary,
    }


def list_notifications() -> list[dict]:
    """Return all notifications visible on default.aspx."""
    soup = _get(LIST_URL)
    rows: list[dict] = []
    for tr in soup.find_all("tr"):
        if not isinstance(tr, Tag):
            continue
        row = _parse_list_row(tr)
        if row:
            rows.append(row)
    return rows


# ---------------------------------------------------------------------------
# Edit page — read
# ---------------------------------------------------------------------------

def _decimal_from_map_link(soup: BeautifulSoup) -> tuple[float, float] | None:
    """The `lnkMap` link carries `?ll=<lat>,<lon>` in decimal degrees."""
    a = soup.find("a", id="ctl00_ContentBody_LogNotify_lnkMap")
    if not isinstance(a, Tag):
        return None
    href = a.get("href", "")
    qs = parse_qs(urlparse(href).query)
    ll = qs.get("ll", [""])[0]
    if "," not in ll:
        return None
    try:
        lat_s, lon_s = ll.split(",", 1)
        return float(lat_s), float(lon_s)
    except ValueError:
        return None


def _selected_value(soup: BeautifulSoup, select_name: str) -> str:
    for sel in soup.find_all("select"):
        if not isinstance(sel, Tag):
            continue
        if sel.get("name", "") != select_name:
            continue
        for opt in sel.find_all("option"):
            if not isinstance(opt, Tag):
                continue
            if opt.has_attr("selected"):
                return opt.get("value", "")
        return ""
    return ""


def _input_value(soup: BeautifulSoup, input_name: str) -> str:
    el = soup.find("input", attrs={"name": input_name})
    if isinstance(el, Tag):
        return el.get("value", "") or ""
    return ""


def _checkbox_checked(soup: BeautifulSoup, input_name: str) -> bool:
    el = soup.find("input", attrs={"name": input_name, "type": "checkbox"})
    return isinstance(el, Tag) and el.has_attr("checked")


def _checked_log_event_ids(soup: BeautifulSoup) -> list[int]:
    """Return the event-ids of all checked `cblLogTypeList$N` checkboxes."""
    out: list[int] = []
    for cb in soup.find_all("input", {"type": "checkbox"}):
        if not isinstance(cb, Tag):
            continue
        name = cb.get("name", "")
        if "cblLogTypeList$" not in name:
            continue
        if not cb.has_attr("checked"):
            continue
        try:
            out.append(int(cb.get("value", "0")))
        except ValueError:
            continue
    return sorted(out)


def fetch_notification_detail(nid: str) -> dict:
    """Return the full server-side state of one notification.

    Coords come from the page's `lnkMap` href (decimal); everything else from
    the form fields.
    """
    soup = _get(f"{EDIT_URL}?NID={nid}")
    coords = _decimal_from_map_link(soup) or (0.0, 0.0)

    type_id_str = _selected_value(soup, _FIELD_PREFIX + "ddTypeList")
    try:
        type_id = int(type_id_str)
    except ValueError:
        type_id = 0

    distance_str = _input_value(soup, _FIELD_PREFIX + "tbDistance") or "0"
    try:
        radius_km = int(float(distance_str))
    except ValueError:
        radius_km = 0

    return {
        "nid": nid,
        "name": _input_value(soup, _FIELD_PREFIX + "tbName"),
        "latitude": coords[0],
        "longitude": coords[1],
        "radius_km": radius_km,
        "cache_type_id": type_id,
        "log_event_ids": _checked_log_event_ids(soup),
        "recipient_email": _selected_value(soup, _FIELD_PREFIX + "ddlAltEmails"),
        "enabled": _checkbox_checked(soup, _FIELD_PREFIX + "cbEnable"),
    }


# ---------------------------------------------------------------------------
# Edit page — write
# ---------------------------------------------------------------------------

def _decimal_to_mindec(dec: float) -> tuple[int, str, int]:
    """Decimal degrees -> (degs, mins-string, sign).

    sign = +1 for N/E, -1 for S/W.
    Minutes is formatted to 3 decimal places, matching the website's form.
    """
    sign = 1 if dec >= 0 else -1
    absd = abs(dec)
    degs = int(math.floor(absd))
    mins = (absd - degs) * 60.0
    return degs, f"{mins:.3f}", sign


def _build_form_data(
    *,
    state: dict[str, str],
    name: str,
    latitude: float,
    longitude: float,
    radius_km: int,
    cache_type_id: int,
    log_event_ids: Iterable[int],
    recipient_email: str,
    enabled: bool,
    submit_button: str = "btnGo",
    submit_label: str = "Edit Notification",
) -> dict[str, str]:
    """Build a complete POST body for create/edit using MinDec coords."""
    lat_degs, lat_mins, lat_sign = _decimal_to_mindec(latitude)
    lon_degs, lon_mins, lon_sign = _decimal_to_mindec(longitude)

    data: dict[str, str] = dict(state)
    data[_FIELD_PREFIX + "tbName"] = name
    data[_FIELD_PREFIX + "ddTypeList"] = str(cache_type_id)
    data[_FIELD_PREFIX + "LatLong"] = "1"  # MinDec
    data[_FIELD_PREFIX + "LatLong:_currentLatLongFormat"] = "1"
    data[_FIELD_PREFIX + "LatLong$_inputLatDegs"] = str(lat_degs)
    data[_FIELD_PREFIX + "LatLong$_inputLatMins"] = lat_mins
    data[_FIELD_PREFIX + "LatLong$_inputLongDegs"] = f"{lon_degs:03d}"
    data[_FIELD_PREFIX + "LatLong$_inputLongMins"] = lon_mins
    data[_FIELD_PREFIX + "LatLong:_selectNorthSouth"] = "1" if lat_sign >= 0 else "-1"
    data[_FIELD_PREFIX + "LatLong:_selectEastWest"] = "1" if lon_sign >= 0 else "-1"
    data[_FIELD_PREFIX + "tbPostalCode"] = ""
    data[_FIELD_PREFIX + "tbDistance"] = str(radius_km)
    if recipient_email:
        data[_FIELD_PREFIX + "ddlAltEmails"] = recipient_email
    if enabled:
        # cbEnable's posted value when checked is "on" (any non-empty string).
        data[_FIELD_PREFIX + "cbEnable"] = "on"

    # Log-event checkboxes: include only the checked ones, at their canonical
    # cblLogTypeList$<index> field-name slot.
    checked = set(log_event_ids)
    for idx, (event_id, _label) in enumerate(LOG_EVENTS):
        if event_id in checked:
            data[f"{_FIELD_PREFIX}cblLogTypeList${idx}"] = str(event_id)

    data[_FIELD_PREFIX + submit_button] = submit_label
    return data


def _read_aspnet_state_from(url: str) -> tuple[BeautifulSoup, dict[str, str]]:
    soup = _get(url)
    return soup, _extract_aspnet_state(soup)


def _create_form_unavailable_reason(soup: BeautifulSoup) -> str | None:
    """Return a human reason if the create form isn't present, else None.

    When the account is at GC's 40-notification cap (or otherwise can't add
    one), edit.aspx renders only a ``.Warning`` paragraph and omits the entire
    form — no ``ddTypeList`` select, no ``tbName``, no ``btnGo``.  Posting our
    forged field names against that page makes GC's handler 500.  Detect the
    missing form up front so callers can fail with a clear message instead.
    """
    if soup.find("select", attrs={"name": _FIELD_PREFIX + "ddTypeList"}) is not None:
        return None  # form is present — all good
    warn = soup.find(class_="Warning")
    text = warn.get_text(" ", strip=True) if isinstance(warn, Tag) else ""
    if "maximum number" in text.lower():
        return (f"Geocaching.com notification limit reached: {text} "
                "Delete some notifications before creating new ones.")
    if text:
        return f"Geocaching.com would not show the notification create form: {text}"
    return ("Geocaching.com did not render the notification create form "
            "(the page may have changed, or the account lacks access).")


def _select_type_postback(state: dict[str, str], cache_type_id: int) -> dict[str, str]:
    """Trigger the ``ddTypeList`` AutoPostBack so the server renders
    ``cblLogTypeList`` items.

    The blank create form's ViewState has ZERO items in the log-type
    CheckBoxList (it only renders after a cache type is picked).  Until we
    trigger the postback, any ``cblLogTypeList$<i>`` keys we POST are silently
    dropped — and the final submit fails with "You need to choose at least one
    log option."  This step does the postback the browser would do on the
    dropdown's ``onchange``.
    """
    data: dict[str, str] = dict(state)
    data["__EVENTTARGET"] = _FIELD_PREFIX + "ddTypeList"
    data["__EVENTARGUMENT"] = ""
    data[_FIELD_PREFIX + "ddTypeList"] = str(cache_type_id)
    # Minimal fields the postback handler reads.
    data[_FIELD_PREFIX + "LatLong"] = "1"
    data[_FIELD_PREFIX + "LatLong:_currentLatLongFormat"] = "1"
    new_soup = _post(EDIT_URL, data)
    return _extract_aspnet_state(new_soup)


def create_notification(
    *,
    name: str,
    latitude: float,
    longitude: float,
    radius_km: int,
    cache_type_id: int,
    log_event_ids: Iterable[int],
    recipient_email: str,
    enabled: bool,
) -> str:
    """POST a create.  Two POSTs total: a select-type postback so the
    server's CheckBoxList learns its items, then the actual create.
    Returns the new NID by re-fetching the list and finding the one row that
    wasn't there before.
    """
    pre_existing = {r["nid"] for r in list_notifications()}

    soup, state = _read_aspnet_state_from(EDIT_URL)
    reason = _create_form_unavailable_reason(soup)
    if reason:
        raise NotificationFormUnavailable(reason)
    state = _select_type_postback(state, cache_type_id)

    data = _build_form_data(
        state=state,
        name=name,
        latitude=latitude,
        longitude=longitude,
        radius_km=radius_km,
        cache_type_id=cache_type_id,
        log_event_ids=log_event_ids,
        recipient_email=recipient_email,
        enabled=enabled,
        submit_label="Create Notification",
    )
    logger.debug("Notify create POST: name=%r type=%s coords=%s,%s radius=%skm",
                 name, cache_type_id, latitude, longitude, radius_km)
    _post(EDIT_URL, data)

    after = list_notifications()
    new = [r for r in after if r["nid"] not in pre_existing]
    if not new:
        raise RuntimeError(f"Notification create POST returned, but no new row "
                           f"appeared on the list page (name={name!r}).")
    return new[0]["nid"]


def update_notification(
    nid: str,
    *,
    name: str,
    latitude: float,
    longitude: float,
    radius_km: int,
    cache_type_id: int,
    log_event_ids: Iterable[int],
    recipient_email: str,
    enabled: bool,
) -> None:
    url = f"{EDIT_URL}?NID={nid}"
    _, state = _read_aspnet_state_from(url)
    data = _build_form_data(
        state=state,
        name=name,
        latitude=latitude,
        longitude=longitude,
        radius_km=radius_km,
        cache_type_id=cache_type_id,
        log_event_ids=log_event_ids,
        recipient_email=recipient_email,
        enabled=enabled,
        submit_label="Edit Notification",
    )
    logger.debug("Notify update POST: NID=%s name=%r", nid, name)
    _post(url, data)


def delete_notification(nid: str) -> None:
    """POST the edit form with `btnArchive` instead of `btnGo`."""
    url = f"{EDIT_URL}?NID={nid}"
    soup, state = _read_aspnet_state_from(url)
    # Preserve the existing field values so the POST validates server-side.
    data: dict[str, str] = dict(state)
    for inp in soup.find_all("input"):
        if not isinstance(inp, Tag):
            continue
        name = inp.get("name", "")
        if not name.startswith(_FIELD_PREFIX):
            continue
        if inp.get("type") in ("submit",):
            continue
        if inp.get("type") == "checkbox" and not inp.has_attr("checked"):
            continue
        val = inp.get("value", "") or ("on" if inp.get("type") == "checkbox" else "")
        data[name] = val
    for sel in soup.find_all("select"):
        if not isinstance(sel, Tag):
            continue
        name = sel.get("name", "")
        if not name.startswith(_FIELD_PREFIX):
            continue
        sv = ""
        for opt in sel.find_all("option"):
            if isinstance(opt, Tag) and opt.has_attr("selected"):
                sv = opt.get("value", "")
                break
        data[name] = sv
    data[_FIELD_PREFIX + "btnArchive"] = "Delete Notification"
    logger.debug("Notify delete POST: NID=%s", nid)
    _post(url, data)


def toggle_notification(nid: str) -> None:
    """Flip the enabled state via the default.aspx `?did=<NID>` link."""
    session = get_session()
    url = f"{LIST_URL}?did={nid}"
    r = session.get(url, timeout=30, headers={"Referer": LIST_URL})
    r.raise_for_status()
    _check_login_redirect(r.text, r.url)
    logger.debug("Notify toggle POST: NID=%s", nid)
