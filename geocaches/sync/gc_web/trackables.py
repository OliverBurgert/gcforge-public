"""
GC trackable management via the website — no partner-API token needed.

**Discovered and live-verified 2026-08-16** (Oliver's real account, TB91ENV
— his own "2018 Community Volunteer Tag" — used for three real create+
delete round trips, all cleaned up afterward; account left in its original
state throughout).

Standalone trackable logging
-----------------------------
Logging a trackable action *not* tied to a geocache-log submission
(discover it lying around, retrieve/grab it, write a note, ...) — as
opposed to the "Visit while logging a cache" case, which already rides
inside ``log_client.create_log()``'s own ``trackables`` list — goes
through the *same* tRPC procedure family as geocache logs::

    POST /api/live/v1/trpc/web.logs.createTrackableLog?batch=1
    POST /api/live/v1/trpc/web.logs.deleteTrackableLog?batch=1

**This resolves ``docs/reference/geocaching-com-web.md``'s long-standing
open question** ("does GC's UI ever route a trackable action through a
genuinely separate ``createTrackableLog`` procedure, or always the in-log
mechanism?") — confirmed live via the real "Log this trackable" page
(``/live/trackable/{code}/log``)'s own network traffic: yes, a real,
separate procedure, not a fluke of the earlier bundle-scan catalog.
Request/response shapes are near-identical to
``createGeocacheLog``/``deleteGeocacheLog`` (same nested ``body`` object,
same field names), plus two trackable-specific fields: ``trackingCode``
(the private code, needed to log someone else's item) and
``geocacheReferenceCode`` (when the action ties to a specific cache).
Confirmed request body (real capture, via an instrumented ``fetch``
intercepting the actual UI submission)::

    {"referenceCode": "TB91ENV", "body": {
        "images": [], "logDate": "2026-08-16T12:00:00.000Z",
        "logText": "...", "logType": 4, "trackables": [],
        "trackingCode": "", "geocacheReferenceCode": ""}}

Confirmed response::

    {"logReferenceCode": "TL27723PN", "trackableReferenceCode": "TB91ENV",
     "dateTimeCreatedUtc": "...", "logDate": "...", "logType": {"id": 4},
     "logText": "...", "images": [], "cannotDelete": false,
     "isArchived": false, "statusCode": 200}

Unlike ``/live/geocache/{code}/log``, the trackable log-entry page does
**not** embed a CSRF token (confirmed by an exhaustive walk of its
``__NEXT_DATA__`` tree and its page-specific JS bundle — genuinely absent,
not just missed). CSRF tokens are session-scoped rather than tied to the
specific page/resource, though — confirmed live by successfully reusing a
token sourced from an unrelated cache's log page (``log_client``'s own
``_LOG_PAGE_URL``) for a real ``createTrackableLog`` call — so
``_csrf_source_url()`` below borrows from there (when a ``geocache_code``
is available) or from the trackable's own most recent log's view page
(``_LOG_VIEW_PAGE_URL`` — also confirmed to embed one) rather than
inventing a new source.

Reading
-------
Three more mechanisms, all classic (non-Next.js) pages, all read-only:

- **Verify / resolve**: ``/track/details.aspx?tracker={code}`` accepts
  *either* a public reference code (``TBxxxxxx``) or a private tracking
  code (confirmed live with both, including one only the owner/holder can
  see) and renders the same detail page either way — "The Travel Bug you
  requested does not exist in the system." (page text, not an HTTP error)
  signals an unknown code.
- **Full detail**: ``/live/trackable/{ref_code}/log`` (the *logging* page,
  still the right source for read-only detail too) embeds the trackable's
  full metadata in ``__NEXT_DATA__.props.pageProps.loggable`` — name,
  description, dateReleased, distance traveled, numeric ``trackableType``,
  owner, current holder, active/missing/locked state. Richer than the
  official API's own ``get_trackable()`` in some respects (icon URL,
  holder object with avatar) but doesn't expose ``trackableType`` as a
  name (bare numeric id, no lookup table found) or a current-geocache-code
  when the item happens to be sitting in a cache (not independently
  confirmed — this account's one held TB was in a person's hands, not a
  cache, throughout testing).
- **Inventory / collection**: ``/my/inventory.aspx`` / ``/my/collection.aspx``
  — same classic table markup, confirmed live against Oliver's real
  1-item inventory and 7-item collection: name, owner, last-log date,
  distance traveled, and the item's routing ``guid`` (*not* its public
  reference code — resolve separately via ``resolve_trackable_web(guid=...)``
  if the caller needs one).
- **Log history**: the same classic detail page's "Tracking History"
  table, paginated via ``?page=N`` (10 entries/page, confirmed against a
  real 2,491-entry history). Each entry is *two* table rows: a summary row
  (date, human-readable action sentence, cache name/location, distance, a
  link to the log's own ``/live/log/{TL-ref}`` page) directly followed by
  a full-text row. The summary sentence is enough for a human-readable
  history but isn't reliably mapped to a precise ``TrackableLogType`` here
  (GC's sentence phrasing wasn't exhaustively captured for every log
  type, and guessing risks silent misclassification) — so
  ``get_trackable_logs_web()`` returns the raw text/TL-ref only, and
  ``fetch_trackable_log_detail_web()`` gives the precise numeric
  ``logType.id`` per entry (confirmed live: id 75 = "Visited", matching
  ``trackable_constants.DEFAULT_TRACKABLE_LOG_TYPE_IDS`` exactly) for
  callers that need it, at the cost of one request per log.

Owned-but-not-held trackables (the official API's ``get_owned_trackables()``,
``type=owned``) and trackable images (``get_trackable_images()``) have no
confirmed web equivalent yet — no page was found for the former in this
pass (tried a couple of plausible URLs, all 404), and images weren't
investigated. Left for a future pass rather than guessed.

See ``docs/reference/geocaching-com-web.md`` and
``geocaches.services.trackable_sync``'s resolver (planned).
"""
from __future__ import annotations

import json
import logging
import re
from datetime import date

import requests
from bs4 import BeautifulSoup

from geocaches.sync.gc_web import trpc
from geocaches.sync.gc_web.log_client import _LOG_PAGE_URL, _LOG_VIEW_PAGE_URL, _format_log_date
from geocaches.sync.gc_web.session import get_session, reset_session
from geocaches.sync.rate_limiter import polite_delay
from geocaches.sync.trackable_constants import DEFAULT_TRACKABLE_LOG_TYPE_IDS

logger = logging.getLogger(__name__)

_DETAILS_URL = "https://www.geocaching.com/track/details.aspx"
_TB_LOG_PAGE_URL = "https://www.geocaching.com/live/trackable/{code}/log"
_INVENTORY_URL = "https://www.geocaching.com/my/inventory.aspx"
_COLLECTION_URL = "https://www.geocaching.com/my/collection.aspx"

_TITLE_CODE_RE = re.compile(r"^\(([A-Z]{2}[A-Z0-9]+)\)\s*(.*)$")
_NOT_FOUND_TEXT = "does not exist in the system"
_GUID_RE = re.compile(r"guid=([0-9a-f-]{36})", re.IGNORECASE)
_LOG_LINK_RE = re.compile(r"/live/log/([A-Z0-9]+)")


def _clean_text(text: str) -> str:
    """Collapse embedded newlines/runs of whitespace left by ``get_text()``
    on multi-line table cells (e.g. "Germany\\r\\n   - 195.02 meters")."""
    return " ".join(text.split())


def _check_session(resp, what: str) -> None:
    if "account/signin" in resp.url:
        reset_session()
        raise RuntimeError(f"Web session expired while {what}")


def _parse_details_title(html: str) -> dict | None:
    soup = BeautifulSoup(html, "html.parser")
    title = (soup.title.string or "").strip() if soup.title else ""
    m = _TITLE_CODE_RE.match(title)
    if not m:
        return None
    return {"reference_code": m.group(1), "name": m.group(2)}


def resolve_trackable_web(
    code: str | None = None, *, guid: str | None = None, internal_id: str | None = None, session=None,
) -> dict | None:
    """Resolve a trackable to its identity via the classic details page.

    Pass ``code`` (public ``TBxxxxxx`` reference code OR a private tracking
    code — confirmed live to work identically for both), ``guid`` (the
    routing id ``list_inventory_web()``/``list_collection_web()`` return),
    or ``internal_id`` (the numeric id ``list_owned_web()`` returns —
    confirmed live to resolve the same page). Returns
    ``{"reference_code", "name"}``, or None if not found.
    """
    if not code and not guid and not internal_id:
        raise ValueError("resolve_trackable_web() needs code=, guid=, or internal_id=")
    session = session or get_session()
    if code:
        params = {"tracker": code}
    elif guid:
        params = {"guid": guid}
    else:
        params = {"id": internal_id}
    resp = session.get(_DETAILS_URL, params=params, timeout=20)
    resp.raise_for_status()
    _check_session(resp, f"resolving trackable {code or guid or internal_id}")
    if _NOT_FOUND_TEXT in resp.text:
        return None
    return _parse_details_title(resp.text)


def get_trackable_web(ref_code: str, *, session=None) -> dict:
    """Full trackable detail via its "Log this trackable" page's embedded
    ``__NEXT_DATA__`` (``pageProps.loggable``) — see module docstring.
    """
    session = session or get_session()
    resp = session.get(_TB_LOG_PAGE_URL.format(code=ref_code), timeout=20)
    resp.raise_for_status()
    _check_session(resp, f"loading {ref_code} detail")
    if "error/404" in resp.url:
        raise RuntimeError(f"Trackable {ref_code} not found.")

    soup = BeautifulSoup(resp.text, "html.parser")
    script = soup.find("script", id="__NEXT_DATA__")
    if not script or not script.string:
        raise RuntimeError(f"Could not find trackable detail for {ref_code} — site may have changed.")
    data = json.loads(script.string)
    loggable = data.get("props", {}).get("pageProps", {}).get("loggable")
    if not loggable:
        raise RuntimeError(f"Trackable {ref_code} not found.")
    return loggable


def get_trackable_classic_detail_web(ref_code: str, *, session=None) -> dict:
    """Fields the Next.js "log" page's ``loggable`` object doesn't carry —
    scraped from the classic detail page's ``dl.BugDetailsList`` and the
    "Current Goal"/"About This Item" sections. **Confirmed live 2026-08-16**
    against three real trackables covering all three ownership/holder
    combinations:

    - ``tracking_code`` is visible **only when the account owns the
      item**, regardless of who currently holds it (confirmed: shown for
      an owned-but-held-by-someone-else item, absent for a not-owned
      item) — matches the official API's own ``trackingNumber`` field
      exactly, cross-checked live.
    - ``origin``/``goal``/``about`` are always visible regardless of
      ownership.

    Returns ``{origin, goal, about, tracking_code}`` (empty strings for
    anything not found/not visible).
    """
    session = session or get_session()
    resp = session.get(_DETAILS_URL, params={"tracker": ref_code}, timeout=20)
    resp.raise_for_status()
    _check_session(resp, f"loading {ref_code} classic detail")
    if _NOT_FOUND_TEXT in resp.text:
        raise RuntimeError(f"Trackable {ref_code} not found.")

    soup = BeautifulSoup(resp.text, "html.parser")
    origin = ""
    dl = soup.find("dl", class_="BugDetailsList")
    if dl:
        dts = dl.find_all("dt")
        dds = dl.find_all("dd")
        for dt, dd in zip(dts, dds, strict=False):
            if dt.get_text(strip=True).rstrip(":") == "Origin":
                origin = _clean_text(dd.get_text(" ", strip=True))
                break

    def _section_text(heading: str) -> str:
        header = soup.find(string=re.compile(re.escape(heading)))
        if not header or not header.parent:
            return ""
        sib = header.parent.find_next_sibling()
        return _clean_text(sib.get_text(" ", strip=True)) if sib else ""

    goal = _section_text("Current Goal")
    about = _section_text("About This Item")

    tracking_code = ""
    tn_label = soup.find(string=re.compile(r"Tracking Number"))
    if tn_label and tn_label.parent and tn_label.parent.parent:
        m = re.search(r"Tracking Number:\s*(\S+)", tn_label.parent.parent.get_text(" ", strip=True))
        if m:
            tracking_code = m.group(1)

    return {"origin": origin, "goal": goal, "about": about, "tracking_code": tracking_code}


_GALLERY_ID_RE = re.compile(r"gallery\.aspx\?ID=(\d+)", re.IGNORECASE)
_GALLERY_URL = "https://www.geocaching.com/track/gallery.aspx"
_IMAGE_GUID_RE = re.compile(r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})", re.IGNORECASE)


def list_trackable_images_web(ref_code: str, *, session=None) -> list[dict]:
    """Trackable listing/log gallery images — ``/track/gallery.aspx?ID={n}``,
    linked from the classic detail page as "View All N Gallery Images".

    **Found and confirmed live 2026-08-16** (Oliver's TBZH18, 4 real
    images). Each ``<td>`` in ``table.GalleryTable`` carries a date stamp,
    a thumbnail linking to the full-size image, and a description. Images
    attached to a specific log use ``/track/log/{thumb,large}/`` URLs;
    images attached directly to the trackable listing use
    ``/track/{thumb,large}/`` (no ``log/`` segment) — ``is_log_image``
    reflects that distinction, matching ``TrackableImage.log``'s nullable FK.

    Returns ``[]`` if the trackable has no gallery link (no images) rather
    than raising — a real, common case, not an error.
    """
    session = session or get_session()
    resp = session.get(_DETAILS_URL, params={"tracker": ref_code}, timeout=20)
    resp.raise_for_status()
    _check_session(resp, f"loading {ref_code} for gallery lookup")
    if _NOT_FOUND_TEXT in resp.text:
        raise RuntimeError(f"Trackable {ref_code} not found.")

    m = _GALLERY_ID_RE.search(resp.text)
    if not m:
        return []

    polite_delay()
    resp2 = session.get(_GALLERY_URL, params={"ID": m.group(1)}, timeout=20)
    resp2.raise_for_status()
    _check_session(resp2, f"loading {ref_code} gallery")

    soup = BeautifulSoup(resp2.text, "html.parser")
    results = []
    for td in soup.select("table.GalleryTable td"):
        link = td.find("a", class_="imageLink")
        if not link:
            continue
        img_tag = link.find("img")
        thumb_url = img_tag.get("src", "") if img_tag else ""
        large_url = link.get("href", "")
        date_span = td.find("span", class_="date-stamp")
        desc_span = td.find("span", class_="image-description")
        guid_match = _IMAGE_GUID_RE.search(large_url or thumb_url)
        results.append({
            "guid": guid_match.group(1) if guid_match else "",
            "thumbnail_url": thumb_url,
            "large_url": large_url,
            "date_text": _clean_text(date_span.get_text(strip=True)) if date_span else "",
            "description": _clean_text(desc_span.get_text(" ", strip=True)) if desc_span else "",
            "is_log_image": "/track/log/" in thumb_url,
        })
    return results


def _parse_holder_list(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    results = []
    for a in soup.select("table a[href*='/track/details.aspx']"):
        m = _GUID_RE.search(a.get("href", ""))
        if not m:
            continue
        tr = a.find_parent("tr")
        if not tr:
            continue
        tds = tr.find_all("td")
        if len(tds) < 4:
            continue
        results.append({
            "guid": m.group(1),
            "name": a.get_text(strip=True),
            "owner": tds[1].get_text(strip=True),
            "last_log_text": tds[2].get_text(strip=True),
            "distance_text": tds[3].get_text(strip=True),
        })
    return results


def list_inventory_web(*, session=None) -> list[dict]:
    """TBs currently held by the authenticated user — ``/my/inventory.aspx``."""
    session = session or get_session()
    resp = session.get(_INVENTORY_URL, timeout=30)
    resp.raise_for_status()
    _check_session(resp, "loading inventory")
    return _parse_holder_list(resp.text)


def list_collection_web(*, session=None) -> list[dict]:
    """TBs in the authenticated user's collection — ``/my/collection.aspx``."""
    session = session or get_session()
    resp = session.get(_COLLECTION_URL, timeout=30)
    resp.raise_for_status()
    _check_session(resp, "loading collection")
    return _parse_holder_list(resp.text)


_SEARCH_URL = "https://www.geocaching.com/track/search.aspx"
_INTERNAL_ID_RE = re.compile(r"details\.aspx\?id=(\d+)")


def list_owned_web(*, uid: str | None = None, session=None) -> list[dict]:
    """Every trackable the authenticated user owns, regardless of who
    currently holds it — web equivalent of the official API's
    ``get_owned_trackables()`` (``type=owned``).

    **Found by Oliver, confirmed live 2026-08-17**:
    ``/track/search.aspx?o=1&uid={their own publicGuid}`` lists exactly
    this set — confirmed against his real account, all 12 owned
    trackables on one page (no pagination needed at that size;
    pagination beyond one page is unconfirmed).

    ``uid`` defaults to the ``gc_public_guid`` preference — already
    populated by ``preferences.views.accounts.fetch_gc_public_guid()`` (an
    existing classic-page scrape unrelated to this effort, or set manually
    in Settings > Accounts) — raises if neither is available.

    Each row has an ``internal_id`` (the numeric id
    ``/track/details.aspx?id=`` accepts — a third identifier alongside
    guid and reference code, confirmed to resolve the same page) rather
    than a reference code directly — this listing page doesn't expose
    one; resolve via ``resolve_trackable_web(internal_id=...)`` if needed.
    The "location" field is a truncated display hint only (the page
    itself clips it with an ellipsis) — use ``get_trackable_web()`` per
    item for the real, precise holder/current-cache data.
    """
    session = session or get_session()
    if not uid:
        from preferences.models import UserPreference
        uid = UserPreference.get("gc_public_guid", "")
    if not uid:
        raise RuntimeError(
            "No GC public GUID configured — set it in Settings > Accounts "
            "(the \"fetch\" button) before listing owned trackables."
        )

    resp = session.get(_SEARCH_URL, params={"o": "1", "uid": uid}, timeout=30)
    resp.raise_for_status()
    _check_session(resp, "loading owned-trackables search")

    soup = BeautifulSoup(resp.text, "html.parser")
    table = next((t for t in soup.find_all("table") if t.find("a", href=_INTERNAL_ID_RE)), None)
    if not table:
        return []

    results = []
    for tr in table.find_all("tr"):
        link = tr.find("a", href=_INTERNAL_ID_RE)
        if not link:
            continue
        m = _INTERNAL_ID_RE.search(link["href"])
        tds = tr.find_all("td")
        if len(tds) < 6:
            continue
        results.append({
            "internal_id": m.group(1),
            "name": _clean_text(link.get_text(strip=True)),
            "last_log_date_text": _clean_text(tds[2].get_text(strip=True)),
            "owner": _clean_text(tds[3].get_text(strip=True)),
            "location_hint": _clean_text(tds[4].get_text(strip=True)),
            "distance_traveled_text": _clean_text(tds[5].get_text(strip=True)),
        })
    return results


def get_trackable_logs_web(ref_code: str, *, page: int = 1, session=None) -> list[dict]:
    """One page (10 entries) of a trackable's tracking history, scraped
    from its classic detail page. See module docstring for the two-row-
    per-entry table layout and the log_type caveat.
    """
    session = session or get_session()
    params = {"tracker": ref_code}
    if page > 1:
        params["page"] = page
    resp = session.get(_DETAILS_URL, params=params, timeout=20)
    resp.raise_for_status()
    _check_session(resp, f"loading {ref_code} log history")
    if _NOT_FOUND_TEXT in resp.text:
        raise RuntimeError(f"Trackable {ref_code} not found.")

    soup = BeautifulSoup(resp.text, "html.parser")
    table = next((t for t in soup.find_all("table") if t.find("a", href=_LOG_LINK_RE)), None)
    if not table:
        return []

    rows = [tr for tr in table.find_all("tr") if "Data" in (tr.get("class") or [])]
    results = []
    for i in range(0, len(rows) - 1, 2):
        summary, note = rows[i], rows[i + 1]

        link = summary.find("a", href=_LOG_LINK_RE)
        m = _LOG_LINK_RE.search(link["href"]) if link else None
        log_ref = m.group(1) if m else ""

        cells = summary.find_all(["td", "th"])
        results.append({
            "log_reference_code": log_ref,
            "date_text": _clean_text(cells[0].get_text(strip=True)) if cells else "",
            "action_text": _clean_text(cells[1].get_text(" ", strip=True)) if len(cells) > 1 else "",
            "location_text": _clean_text(cells[2].get_text(" ", strip=True)) if len(cells) > 2 else "",
            "note_text": _clean_text(note.get_text(" ", strip=True)),
        })
    return results


def fetch_trackable_log_detail_web(log_ref: str, *, session=None) -> dict:
    """Precise per-log detail (numeric ``logType.id``, full text, geocache
    info) via the log's own view page — see module docstring's log-history
    caveat for why this is a separate, opt-in call.
    """
    session = session or get_session()
    polite_delay()
    resp = session.get(_LOG_VIEW_PAGE_URL.format(log_ref=log_ref), timeout=20)
    resp.raise_for_status()
    _check_session(resp, f"loading log {log_ref} detail")

    soup = BeautifulSoup(resp.text, "html.parser")
    script = soup.find("script", id="__NEXT_DATA__")
    if not script or not script.string:
        raise RuntimeError(f"Could not find log detail for {log_ref} — site may have changed.")
    pp = json.loads(script.string).get("props", {}).get("pageProps", {})
    return {
        "log_reference_code": pp.get("logReferenceCode", log_ref),
        "trackable_reference_code": pp.get("trackableReferenceCode", ""),
        "date_created_utc": pp.get("dateTimeCreatedUtc", ""),
        "log_date": pp.get("logDate", ""),
        "log_type_id": (pp.get("logType") or {}).get("id"),
        "log_text": pp.get("logText", ""),
        "username": pp.get("username", ""),
        "geocache": pp.get("geocache"),
        "is_archived": pp.get("isArchived", False),
    }


def _csrf_source_url(geocache_code: str | None, ref_code: str, *, session: requests.Session) -> str:
    """Pick a ``/live/*`` page known to embed a CSRF token for this session.

    Prefers the given geocache's log page (matches ``log_client``'s own
    convention) when available; otherwise borrows the trackable's own most
    recent log's view page. Raises if the trackable has no log history to
    borrow from and no ``geocache_code`` was given.
    """
    if geocache_code:
        return _LOG_PAGE_URL.format(code=geocache_code)
    for entry in get_trackable_logs_web(ref_code, session=session):
        if entry.get("log_reference_code"):
            return _LOG_VIEW_PAGE_URL.format(log_ref=entry["log_reference_code"])
    raise RuntimeError(
        f"No CSRF token source available for {ref_code}: pass geocache_code, "
        "or the trackable needs at least one prior log to borrow one from."
    )


def submit_trackable_log_web(
    trackable_ref: str,
    log_type: str,
    logged_at_iso: str,
    text: str,
    *,
    geocache_code: str | None = None,
    tracking_code: str | None = None,
    session: requests.Session | None = None,
) -> dict:
    """Submit a standalone trackable log via the website's tRPC API.

    Same call shape as
    ``gcprivate.trackable_client.TrackableClient.submit_trackable_log()``
    (GCForge ``TrackableLogType`` string in) so a resolver can pick either
    backend interchangeably. Only the *date* portion of ``logged_at_iso``
    survives — same "noon UTC regardless of time-of-day" convention as
    ``log_client.submit_log()`` (this form has no time-of-day field either).
    """
    session = session or get_session()
    type_id = DEFAULT_TRACKABLE_LOG_TYPE_IDS.get(log_type)
    if type_id is None:
        raise ValueError(f"Unknown trackable log type: {log_type!r}")

    csrf_token = trpc.fetch_csrf_token(session, _csrf_source_url(geocache_code, trackable_ref, session=session))
    log_date = date.fromisoformat(logged_at_iso[:10])

    input_data = {
        "referenceCode": trackable_ref,
        "body": {
            "images": [],
            "logDate": _format_log_date(log_date),
            "logText": text or "",
            "logType": type_id,
            "trackables": [],
            "trackingCode": tracking_code or "",
            "geocacheReferenceCode": geocache_code or "",
        },
    }
    logger.info("Web trackable log create: ref=%s type=%s", trackable_ref, log_type)
    return trpc.mutate(session, "web.logs.createTrackableLog", input_data, csrf_token=csrf_token)


def delete_trackable_log_web(log_ref: str, *, reason_text: str = "", session: requests.Session | None = None) -> None:
    """Delete a trackable log via the website's tRPC API — same flat-body
    shape (no nested ``body``) as ``log_client.delete_log()``.
    """
    session = session or get_session()
    csrf_token = trpc.fetch_csrf_token(session, _LOG_VIEW_PAGE_URL.format(log_ref=log_ref))
    input_data = {"referenceCode": log_ref, "reasonText": reason_text}
    logger.info("Web trackable log delete: ref=%s", log_ref)
    trpc.mutate(session, "web.logs.deleteTrackableLog", input_data, csrf_token=csrf_token)


class GCWebTrackableClient:
    """Thin adapter matching the two methods of
    ``gcprivate.trackable_client.TrackableClient`` that ``log_submit.py``'s
    interactive TB-action chain and ``views/trackables.py``'s tracking-code
    verify endpoint actually call — not a full client, just enough surface
    for ``geocaches.sync.gc_access.get_trackable_client()`` to swap in.
    """

    def verify_tracking_code(self, code: str) -> dict:
        """Match ``TrackableClient.verify_tracking_code()``'s return shape
        (``reference_code``, ``name``, ``current_geocache_code``, ``holder``).

        ``current_geocache_code`` is always empty here — not exposed by
        ``get_trackable_web()``'s data source (see module docstring).
        """
        resolved = resolve_trackable_web(code)
        if resolved is None:
            raise ValueError(f"Tracking code not recognised: {code}")
        detail = get_trackable_web(resolved["reference_code"])

        holder = None
        holder_raw = detail.get("holder") or {}
        if holder_raw.get("userName"):
            pr_code = holder_raw.get("code") or ""
            holder = {
                "username": holder_raw.get("userName", ""),
                "reference_code": pr_code,
                "profile_url": f"https://coord.info/{pr_code}" if pr_code else "",
                "message_url": (
                    f"https://www.geocaching.com/account/messagecenter/?recipientId={pr_code}"
                    if pr_code else ""
                ),
            }
        return {
            "reference_code": detail.get("referenceCode", resolved["reference_code"]),
            "name": detail.get("name", resolved["name"]),
            "current_geocache_code": "",
            "holder": holder,
        }

    def submit_trackable_log(
        self,
        trackable_ref: str,
        log_type: str,
        logged_at_iso: str,
        text: str,
        *,
        geocache_code: str | None = None,
        tracking_code: str | None = None,
    ) -> dict:
        """Match ``TrackableClient.submit_trackable_log()``'s call shape and
        its ``{"referenceCode": ...}``-shaped return (aliased from the web
        response's ``logReferenceCode`` — same convention as
        ``log_client.submit_log()``).
        """
        resp = submit_trackable_log_web(
            trackable_ref, log_type, logged_at_iso, text,
            geocache_code=geocache_code, tracking_code=tracking_code,
        )
        return {"referenceCode": resp.get("logReferenceCode", "")}
