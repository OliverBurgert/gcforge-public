"""
GC ignore-list sync via the website — no partner-API token needed.

**Discovered and live-verified 2026-08-16** (Oliver's real account, using
GCBPNTE — Bahnschatz #3, not his own cache — for a real add-then-remove
round trip; see below).

Listing
-------
The official API models the ignore list as a special system-managed list
(type code ``"il"``, one per account) addressed via the generic
``/lists/{referenceCode}/geocaches`` endpoint (see
``gcprivate.gc_client.GCClient.get_ignore_list()``). The website's
``/plan/lists`` app (already used by ``cache_fetch.py`` for ordinary
bookmark lists) has a dedicated route for this exact list:
``/plan/lists/ignored``. A plain authenticated GET server-renders the
**entire** list into the page's ``__NEXT_DATA__.props.pageProps`` — same
SSR-embedding technique already used by ``trpc.fetch_csrf_token()`` and (for
a whole-page scrape) ``souvenirs.py``. Confirmed live: ``pageProps.list``
carries the special list's own metadata (``referenceCode``, ``type.code ==
"il"``), and ``pageProps.geocaches.data`` carries one entry per ignored
cache with richer fields than the official API's own ``get_ignore_list()``
(which returns only ``referenceCode,name,status``) — ``geocacheType``/
``containerType``/``difficulty``/``terrain``/``state.isArchived``/
``state.isAvailable`` all come back directly, letting this derive a
``CacheStatus`` locally instead of trusting a raw status string.
``pageProps`` also carries ``skip``/``take`` (500 seen live) alongside
``geocaches.total``, so pagination is implemented defensively for accounts
with more than 500 ignored caches — only a single page (8 entries) was
actually exercised live.

Add / remove
------------
The classic (non-Next.js) ``/geocache/{code}`` detail page's "Ignore"
sidebar link points at a classic ASP.NET confirmation page::

    /bookmarks/ignore.aspx?guid=<cache guid>&WptTypeID=<numeric type id>

The same URL toggles both directions — GETting it shows a confirmation
appropriate to the cache's *current* ignore state ("add" vs "remove"
wording), and POSTing the standard WebForms postback fields (scraped
``__VIEWSTATE``/``__VIEWSTATEGENERATOR``/``__RequestVerificationToken``/
``returnUrl``/``__EVENTTARGET``/``__EVENTARGUMENT`` plus the
``ctl00$ContentBody$btnYes`` submit button) confirms it. **Both directions
use the exact same button name** — only its *value* text differs ("Yes.
Ignore it." vs "Yes. I want to restore it.") — so this always re-parses the
confirmation page's text before POSTing and refuses to proceed if it
doesn't match the caller's intended direction, treating a mismatch as an
idempotent no-op rather than risk silently reversing an already-correct
state (e.g. a naive "add" call on an already-ignored cache would otherwise
flip it to "remove").

Live-verified round trip on GCBPNTE: added via this exact mechanism ("You
have successfully added ... to your ignore list."), confirmed it showed up
via ``/plan/lists/ignored`` (``geocachesTotal`` incremented to 8), then
removed again via the same toggle ("Removed from the ignore list.") to
restore the account to its prior state.

See ``docs/reference/geocaching-com-web.md`` and
``geocaches.services.ignore_list``'s resolver.
"""
from __future__ import annotations

import json
import logging
import re

from bs4 import BeautifulSoup

from geocaches.sync.gc_web.session import get_session, reset_session
from geocaches.sync.rate_limiter import polite_delay

logger = logging.getLogger(__name__)

_IGNORED_LIST_URL = "https://www.geocaching.com/plan/lists/ignored"
_CACHE_PAGE_URL = "https://www.geocaching.com/geocache/{code}"
_IGNORE_TOGGLE_URL = "https://www.geocaching.com/bookmarks/ignore.aspx"

_IGNORE_LINK_RE = re.compile(
    r"ignore\.aspx\?guid=([0-9a-f-]{36})&amp;WptTypeID=(\d+)", re.IGNORECASE,
)
_ADD_CONFIRM_RE = re.compile(r"are you sure you want to add", re.IGNORECASE)
_REMOVE_CONFIRM_RE = re.compile(r"already exists on your ignore list", re.IGNORECASE)
_ADD_SUCCESS_RE = re.compile(r"successfully added .* to your ignore list", re.IGNORECASE)
_REMOVE_SUCCESS_RE = re.compile(r"removed from the ignore list", re.IGNORECASE)

_TAKE = 500


def _check_session(resp, what: str) -> None:
    if "account/signin" in resp.url:
        reset_session()
        raise RuntimeError(f"Web session expired while {what}")


def _derive_status(state: dict) -> str:
    """Map the ignored-list page's ``state`` flags to a ``CacheStatus`` value."""
    from geocaches.models.enums import CacheStatus
    if state.get("isArchived"):
        return CacheStatus.ARCHIVED
    if state.get("isAvailable") is False:
        return CacheStatus.DISABLED
    return CacheStatus.ACTIVE


def list_ignored_gc_web(*, session=None) -> list[dict]:
    """Return every geocache on the account's GC ignore list, scraped from
    ``/plan/lists/ignored``'s server-rendered ``__NEXT_DATA__``.

    Each dict is shaped like the official API's ``get_ignore_list()``
    (``referenceCode``, ``name``, ``status``) so it's a drop-in source for
    ``geocaches.services.ignore_list.sync_gc_ignore_list()``'s resolver.
    """
    session = session or get_session()
    results: list[dict] = []
    skip = 0

    while True:
        resp = session.get(_IGNORED_LIST_URL, params={"skip": skip, "take": _TAKE}, timeout=30)
        resp.raise_for_status()
        _check_session(resp, "loading the ignore list")

        soup = BeautifulSoup(resp.text, "html.parser")
        script = soup.find("script", id="__NEXT_DATA__")
        if not script or not script.string:
            raise RuntimeError(
                "Could not find __NEXT_DATA__ on the ignore-list page — site may have changed."
            )
        data = json.loads(script.string)
        page_props = data.get("props", {}).get("pageProps", {})
        geocaches = page_props.get("geocaches", {})
        rows = geocaches.get("data", [])
        total = geocaches.get("total", len(rows))

        for row in rows:
            code = row.get("referenceCode", "")
            if not code:
                continue
            results.append({
                "referenceCode": code,
                "name": row.get("name", ""),
                "status": _derive_status(row.get("state", {})),
            })

        skip += len(rows)
        if not rows or skip >= total:
            break
        polite_delay()

    return results


def _resolve_ignore_link(session, code: str) -> tuple[str, str]:
    """Scrape the cache's classic-page "Ignore" link — guid + WptTypeID, both
    public routing ids embedded in the page's HTML, not secrets."""
    resp = session.get(_CACHE_PAGE_URL.format(code=code), timeout=20)
    resp.raise_for_status()
    _check_session(resp, f"resolving the ignore link for {code}")
    m = _IGNORE_LINK_RE.search(resp.text)
    if not m:
        raise RuntimeError(
            f"Could not find an Ignore link on the {code} cache page — site may have changed."
        )
    return m.group(1), m.group(2)


def _toggle_ignore(session, guid: str, wpt_type_id: str, *, want: str) -> bool:
    """GET the ignore.aspx confirmation page, verify it matches ``want``
    ("add" or "remove"), and POST the confirmation if so.

    Returns True if a mutation was made, False if the account was already in
    the desired state (idempotent no-op — refuses to POST the *other*
    direction's confirmation, since both directions share the same button
    name and would otherwise silently flip the opposite way).
    """
    resp = session.get(
        _IGNORE_TOGGLE_URL, params={"guid": guid, "WptTypeID": wpt_type_id}, timeout=20,
    )
    resp.raise_for_status()
    _check_session(resp, "loading the ignore confirmation page")

    text = resp.text
    if _ADD_CONFIRM_RE.search(text):
        page_wants = "add"
    elif _REMOVE_CONFIRM_RE.search(text):
        page_wants = "remove"
    else:
        raise RuntimeError("Unrecognized ignore-list confirmation page — site may have changed.")

    if page_wants != want:
        logger.info("Ignore-list %s: already in the desired state, no-op.", want)
        return False

    soup = BeautifulSoup(text, "html.parser")
    form = soup.find("form", id="aspnetForm") or soup.find("form")
    if not form:
        raise RuntimeError("Could not find the ignore-list confirmation form.")

    data = {}
    for inp in form.find_all("input"):
        name = inp.get("name")
        if not name:
            continue
        if inp.get("type") == "submit" and name != "ctl00$ContentBody$btnYes":
            continue  # never submit btnNo
        data[name] = inp.get("value", "") or ""

    polite_delay()
    post_resp = session.post(
        _IGNORE_TOGGLE_URL,
        params={"guid": guid, "WptTypeID": wpt_type_id},
        data=data,
        timeout=20,
        headers={"Referer": resp.url, "Origin": "https://www.geocaching.com"},
    )
    post_resp.raise_for_status()
    _check_session(post_resp, "confirming the ignore-list change")

    success_re = _ADD_SUCCESS_RE if want == "add" else _REMOVE_SUCCESS_RE
    if not success_re.search(post_resp.text):
        raise RuntimeError(
            f"Ignore-list {want} confirmation did not report success — site may have changed."
        )
    return True


def add_gc_web(gc_code: str, *, session=None) -> bool:
    """Add a geocache to the account's GC ignore list via the website.

    Returns True if it made a change, False if it was already ignored
    (idempotent). Raises RuntimeError on an unexpected page/session issue.
    """
    session = session or get_session()
    guid, wpt_type_id = _resolve_ignore_link(session, gc_code)
    return _toggle_ignore(session, guid, wpt_type_id, want="add")


def remove_gc_web(gc_code: str, *, session=None) -> bool:
    """Remove a geocache from the account's GC ignore list via the website.

    Returns True if it made a change, False if it wasn't on the list
    (idempotent). Raises RuntimeError on an unexpected page/session issue.
    """
    session = session or get_session()
    guid, wpt_type_id = _resolve_ignore_link(session, gc_code)
    return _toggle_ignore(session, guid, wpt_type_id, want="remove")
