import logging
import os
import tempfile
import threading
import time
from collections import deque

logger = logging.getLogger("geocaches.pq")

# ---------------------------------------------------------------------------
# Website status cache — the PQ list page scrape (GUIDs, trigger availability,
# deleted/struck state) is slow, so we keep it off the page-load path: the view
# renders from the GC API immediately and a daemon thread refreshes this cache
# in the background.  The page swaps in the web-derived chips once it's ready.
# ---------------------------------------------------------------------------

_web_status_lock = threading.Lock()
_web_status: dict = {"rows": None, "summary": {}, "fetched_at": 0.0, "refreshing": False}


def get_web_status_snapshot() -> dict:
    """Return a copy of the current cached web status (non-blocking)."""
    with _web_status_lock:
        return dict(_web_status)


def invalidate_web_status() -> None:
    """Mark the cache stale so the next ``ensure_web_status_fresh`` refetches."""
    with _web_status_lock:
        _web_status["fetched_at"] = 0.0


def ensure_web_status_fresh(max_age: float = 120.0) -> None:
    """Kick a background refresh if the cache is stale and none is in flight.

    Non-blocking: returns immediately; callers read ``get_web_status_snapshot``.
    """
    with _web_status_lock:
        if _web_status["refreshing"]:
            return
        fresh = (
            _web_status["rows"] is not None
            and (time.monotonic() - _web_status["fetched_at"]) < max_age
        )
        if fresh:
            return
        _web_status["refreshing"] = True
    threading.Thread(target=_refresh_web_status_worker, daemon=True).start()


def _refresh_web_status_worker() -> None:
    from django.db import close_old_connections
    close_old_connections()
    rows = summary = None
    try:
        from geocaches.pq.trigger import get_pq_web_status
        rows, summary = get_pq_web_status()
    except Exception as exc:
        logger.warning("PQ web status refresh failed: %s", exc)
    with _web_status_lock:
        if rows is not None:
            _web_status["rows"] = rows
            _web_status["summary"] = summary
            _web_status["fetched_at"] = time.monotonic()
        _web_status["refreshing"] = False

# ---------------------------------------------------------------------------
# Sequential download queue — prevents "database is locked" from concurrent
# downloads.  Items are (reference_code, name, tag_names, generation_date)
# tuples.
# ---------------------------------------------------------------------------

_download_queue: deque[tuple[str, str, list[str] | None, object]] = deque()
_queue_lock = threading.Lock()
_queue_task_id: str | None = None  # ID of the currently running queue worker


def enqueue_pq_download(reference_code, name, tag_names=None, generation_date=None):
    """Add a PQ to the download queue and ensure the worker task is running.

    ``generation_date`` (a ``date``) is the PST date of the generation being
    downloaded, threaded into the ``pq_imported`` tracking key (see
    ``pq_import_key()``) so the "Imported" badge reflects *this* generation.
    Defaults to today's PST date if omitted — correct for every queue-driven
    caller (``bulk_trigger_and_download``, ``trigger_and_download_by_pattern``,
    ``download_all_fresh``), since each only enqueues a PQ once it has
    confirmed today's generation is ready. A caller downloading a PQ that
    *isn't* necessarily fresh (e.g. a manually-selected older-but-still-ready
    row) should pass the row's actual generation date explicitly.

    Returns the task ID of the queue worker.
    """
    from geocaches.tasks import submit_task, get_task

    with _queue_lock:
        _download_queue.append((reference_code, name, tag_names, generation_date))

        global _queue_task_id
        # Check if worker is still running
        if _queue_task_id:
            task = get_task(_queue_task_id)
            if task and task["state"] == "running":
                return _queue_task_id

        # Start a new worker
        _queue_task_id = submit_task(
            "PQ download queue",
            _queue_worker,
        )
        return _queue_task_id


def _queue_worker(task_info=None):
    """Process the download queue sequentially.

    Runs imports with ``auto_enrich=False`` so we can fire a single enrichment
    pass once the queue drains, instead of one enrichment task per PQ (which
    would race with the next import inside the same worker pool).

    If a PQ fails with "database is locked" it is re-queued at the back and
    retried up to 3 times (with an increasing sleep so the competing writer has
    time to finish) rather than failing permanently.
    """
    from datetime import datetime, timezone
    from django.db.utils import OperationalError
    from geocaches.pq.trigger import _gc_today_pst

    results = []
    total_created = 0
    total_updated = 0
    processed = 0
    batch_since = datetime.now(timezone.utc)
    retry_counts: dict[str, int] = {}

    while True:
        with _queue_lock:
            if not _download_queue:
                break
            ref, name, tag_names, generation_date = _download_queue.popleft()
            remaining = len(_download_queue)

        retry = retry_counts.get(ref, 0)
        retry_info = f" (retry {retry}/3)" if retry else ""
        queue_info = f" (+{remaining} queued)" if remaining else ""

        if task_info:
            task_info.phase = f"Downloading {name}{retry_info}{queue_info}"
            task_info.total = processed + remaining + 1
            task_info.completed = processed

        try:
            result = _do_download_and_import(ref, name, tag_names=tag_names,
                                              auto_enrich=False)
            results.append(result)
            total_created += result.get("created", 0)
            total_updated += result.get("updated", 0)
            _mark_pq_imported(pq_import_key(ref, generation_date or _gc_today_pst()))
            retry_counts.pop(ref, None)
            processed += 1

        except OperationalError as exc:
            if "database is locked" in str(exc) and retry < 3:
                retry_counts[ref] = retry + 1
                with _queue_lock:
                    _download_queue.append((ref, name, tag_names, generation_date))
                wait = 5 * (retry + 1)
                logger.warning(
                    "PQ %s: database locked (retry %d/3), sleeping %ds before next attempt",
                    name, retry + 1, wait,
                )
                time.sleep(wait)
                continue
            logger.warning("Failed to download/import PQ %s: %s", ref, exc)
            results.append({"pq_name": name, "reference_code": ref, "error": str(exc)})
            processed += 1

        except Exception as exc:
            logger.warning("Failed to download/import PQ %s: %s", ref, exc)
            results.append({"pq_name": name, "reference_code": ref, "error": str(exc)})
            processed += 1

        if task_info:
            task_info.completed = processed

    if task_info:
        task_info.phase = "Enriching"

    # One enrichment pass for the whole batch — runs after all imports finished
    # so the import path is no longer competing for the SQLite writer.
    _start_batch_enrich(batch_since)

    if task_info:
        task_info.phase = "Done"
        task_info.completed = processed
        task_info.total = processed

    if len(results) == 1:
        return results[0]

    return {
        "results": results,
        "total_created": total_created,
        "total_updated": total_updated,
    }


# ---------------------------------------------------------------------------
# Import tracking
# ---------------------------------------------------------------------------

def _mark_pq_imported(reference_code):
    """Record that a PQ was imported."""
    from datetime import datetime, timezone
    from preferences.models import UserPreference
    imported = UserPreference.get("pq_imported", {})
    imported[reference_code] = datetime.now(timezone.utc).isoformat()
    UserPreference.set("pq_imported", imported)


def pq_import_key(identifier: str, generation_date) -> str:
    """Build the "imported" tracking key for one PQ generation.

    Both a website-scraped GUID and an official-API referenceCode are
    *stable* per saved Pocket Query — neither changes when the PQ
    regenerates, they identify the saved query slot, not a particular run.
    Keying ``pq_imported`` on the bare identifier meant that once a
    recurring PQ was imported once, its "Imported" badge stuck forever, even
    after a later regeneration was triggered and never actually downloaded
    (2026-09-06: a 7-PQ pattern trigger left 2 PQs' fresh generations
    undownloaded, but both still showed "Imported" from a successful run
    weeks earlier — confirmed live: the official API returned the *same*
    referenceCode for those PQs on both occasions). Folding in the PST
    generation date — coarse enough since GC allows only one run per PST
    day — makes each generation's import status independent.
    """
    return f"{identifier}@{generation_date.isoformat()}"


def get_imported_pqs() -> dict[str, str]:
    """Return {reference_code: iso_timestamp} of imported PQs."""
    from preferences.models import UserPreference
    return UserPreference.get("pq_imported", {})


# ---------------------------------------------------------------------------
# Core download/import
# ---------------------------------------------------------------------------

def list_pocket_queries():
    from geocaches.sync.gc_access import get_gc_client
    client = get_gc_client()
    return client.get_pocket_queries()


def _import_pq_zip_bytes(reference_code, name, data: bytes, tag_names=None,
                          task_info=None, auto_enrich=True):
    """Shared zip-extract-and-import logic, given already-downloaded PQ zip bytes.

    Backend-agnostic — used by both the official-API download path
    (``_do_download_and_import``) and the website-scrape path
    (``download_and_import_pq_web``, added 2026-08-15). Everything past "here
    are the raw zip bytes" is identical regardless of how they were fetched,
    since both sources return the same Groundspeak GPX-in-zip format.
    """
    from geocaches.services import import_and_enrich

    tmp = tempfile.NamedTemporaryFile(
        suffix=".zip", prefix=f"pq_{reference_code}_", delete=False,
    )
    try:
        tmp.write(data)
        tmp.close()

        if task_info:
            task_info.completed = 1
            task_info.phase = f"Importing {name}"

        logger.info("PQ import start: %s (%s)", name, reference_code)
        result = import_and_enrich("unified_gpx", tmp.name, tag_names,
                                   auto_enrich=auto_enrich)

        created = getattr(result, "created", 0)
        updated = getattr(result, "updated", 0)
        locked = getattr(result, "locked", 0)
        errors = getattr(result, "errors", [])
        logger.info(
            "PQ import complete: %s (%s) — created=%d updated=%d locked=%d errors=%d",
            name, reference_code, created, updated, locked, len(errors),
        )
        if errors:
            for e in errors[:10]:
                logger.warning("PQ import error (%s): %s", reference_code, e)

        if task_info:
            task_info.completed = 2
            task_info.phase = "Done"

        return {
            "pq_name": name,
            "reference_code": reference_code,
            "created": created,
            "updated": updated,
            "locked": locked,
            "errors": errors,
        }
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass


def _do_download_and_import(reference_code, name, tag_names=None, task_info=None,
                            auto_enrich=True):
    from geocaches.sync.gc_access import get_gc_client

    if task_info:
        task_info.phase = f"Downloading {name}"
        task_info.total = 2

    logger.info("PQ download start: %s (%s)", name, reference_code)
    client = get_gc_client()
    data = client.download_pocket_query(reference_code)
    logger.info("PQ download complete: %s (%s) — %d bytes received", name, reference_code, len(data))

    return _import_pq_zip_bytes(reference_code, name, data, tag_names, task_info, auto_enrich)


def download_and_import_pq(reference_code, name, tag_names=None, task_info=None):
    """Public API: enqueue a PQ download via the sequential queue."""
    queue_task_id = enqueue_pq_download(reference_code, name, tag_names=tag_names)
    return {
        "pq_name": name,
        "reference_code": reference_code,
        "status": "queued",
        "queue_task_id": queue_task_id,
    }


def download_and_import_pq_web(guid, name, tag_names=None, task_info=None, generation_date=None):
    """Download + import a ready Pocket Query via the website — no API token needed.

    Uses ``trigger.download_pq_web()`` (confirmed 2026-08-15 to return the
    same zip format as the official API) then the shared
    ``_import_pq_zip_bytes()`` logic. Synchronous — unlike
    ``download_and_import_pq()``'s official-API path, this doesn't go
    through the sequential download queue; callers wrap it in their own
    background task (see ``views/pq.py``'s ``pq_management_web``).

    ``generation_date`` should be the PST date of the generation being
    downloaded (the row's parsed "Last Generated" date); defaults to today's
    PST date, correct for every current caller since they all download a PQ
    immediately after confirming it's freshly ready. Threaded into the
    ``pq_imported`` tracking key (see ``pq_import_key()``) so the "Imported"
    badge reflects *this* generation, not "ever imported".
    """
    from geocaches.pq.trigger import _gc_today_pst, download_pq_web

    if task_info:
        task_info.phase = f"Downloading {name}"
        task_info.total = 2

    logger.info("PQ web download start: %s (%s)", name, guid)
    data = download_pq_web(guid)
    logger.info("PQ web download complete: %s (%s) — %d bytes received", name, guid, len(data))

    result = _import_pq_zip_bytes(guid, name, data, tag_names, task_info)
    _mark_pq_imported(pq_import_key(guid, generation_date or _gc_today_pst()))
    return result


def trigger_and_download_web(guids, names=None, tag_names=None, task_info=None):
    """Trigger, wait for, download, and import PQs — entirely via the website.

    No partner-API token needed anywhere in this flow (added 2026-08-15,
    closing the gap Oliver flagged: PQ triggering was already web-only, but
    the "wait until ready" + download steps used to always go through the
    official API even when triggering didn't). Sequence:

    1. ``trigger.trigger_pqs_by_guid()`` — already web-only before this
       effort — triggers each PQ (or reports it as already run/scheduled/
       rate-limited).
    2. ``trigger.wait_for_pq_generation_web()`` polls the "Ready for
       Download" tab's date-only last-generated column until each triggered
       PQ shows up fresh — sufficient because GC only lets a PQ run once
       per PST day.
    3. ``download_and_import_pq_web()`` per PQ once ready.

    ``tag_names`` is shared across every selected PQ (unlike the official
    ``bulk_trigger_and_download()``'s per-reference-code ``tag_map`` — this
    flow's UI only offers one tags field for the whole selection, see
    ``views/pq.py``'s ``pq_management_web``).

    Returns ``{"created", "updated", "locked", "errors", "trigger_errors"}``
    — same ``created``/``updated``/``locked``/``errors`` shape
    ``download_and_import_pq_web()`` and the official queue return, so the
    existing ``_pq_web_status.html`` partial renders either without changes.
    """
    from datetime import datetime, timezone
    from geocaches.pq.trigger import trigger_pqs_by_guid, wait_for_pq_generation_web

    names = dict(names or {})
    if task_info:
        task_info.phase = f"Triggering {len(guids)} PQ(s)"
        task_info.total = len(guids) * 3 or 1

    since = datetime.now(timezone.utc)
    trigger_results = trigger_pqs_by_guid(guids)

    if task_info:
        task_info.completed = len(guids)

    wait_guids, ready_guids = [], []
    trigger_errors = []
    for r in trigger_results:
        guid = r["guid"]
        name = r.get("name") or names.get(guid, guid)
        names[guid] = name
        if r["status"] in ("triggered", "already_scheduled"):
            wait_guids.append(guid)
        elif r["status"] == "already_ran":
            ready_guids.append(guid)
        else:
            trigger_errors.append({"pq_name": name, "guid": guid, "status": r["status"]})

    completed_map = {}
    if wait_guids:
        if task_info:
            task_info.phase = f"Waiting for {len(wait_guids)} PQ(s) to generate"
        completed_map = wait_for_pq_generation_web(wait_guids, since, task_info=task_info)
    for guid in ready_guids:
        completed_map[guid] = True

    if task_info:
        task_info.completed = len(guids) * 2
        task_info.phase = "Downloading"

    created = updated = locked = 0
    errors = []
    to_download = wait_guids + ready_guids
    for i, guid in enumerate(to_download):
        name = names.get(guid, guid)
        if not completed_map.get(guid):
            errors.append(f"{name}: generation timed out")
            continue
        try:
            result = download_and_import_pq_web(guid, name, tag_names=tag_names)
            created += result.get("created", 0)
            updated += result.get("updated", 0)
            locked += result.get("locked", 0)
            errors.extend(result.get("errors", []))
        except Exception as exc:
            errors.append(f"{name}: {exc}")
        if task_info:
            task_info.completed = len(guids) * 2 + i + 1

    for te in trigger_errors:
        errors.append(f"{te['pq_name']}: {te['status']}")

    if task_info:
        task_info.completed = task_info.total
        task_info.phase = "Done"

    summary = {
        "created": created, "updated": updated, "locked": locked,
        "errors": errors[:20], "trigger_errors": trigger_errors,
    }
    if task_info:
        task_info.result = summary
    return summary


# ---------------------------------------------------------------------------
# Trigger functions
# ---------------------------------------------------------------------------

def bulk_trigger(items, task_info=None):
    """Trigger the selected PQs (items: list of (guid, name)).

    Returns a trigger-style result the template renders under the "triggered"
    branch, then auto-refreshes the list while generation runs.
    """
    from geocaches.pq.trigger import trigger_pqs_by_guid

    guids = [guid for guid, _name in items if guid]
    if task_info:
        task_info.phase = f"Triggering {len(guids)} PQ(s)"
        task_info.total = 1

    results = trigger_pqs_by_guid(guids)

    if task_info:
        task_info.completed = 1
        task_info.phase = "Done"

    return {
        "status": "triggered",
        "pq_name": "Selected PQs",
        "results": results,
    }


def bulk_trigger_and_download(items, tag_map=None, task_info=None):
    """Trigger selected PQs, wait for generation, then enqueue downloads.

    items: list of dicts {"ref", "guid", "name"}.  Identifiers are taken
    straight from the selected rows — no website↔API name matching — so this
    can't silently no-op the way the old pattern path did.

    Returns {"results", "queue_task_id", ...}; the view follows the queue task
    for the real created/updated counts.
    """
    from datetime import datetime, timezone
    from geocaches.pq.trigger import trigger_pqs_by_guid, wait_for_pq_generation

    items = [it for it in items if it.get("guid")]
    guid_to_ref = {it["guid"]: it.get("ref", "") for it in items}
    guid_to_name = {it["guid"]: it.get("name", it.get("ref", "")) for it in items}

    if task_info:
        task_info.phase = f"Triggering {len(items)} PQ(s)"
        task_info.total = len(items) * 3 or 1

    since = datetime.now(timezone.utc)
    trigger_results = trigger_pqs_by_guid([it["guid"] for it in items])

    if task_info:
        task_info.completed = len(items)

    # Refs that need to generate vs. already fresh today.
    wait_refs, ready_refs = [], []
    result_rows = []
    for r in trigger_results:
        ref = guid_to_ref.get(r["guid"], "")
        name = r.get("name") or guid_to_name.get(r["guid"], ref)
        if r["status"] in ("triggered", "already_scheduled") and ref:
            wait_refs.append(ref)
        elif r["status"] == "already_ran" and ref:
            ready_refs.append(ref)
        else:
            result_rows.append({"pq_name": name, "reference_code": ref, "error": r["status"]})

    completed_map = {}
    if wait_refs:
        if task_info:
            task_info.phase = f"Waiting for {len(wait_refs)} PQ(s) to generate"
        completed_map = wait_for_pq_generation(
            wait_refs, since, task_info=task_info,
        )
    for ref in ready_refs:
        completed_map[ref] = True

    if task_info:
        task_info.completed = len(items) * 2
        task_info.phase = "Queueing downloads"

    queue_task_id = None
    ref_to_name = {it.get("ref", ""): it.get("name", "") for it in items}
    for ref in wait_refs + ready_refs:
        name = ref_to_name.get(ref, ref)
        if not completed_map.get(ref):
            result_rows.append({"pq_name": name, "reference_code": ref,
                                "error": "Generation timed out"})
            continue
        tag_names = tag_map.get(ref) if tag_map else None
        queue_task_id = enqueue_pq_download(ref, name, tag_names=tag_names)
        result_rows.append({"pq_name": name, "reference_code": ref, "status": "queued"})

    if task_info:
        task_info.completed = task_info.total
        task_info.phase = "Queued for download"

    return {
        "results": result_rows,
        "queue_task_id": queue_task_id,
        "total_created": 0,
        "total_updated": 0,
    }


def bulk_delete(items, task_info=None):
    """Delete the selected PQs on geocaching.com (items: list of (delete_id, name)).

    delete_id is the website's numeric PQ id (checkbox value), not the GUID.
    """
    from geocaches.pq.trigger import delete_pqs

    pairs = [(str(did), name) for did, name in items if str(did).strip()]
    if task_info:
        task_info.phase = f"Deleting {len(pairs)} PQ(s)"
        task_info.total = 1

    name_by_id = {did: name for did, name in pairs}
    outcome = delete_pqs([did for did, _ in pairs])
    deleted = set(outcome["deleted"])

    # Force the website-status cache to refetch so the deleted PQs pick up their
    # "Deleted" chip on the next page load.
    invalidate_web_status()

    results = [
        {"name": name_by_id.get(did, did),
         "status": "deleted" if did in deleted else "not deleted"}
        for did, _ in pairs
    ]

    if task_info:
        task_info.completed = 1
        task_info.phase = "Done"

    return {
        "status": "deleted",
        "deleted_count": len(deleted),
        "requested_count": len(pairs),
        "results": results,
    }


def trigger_pqs_by_pattern(pattern, task_info=None):
    from geocaches.pq.trigger import trigger_pqs_by_name, get_pq_web_status

    if task_info:
        task_info.phase = "Fetching PQ list from website"
        task_info.total = 2

    web_pqs, _summary = get_pq_web_status()
    pattern_lower = pattern.lower()
    matching = [pq for pq in web_pqs if pattern_lower in pq["name"].lower()]

    if not matching:
        available = [pq["name"] for pq in web_pqs]
        return {
            "status": "no_match",
            "error": f"No PQs match '{pattern}'. Available: {', '.join(available)}",
        }

    if task_info:
        task_info.completed = 1
        task_info.phase = f"Triggering {len(matching)} PQ(s)"

    names = [pq["name"] for pq in matching]
    results = trigger_pqs_by_name(names)

    if task_info:
        task_info.completed = 2
        task_info.phase = "Done"

    triggered = [r["name"] for r in results if r["status"] == "triggered"]
    skipped = [r for r in results if r["status"] != "triggered"]

    return {
        "status": "triggered",
        "pq_name": f"Pattern: {pattern}",
        "triggered": triggered,
        "skipped": skipped,
        "results": results,
    }


def trigger_and_download_by_pattern(pattern, tag_map=None, task_info=None):
    """Trigger PQs matching a name pattern, wait for generation, then download.

    A PQ only shows up in the GC API's pocket-query list once it has a recent
    generation — a definition that's never been triggered (or hasn't run
    recently) simply isn't in there, so unlike ``bulk_trigger_and_download``
    (which only ever operates on rows the API already knows about, since
    that's where the checkbox rows come from), this triggers by GUID first
    and discovers each PQ's referenceCode afterwards by polling the API and
    matching on name (normalized — see ``trigger._normalize_pq_name()``).
    GC's batch processor can take anywhere from seconds to hours, so the wait
    uses a long grace period with retries.

    Each PQ's download is enqueued the moment its own name resolves (via
    ``wait_for_pq_generation_by_name``'s ``on_resolved`` callback), not after
    the whole batch resolves — a straggler that never matches (or is simply
    slow) no longer holds up ones that are already ready (2026-09-05: a
    5-PQ pattern trigger sat blocked for its full 4-hour timeout because 2 of
    the 5 names never matched the API's name field, even though the other 3
    were ready within a minute).
    """
    from datetime import datetime, timezone
    from geocaches.pq.trigger import (
        get_pq_web_status, trigger_pqs_by_guid, wait_for_pq_generation_by_name,
    )

    if task_info:
        task_info.phase = "Fetching PQ list from website"
        task_info.total = 3

    web_pqs, _summary = get_pq_web_status()
    pattern_lower = pattern.lower()
    matching_web = {
        pq["name"]: pq
        for pq in web_pqs
        if pattern_lower in pq["name"].lower() and pq.get("guid")
    }

    if not matching_web:
        available = [pq["name"] for pq in web_pqs]
        return {
            "status": "no_match",
            "error": f"No PQs match '{pattern}'. Available: {', '.join(available)}",
        }

    if task_info:
        task_info.completed = 1
        task_info.phase = f"Triggering {len(matching_web)} PQ(s)"

    since = datetime.now(timezone.utc)
    guid_to_name = {pq["guid"]: name for name, pq in matching_web.items()}
    trigger_results = trigger_pqs_by_guid(list(guid_to_name))

    # Freshly triggered/scheduled PQs must show a lastUpdatedDateUtc at or
    # after `since` to count as ready (so a stale hit isn't mistaken for the
    # run we just kicked off).  A PQ that already ran earlier today can't be
    # re-triggered, so its existing (older) data is what we want — accept it
    # as soon as it appears, at any recency.
    since_by_name: dict[str, datetime | None] = {}
    result_rows = []
    for r in trigger_results:
        name = guid_to_name.get(r["guid"], r.get("name", ""))
        if r["status"] in ("triggered", "already_scheduled"):
            since_by_name[name] = since
        elif r["status"] == "already_ran":
            since_by_name[name] = None
        else:
            result_rows.append({"pq_name": name, "reference_code": "", "error": r["status"]})

    if task_info:
        task_info.completed = 2

    queue_task_id = None

    def _on_resolved(name, ref):
        nonlocal queue_task_id
        tag_names = tag_map.get(ref) if tag_map else None
        queue_task_id = enqueue_pq_download(ref, name, tag_names=tag_names)

    resolved: dict[str, str] = {}
    if since_by_name:
        if task_info:
            task_info.phase = f"Waiting for {len(since_by_name)} PQ(s) to generate"
        resolved = wait_for_pq_generation_by_name(
            since_by_name, task_info=task_info, on_resolved=_on_resolved,
        )

    for name in since_by_name:
        if name in resolved:
            result_rows.append({"pq_name": name, "reference_code": resolved[name], "status": "queued"})
        else:
            result_rows.append({"pq_name": name, "reference_code": "", "error": "Generation timed out"})

    if not resolved:
        return {
            "status": "no_match",
            "error": (
                f"Found PQs matching '{pattern}' on the website and triggered them, "
                "but none appeared in the API within the wait window."
            ),
            "results": result_rows,
        }

    if task_info:
        task_info.completed = 3
        task_info.phase = "Done"

    return {
        "results": result_rows,
        "queue_task_id": queue_task_id,
        "total_created": 0,
        "total_updated": 0,
    }


def _start_batch_enrich(since):
    """Start a single enrichment pass for all caches imported since `since`.

    Thin wrapper around services._start_auto_enrich; kept as a public-ish name
    so the queue worker can fire the batch enrichment after the queue drains.
    """
    from geocaches.services import _start_auto_enrich
    _start_auto_enrich(since)


def download_all_fresh(pq_list, tag_map=None, task_info=None):
    """Enqueue all PQs that ran today and have not yet been imported.

    pq_list entries must be annotated with already_ran and imported flags
    (done by the view before submitting the task).  All downloads route through
    the global PQ queue worker, which fires a single enrichment pass after the
    queue drains — avoiding SQLite contention from concurrent import + enrich.
    """
    fresh = [
        pq for pq in pq_list
        if pq.get("already_ran") and pq.get("lastUpdatedDateUtc") and not pq.get("imported")
    ]
    if not fresh:
        if task_info:
            task_info.phase = "No fresh unimported PQs"
            task_info.total = 0
            task_info.completed = 0
        return {"results": [], "total_created": 0, "total_updated": 0}

    logger.info(
        "--- PQ bulk download enqueue: %d PQ(s): %s ---",
        len(fresh),
        ", ".join(pq.get("name", pq["referenceCode"]) for pq in fresh),
    )

    if task_info:
        task_info.total = len(fresh)
        task_info.completed = 0
        task_info.phase = "Queueing downloads"

    enqueue_results = []
    queue_task_id = None
    for i, pq in enumerate(fresh):
        ref = pq["referenceCode"]
        name = pq.get("name", ref)
        tag_names = tag_map.get(ref) if tag_map else None
        queue_task_id = enqueue_pq_download(ref, name, tag_names=tag_names)
        enqueue_results.append({
            "pq_name": name, "reference_code": ref, "status": "queued",
        })
        if task_info:
            task_info.completed = i + 1

    if task_info:
        task_info.phase = "Queued for download"

    return {
        "results": enqueue_results,
        "queue_task_id": queue_task_id,
        "total_created": 0,
        "total_updated": 0,
    }
