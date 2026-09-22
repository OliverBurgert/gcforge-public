"""
GC log-draft ("Field Notes") download via the website — no partner-API
token needed.

**Discovered and live-verified 2026-08-16** (Oliver's own account, via a
real upload -> list -> detail -> delete round trip, twice — once driven
through the browser's own "Upload drafts" button, once again through plain
Python ``requests`` to confirm the exact wire shape independent of the
browser; account left with zero drafts, same as before testing started).

The official API's ``/logdrafts`` endpoint (``GCClient.get_log_drafts()``)
is replaced by a plain authenticated REST JSON API under
``/api/proxy/web/v1/LogDrafts`` — the same ``/api/proxy/web`` prefix family
``search_client.py``'s criteria/bbox search already uses (a genuinely
different backend from the tRPC ``/api/live/v1/trpc`` surface log CRUD
uses). **No CSRF token needed** for list, detail, or delete — confirmed
live that plain cookie-session auth was sufficient for all three (upload
was also confirmed live, from plain Python ``requests``, but isn't wired
into any resolver here — this module's scope is *download*, matching the
gap being closed)::

    GET  /api/proxy/web/v1/LogDrafts?sortAsc=false
        -> {"total": N, "data": [{referenceCode, guid, logTypeId,
            notePreview, dateLoggedUtc, dateLoggedGeocacheTime, geocache:
            {referenceCode, name, geocacheType, ianaTimezoneId, state}}, ...]}

    GET  /api/proxy/web/v1/LogDrafts/{referenceCode}
        -> same shape plus the full ``note`` text (``notePreview`` truncates
           around ~50 chars — confirmed live with a 265-char note — so the
           detail call is required for anything but a short note) and
           ``useFavoritePoint``/``isArchived``/``imageCount``.

Two things make this a *better* source than the official API's own
``get_log_drafts()``, not just an equivalent one:

- ``dateLoggedUtc`` is genuinely UTC (confirmed live: a submitted
  ``...T15:00:00Z`` came back as ``dateLoggedUtc: "2026-08-16T15:00:00"``
  exactly) — no timezone bug to correct here, unlike the official API's
  ``loggedDate`` (which ``fieldnote.py``'s original
  ``download_gc_fieldnotes()`` has to un-mislabel via a per-cache timezone
  lookup). This module needs no such correction at all.
- ``dateLoggedGeocacheTime`` gives the cache-local time directly (confirmed
  live: 15:00 UTC -> 17:00 for a Europe/Berlin cache, correct for August
  DST) — not used here (the field-note file format wants true UTC), but
  available should a future caller want it.

``logTypeId`` uses the same numeric id space as everywhere else in this
effort (``gc_reference._LOG_TYPE_MAP``) — confirmed live, id 4 = "Write
note", matching the map exactly.

See ``docs/reference/geocaching-com-web.md`` and
``geocaches.importers.fieldnote``'s resolver.
"""
from __future__ import annotations

import logging
from pathlib import Path

from geocaches.sync.gc_web.session import get_session, reset_session
from geocaches.sync.rate_limiter import polite_delay

logger = logging.getLogger(__name__)

_LIST_URL = "https://www.geocaching.com/api/proxy/web/v1/LogDrafts"
_DETAIL_URL = "https://www.geocaching.com/api/proxy/web/v1/LogDrafts/{ref}"


def _check_session(resp, what: str) -> None:
    if "account/signin" in resp.url:
        reset_session()
        raise RuntimeError(f"Web session expired while {what}")


def list_drafts_web(*, session=None) -> list[dict]:
    """Return the summary list of every log draft ("Field Note") on the
    account. ``notePreview`` may be truncated — use
    ``fetch_draft_detail_web()`` for the full text.
    """
    session = session or get_session()
    resp = session.get(
        _LIST_URL, params={"sortAsc": "false"}, headers={"Accept": "application/json"}, timeout=30,
    )
    resp.raise_for_status()
    _check_session(resp, "loading the drafts list")
    return resp.json().get("data", [])


def fetch_draft_detail_web(reference_code: str, *, session=None) -> dict:
    """Fetch one draft's full detail, including the untruncated ``note`` text."""
    session = session or get_session()
    resp = session.get(
        _DETAIL_URL.format(ref=reference_code), headers={"Accept": "application/json"}, timeout=30,
    )
    resp.raise_for_status()
    _check_session(resp, f"loading draft {reference_code} detail")
    return resp.json()


def fetch_all_drafts_web(*, session=None) -> list[dict]:
    """Return every draft on the account with full detail (note text +
    geocache info), one list call plus one detail call per draft.
    """
    session = session or get_session()
    rows = list_drafts_web(session=session)
    results = []
    for row in rows:
        ref = row.get("referenceCode", "")
        if not ref:
            continue
        polite_delay()
        results.append(fetch_draft_detail_web(ref, session=session))
    return results


def download_gc_fieldnotes_web() -> Path:
    """Fetch log drafts via the website and save as a field note file —
    same output contract as ``fieldnote._download_gc_fieldnotes_api()``, so
    it's a drop-in resolver alternative.

    Returns the path of the saved file. Raises ValueError if there are no
    drafts.
    """
    from datetime import datetime as _dt
    from geocaches.importers.fieldnote import _fieldnotes_dir
    from geocaches.sync.gc_reference import _LOG_TYPE_MAP

    drafts = fetch_all_drafts_web()
    if not drafts:
        raise ValueError("No log drafts found on geocaching.com.")

    lines = []
    for d in drafts:
        gc_code = (d.get("geocache") or {}).get("referenceCode", "").strip()
        if not gc_code:
            continue
        logged_utc = (d.get("dateLoggedUtc") or "").rstrip("Z")
        log_type = _LOG_TYPE_MAP.get(d.get("logTypeId"), "Write note")
        text = d.get("note", "") or ""
        text_escaped = text.replace('"', '""')
        lines.append(f'{gc_code},{logged_utc}Z,{log_type},"{text_escaped}"')

    content = "\n".join(lines) + "\n"

    fn = _dt.now().strftime("gc_web_%Y%m%d_%H%M%S.txt")
    out_path = _fieldnotes_dir() / fn
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(content, encoding="utf-8")
    logger.info("Fetched %d GC log drafts (web) -> %s", len(drafts), out_path)
    return out_path
