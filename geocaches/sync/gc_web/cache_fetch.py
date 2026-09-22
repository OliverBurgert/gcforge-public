"""
Web/tRPC-based full-mode geocache fetch — the no-partner-API-token
fallback for pulling complete cache detail (used where ``GCClient.get_caches()``
would otherwise be the only option).

**Live-verified 2026-08-13** via two HAR captures: the full bookmark-list
lifecycle (``web.lists.create``/``addGeocache``/``bulkAddGeocaches``/``delete``,
all under tRPC) and a list GPX download
(``POST /api/live/v1/gpx/list/{list_ref}``, plain REST, not tRPC — same
family as ``log_client.upload_log_image()``'s image endpoints). A single
CSRF token, fetched once from ``/plan/lists``, covers a whole batch —
confirmed identical (same token) across create/addGeocache/
bulkAddGeocaches/delete within one session, so ``fetch_caches_gpx()`` below
fetches it only once rather than per call.

The downloaded GPX is the **same standard Groundspeak 1.0.1 format** GC's
own Pocket Query system produces — confirmed to include
``long_description``, ``encoded_hints``, ``attributes``, difficulty/terrain,
container, and full ``logs`` (id/date/type/finder/text) for a real cache
checked in the capture. That means no new normalizer is needed at all:
``fetch_and_import_caches()`` hands the bytes straight to the existing
``geocaches.services.import_and_enrich("gpx", ...)`` pipeline, exactly as if
the GPX had been uploaded by the user.

**Batch size — confirmed live 2026-08-13.** ``bulkAddGeocaches`` hard-rejects
above 500: the tRPC server's own Zod validation returned ``"Array must
contain at most 500 element(s)"`` when 1000 real codes were tried in one
call (a precise, authoritative limit, not an inferred one). 500 succeeded
cleanly (500/500, 0 failures), and the resulting 500-cache GPX download also
succeeded (4.5MB, 1093 ``<wpt>`` elements — more than 500 because each cache
can carry companion waypoints too). A list's own total membership cap is
1000, per the fill counter (``x/1000``) shown on the list page's own UI.
``fetch_caches_gpx()`` enforces both: it chunks ``bulkAddGeocaches`` calls at
``BULK_ADD_MAX`` (500) and raises ``ValueError`` above ``LIST_MAX`` (1000)
rather than silently truncating or sending a call the server is known to
reject.
"""

import logging
import tempfile
from pathlib import Path

import requests

from geocaches.sync.gc_web import trpc
from geocaches.sync.gc_web.session import get_session
from geocaches.sync.rate_limiter import polite_delay

logger = logging.getLogger(__name__)

_LISTS_PAGE_URL = "https://www.geocaching.com/plan/lists"
_GPX_DOWNLOAD_URL = "https://www.geocaching.com/api/live/v1/gpx/list/{list_ref}"

# Confirmed live 2026-08-13 — see module docstring.
BULK_ADD_MAX = 500
LIST_MAX = 1000


def create_list(name: str, *, csrf_token: str, session: requests.Session) -> dict:
    """Create a new bookmark list. Returns the list object, incl. ``referenceCode``."""
    input_data = {"name": name, "type": {"code": "bm"}}
    return trpc.mutate(session, "web.lists.create", input_data, csrf_token=csrf_token)


def add_geocache(list_ref: str, gc_code: str, *, csrf_token: str, session: requests.Session) -> dict:
    """Add a single cache to a list. Returns a cache summary dict."""
    input_data = {"listReferenceCode": list_ref, "gcCode": gc_code}
    return trpc.mutate(session, "web.lists.addGeocache", input_data, csrf_token=csrf_token)


def bulk_add_geocaches(
    list_ref: str, gc_codes: list[str], *, csrf_token: str, session: requests.Session,
) -> dict:
    """Add multiple caches to a list in one call.

    Returns ``{"findCount": int, "successes": [{"referenceCode"}], "failures": [...]}``.

    Raises ``ValueError`` above ``BULK_ADD_MAX`` (500) — the server rejects
    a larger single call outright (confirmed live, see module docstring);
    callers with more codes should chunk (``fetch_caches_gpx()`` does this
    automatically).
    """
    if len(gc_codes) > BULK_ADD_MAX:
        raise ValueError(
            f"bulkAddGeocaches accepts at most {BULK_ADD_MAX} codes per call "
            f"(got {len(gc_codes)}) — the server rejects more with a 400. Chunk the request."
        )
    input_data = {"listReferenceCode": list_ref, "referenceCodes": gc_codes}
    return trpc.mutate(session, "web.lists.bulkAddGeocaches", input_data, csrf_token=csrf_token)


def delete_list(list_ref: str, *, csrf_token: str, session: requests.Session) -> bool:
    """Delete a list — and everything in it — in one call. Returns ``True`` on success."""
    input_data = {"referenceCode": list_ref}
    return trpc.mutate(session, "web.lists.delete", input_data, csrf_token=csrf_token)


def download_list_gpx(list_ref: str, *, csrf_token: str, session: requests.Session) -> bytes:
    """Download a list's contents as a standard Groundspeak GPX file (raw bytes)."""
    polite_delay()
    resp = session.post(
        _GPX_DOWNLOAD_URL.format(list_ref=list_ref),
        headers={trpc.CSRF_HEADER: csrf_token, "Accept": "application/gpx+xml"},
        timeout=120,
    )
    resp.raise_for_status()
    return resp.content


def fetch_caches_gpx(gc_codes: list[str], *, session: requests.Session | None = None) -> bytes:
    """Fetch full cache detail for ``gc_codes`` as a GPX blob, via a scratch list.

    Creates a temporary bookmark list, bulk-adds the codes (chunked at
    ``BULK_ADD_MAX`` per call if needed), downloads the list as GPX, then
    deletes the list — self-contained, nothing left behind even if
    interrupted partway (cleanup runs in a ``finally``). Any codes that
    failed to add are logged, not raised — the caller gets whatever GPX the
    endpoint returns for the codes that did succeed.

    Raises ``ValueError`` above ``LIST_MAX`` (1000) — a single list can't
    hold more (see module docstring); split into multiple calls instead of
    silently truncating.

    Returns raw GPX bytes — feed to ``fetch_and_import_caches()`` below, or
    handle directly via ``geocaches.importers.import_gc_gpx``.
    """
    if len(gc_codes) > LIST_MAX:
        raise ValueError(
            f"fetch_caches_gpx() supports at most {LIST_MAX} codes per call "
            f"(a list can't hold more, got {len(gc_codes)}) — split into multiple calls."
        )

    session = session or get_session()
    csrf_token = trpc.fetch_csrf_token(session, _LISTS_PAGE_URL)

    list_obj = create_list("GCForge sync", csrf_token=csrf_token, session=session)
    list_ref = list_obj["referenceCode"]
    logger.info("Web cache fetch: created scratch list %s for %d code(s)", list_ref, len(gc_codes))

    try:
        all_failures = []
        for i in range(0, len(gc_codes), BULK_ADD_MAX):
            chunk = gc_codes[i:i + BULK_ADD_MAX]
            add_result = bulk_add_geocaches(list_ref, chunk, csrf_token=csrf_token, session=session)
            all_failures.extend(add_result.get("failures") or [])
        if all_failures:
            logger.warning(
                "Web cache fetch: %d code(s) failed to add to scratch list %s: %s",
                len(all_failures), list_ref, all_failures,
            )
        return download_list_gpx(list_ref, csrf_token=csrf_token, session=session)
    finally:
        try:
            delete_list(list_ref, csrf_token=csrf_token, session=session)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Web cache fetch: failed to clean up scratch list %s: %s", list_ref, exc)


def fetch_and_import_caches(
    gc_codes: list[str], *, tag_names: list[str] | None = None, session: requests.Session | None = None,
):
    """Fetch ``gc_codes`` via the web and import them through the normal GPX pipeline.

    Writes the fetched GPX to a temp file and delegates to
    ``geocaches.services.import_and_enrich("gpx", ...)`` — same code path as
    a user-uploaded GPX, including auto-enrichment. Returns the resulting
    ``ImportStats``.
    """
    from geocaches.services import import_and_enrich

    gpx_bytes = fetch_caches_gpx(gc_codes, session=session)

    with tempfile.NamedTemporaryFile(suffix=".gpx", delete=False) as tmp:
        tmp.write(gpx_bytes)
        tmp_path = tmp.name

    try:
        return import_and_enrich("gpx", tmp_path, tag_names=tag_names)
    finally:
        Path(tmp_path).unlink(missing_ok=True)
