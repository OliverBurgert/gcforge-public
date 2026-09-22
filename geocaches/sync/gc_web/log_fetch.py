"""
GC log-history fetch via the website's classic logbook JSON endpoint — the
no-partner-API-token fallback for ``geocaches.sync.log_fetch``'s deep,
paginated log history (the official API's ``/geocachelogs`` endpoint).

**Discovered and live-verified 2026-08-14.** The classic (non-Next.js)
``/geocache/{code}`` detail page's "View all logs" link still leads to a
classic ASP.NET page (``/seek/geocache_logs.aspx?guid=...``) whose
pagination is backed by a small JSON endpoint:

    GET /seek/geocache.logbook
        ?tkn=<page token>&idx=<1-based page>&num=<page size>
        &showOwnerOnly=<bool>&decrypt=true&sp=false&sf=false

Two ordinary HTML scrapes resolve the two inputs it needs, same technique
already used by ``notify.py``/``tb_tracking.py``:

- ``guid`` — the cache's classic-page routing id, a public identifier (not
  secret) embedded in the "View all logs" link's href on ``/geocache/{code}``.
- ``tkn`` — a page-scoped token embedded in an inline script on
  ``/seek/geocache_logs.aspx?guid=...``. Confirmed live to stay valid across
  at least two separate ``geocache.logbook`` calls, so (mirroring
  ``cache_fetch.py``'s one-CSRF-token-per-batch design) it's fetched once per
  public-function call here, not once per page.

The response shape, confirmed live against a real 57-log cache:

    {"status": "success",
     "data": [{"LogID": <int>, "LogGuid": <uuid>, "LogTypeID": <int>,
               "LogType": <str>, "LogText": <html str>, "Visited": <date>,
               "UserName": <str>, "AccountGuid": <uuid>, ...}, ...],
     "pageInfo": {"idx", "rows", "size", "totalPages", "totalRows"}}

Three things make this a *better* source than the official API's log
endpoint, not just an equivalent one:

- ``LogTypeID``/``LogType`` match GCForge's own ``LogType`` enum and the
  official API's numeric ids exactly (verified live against ``enums.py``'s
  own doc comments, e.g. id 2 = "Found it", 45 = "Needs Maintenance", 46 =
  "Owner Maintenance") — no mapping table needed here at all.
- ``LogID`` (numeric) is reused directly as ``source_id`` — the same id
  space legacy GPX-imported logs already use, so the existing numeric ↔
  reference-code upgrade path in
  ``geocaches.sync.log_fetch._dedup_gc_logs`` (shared, imported below rather
  than duplicated) handles it with zero changes.
- ``showOwnerOnly=true`` returns *only* the requesting account's own logs in
  one call — confirmed live (7/7 rows matched the authenticated account on a
  real cache) — so ``ensure_my_gc_logs`` below doesn't need to page through
  the *entire* log history hunting for a match, unlike the official-API
  version. Oliver flagged that page-and-search approach as unreliable in
  practice; this sidesteps it entirely. Also exposed as its own explicit
  "Fetch my logs via Website" cache-detail button
  (``views/cache_actions.py``), always using this web path regardless of
  ``gc_access_mode`` — the user's menu choice already *is* the backend
  choice, same precedent as ``cache_fetch.py``'s "Fetch by List Generation".

See ``docs/reference/geocaching-com-web.md`` §5/§10 and
``geocaches/sync/gc_access.py`` (resolver wiring for the other three
functions here).
"""
from __future__ import annotations

import logging
import re

import requests
from bs4 import BeautifulSoup

from geocaches.sync.gc_web.session import get_session, reset_session
from geocaches.sync.rate_limiter import polite_delay

logger = logging.getLogger(__name__)

_CACHE_PAGE_URL = "https://www.geocaching.com/geocache/{code}"
_LOGS_PAGE_URL = "https://www.geocaching.com/seek/geocache_logs.aspx"
_LOGBOOK_URL = "https://www.geocaching.com/seek/geocache.logbook"

_GUID_RE = re.compile(r"geocache_logs\.aspx\?guid=([0-9a-f-]{36})", re.IGNORECASE)
_TOKEN_RE = re.compile(r"userToken\s*=\s*['\"]([^'\"]+)['\"]")

BATCH_SIZE = 50


def _resolve_guid(session: requests.Session, code: str) -> str:
    """Scrape the cache's classic-page routing guid — a public link, not a secret."""
    resp = session.get(_CACHE_PAGE_URL.format(code=code), timeout=20)
    resp.raise_for_status()
    if "account/signin" in resp.url:
        reset_session()
        raise RuntimeError("Web session expired while resolving cache guid")
    m = _GUID_RE.search(resp.text)
    if not m:
        raise RuntimeError(
            f"Could not find a logs guid on the {code} cache page — site may have changed."
        )
    return m.group(1)


def _resolve_token(session: requests.Session, guid: str) -> str:
    """Scrape the page-scoped logbook token, reused for every page in a batch."""
    resp = session.get(_LOGS_PAGE_URL, params={"guid": guid}, timeout=20)
    resp.raise_for_status()
    m = _TOKEN_RE.search(resp.text)
    if not m:
        raise RuntimeError(
            "Could not find a logbook token on the logs page — site may have changed."
        )
    return m.group(1)


def _resolve_batch(session: requests.Session, code: str) -> str:
    """Resolve the logbook token for ``code``, good for a whole batch of pages."""
    guid = _resolve_guid(session, code)
    return _resolve_token(session, guid)


def _fetch_logbook_page(
    session: requests.Session, token: str, *, idx: int, num: int, show_owner_only: bool = False,
) -> dict:
    """GET one page of the classic logbook endpoint. Raises on a non-success status."""
    polite_delay()
    resp = session.get(_LOGBOOK_URL, params={
        "tkn": token, "idx": idx, "num": num,
        "showOwnerOnly": "true" if show_owner_only else "false",
        "decrypt": "true", "sp": "false", "sf": "false",
    }, timeout=20)
    resp.raise_for_status()
    data = resp.json()
    if data.get("status") != "success":
        raise RuntimeError(f"geocache.logbook returned status={data.get('status')!r}")
    return data


def _strip_html(text: str) -> str:
    """LogText comes back HTML-wrapped (``<p>...<br />...</p>``); official-API text doesn't."""
    if not text:
        return ""
    return BeautifulSoup(text, "html.parser").get_text("\n").strip()


def _normalize_entries(entries: list[dict]) -> list[dict]:
    """Map raw ``geocache.logbook`` rows to the same dict shape
    ``geocaches.sync.log_fetch``'s shared dedup/save helpers expect
    (``gcprivate.gc_client.GCClient._normalize_logs``'s output shape)."""
    normalized = []
    for e in entries:
        date_str = (e.get("Visited") or "")[:10]
        if not date_str:
            continue
        normalized.append({
            "log_type": e.get("LogType") or "Write note",
            "logged_date": date_str,
            "user_name": e.get("UserName", ""),
            "user_id": str(e.get("AccountGuid", "")),
            "text": _strip_html(e.get("LogText", "")),
            "source_id": str(e.get("LogID", "")),
            "source": "gc",
        })
    return normalized


def _get_cache_or_none(code: str):
    from geocaches.models import Geocache
    return Geocache.objects.filter(gc_code=code).first()


def fetch_recent_gc_logs(
    code: str, count: int = BATCH_SIZE, *, session: requests.Session | None = None,
) -> tuple[int, int]:
    """Fetch the N most recent GC logs for ``code``. Returns (new_logs_saved, page_count)."""
    from geocaches.sync.log_fetch import _dedup_gc_logs, _save_logs

    cache = _get_cache_or_none(code)
    if not cache:
        return 0, 0

    session = session or get_session()
    token = _resolve_batch(session, code)
    data = _fetch_logbook_page(session, token, idx=1, num=count)
    raw = data.get("data") or []
    if not raw:
        return 0, 0

    existing_ids = set(cache.logs.filter(source="gc").values_list("source_id", flat=True))
    normalized = _normalize_entries(raw)
    new_logs, upgraded = _dedup_gc_logs(cache, normalized, existing_ids)
    if upgraded:
        logger.info("Upgraded %d GC log source_id(s) from numeric to API ref for %s", upgraded, code)
    saved = _save_logs(cache, new_logs)
    logger.info("fetch_recent_gc_logs(web) %s: fetched %d, saved %d new", code, len(raw), saved)
    return saved, len(raw)


def fetch_more_gc_logs(
    code: str, skip: int = 0, count: int = BATCH_SIZE, *, session: requests.Session | None = None,
) -> tuple[int, int]:
    """Fetch ``count`` GC logs starting at ``skip`` offset. Returns (new_logs_saved, page_count).

    The logbook endpoint pages by 1-based index rather than raw skip/take;
    ``skip`` is assumed to be a multiple of ``count`` (true for every
    existing caller, which always increments by exactly one page).
    """
    from geocaches.sync.log_fetch import _dedup_gc_logs, _save_logs

    cache = _get_cache_or_none(code)
    if not cache:
        return 0, 0

    session = session or get_session()
    token = _resolve_batch(session, code)
    idx = (skip // count) + 1 if count else 1
    data = _fetch_logbook_page(session, token, idx=idx, num=count)
    raw = data.get("data") or []
    if not raw:
        return 0, 0

    existing_ids = set(cache.logs.filter(source="gc").values_list("source_id", flat=True))
    normalized = _normalize_entries(raw)
    new_logs, upgraded = _dedup_gc_logs(cache, normalized, existing_ids)
    if upgraded:
        logger.info("Upgraded %d GC log source_id(s) from numeric to API ref for %s", upgraded, code)
    saved = _save_logs(cache, new_logs)
    logger.info("fetch_more_gc_logs(web) %s: idx=%d, fetched %d, saved %d new", code, idx, len(raw), saved)
    return saved, len(raw)


def fetch_all_gc_logs(code: str, *, session: requests.Session | None = None) -> int:
    """Page through ALL logs for a cache until exhausted. Returns total new logs saved."""
    from geocaches.sync.log_fetch import _dedup_gc_logs, _save_logs

    cache = _get_cache_or_none(code)
    if not cache:
        return 0

    session = session or get_session()
    token = _resolve_batch(session, code)
    existing_ids = set(cache.logs.filter(source="gc").values_list("source_id", flat=True))
    total_saved = 0
    idx = 1

    while True:
        data = _fetch_logbook_page(session, token, idx=idx, num=BATCH_SIZE)
        raw = data.get("data") or []
        if not raw:
            break

        normalized = _normalize_entries(raw)
        new_logs, upgraded = _dedup_gc_logs(cache, normalized, existing_ids)
        if upgraded:
            logger.info("Upgraded %d GC log source_id(s) from numeric to API ref for %s", upgraded, code)
        if new_logs:
            total_saved += _save_logs(cache, new_logs)
            existing_ids.update(log["source_id"] for log in new_logs)

        total_pages = (data.get("pageInfo") or {}).get("totalPages", idx)
        if idx >= total_pages:
            break
        idx += 1

    logger.info("fetch_all_gc_logs(web) %s: paged %d page(s), saved %d new log(s)", code, idx, total_saved)
    return total_saved


def ensure_my_gc_logs(code: str, *, session: requests.Session | None = None) -> int:
    """Fetch and save all of the caller's own logs on ``code``, saving anything new.

    Uses ``showOwnerOnly=true`` to ask the server directly for just the
    requesting account's logs, rather than paging through the entire log
    history looking for a match (the official-API version's approach,
    which Oliver found unreliable in practice). Returns total new logs saved.
    """
    from geocaches.sync.log_fetch import _dedup_gc_logs, _save_logs

    cache = _get_cache_or_none(code)
    if not cache:
        return 0

    session = session or get_session()
    token = _resolve_batch(session, code)
    existing_ids = set(cache.logs.filter(source="gc").values_list("source_id", flat=True))
    total_saved = 0
    idx = 1

    while True:
        data = _fetch_logbook_page(session, token, idx=idx, num=BATCH_SIZE, show_owner_only=True)
        raw = data.get("data") or []
        if not raw:
            break

        normalized = _normalize_entries(raw)
        new_logs, upgraded = _dedup_gc_logs(cache, normalized, existing_ids)
        if upgraded:
            logger.info("Upgraded %d GC log source_id(s) from numeric to API ref for %s", upgraded, code)
        if new_logs:
            total_saved += _save_logs(cache, new_logs)
            existing_ids.update(log["source_id"] for log in new_logs)

        total_pages = (data.get("pageInfo") or {}).get("totalPages", idx)
        if idx >= total_pages:
            break
        idx += 1

    if total_saved:
        logger.info("ensure_my_gc_logs(web) %s: saved %d log(s)", code, total_saved)
    return total_saved
