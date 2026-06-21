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
        from geocaches.pq_trigger import get_pq_web_status
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
# downloads.  Items are (reference_code, name, tag_names) tuples.
# ---------------------------------------------------------------------------

_download_queue: deque[tuple[str, str, list[str] | None]] = deque()
_queue_lock = threading.Lock()
_queue_task_id: str | None = None  # ID of the currently running queue worker


def enqueue_pq_download(reference_code, name, tag_names=None):
    """Add a PQ to the download queue and ensure the worker task is running.

    Returns the task ID of the queue worker.
    """
    from geocaches.tasks import submit_task, get_task

    with _queue_lock:
        _download_queue.append((reference_code, name, tag_names))

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
            ref, name, tag_names = _download_queue.popleft()
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
            _mark_pq_imported(ref)
            retry_counts.pop(ref, None)
            processed += 1

        except OperationalError as exc:
            if "database is locked" in str(exc) and retry < 3:
                retry_counts[ref] = retry + 1
                with _queue_lock:
                    _download_queue.append((ref, name, tag_names))
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


def get_imported_pqs() -> dict[str, str]:
    """Return {reference_code: iso_timestamp} of imported PQs."""
    from preferences.models import UserPreference
    return UserPreference.get("pq_imported", {})


# ---------------------------------------------------------------------------
# Core download/import
# ---------------------------------------------------------------------------

def list_pocket_queries():
    from gcprivate.gc_client import GCClient
    client = GCClient()
    return client.get_pocket_queries()


def _do_download_and_import(reference_code, name, tag_names=None, task_info=None,
                            auto_enrich=True):
    from gcprivate.gc_client import GCClient
    from geocaches.services import import_and_enrich

    if task_info:
        task_info.phase = f"Downloading {name}"
        task_info.total = 2

    logger.info("PQ download start: %s (%s)", name, reference_code)
    client = GCClient()
    data = client.download_pocket_query(reference_code)
    logger.info("PQ download complete: %s (%s) — %d bytes received", name, reference_code, len(data))

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


def download_and_import_pq(reference_code, name, tag_names=None, task_info=None):
    """Public API: enqueue a PQ download via the sequential queue."""
    queue_task_id = enqueue_pq_download(reference_code, name, tag_names=tag_names)
    return {
        "pq_name": name,
        "reference_code": reference_code,
        "status": "queued",
        "queue_task_id": queue_task_id,
    }


# ---------------------------------------------------------------------------
# Trigger functions
# ---------------------------------------------------------------------------

def bulk_trigger(items, task_info=None):
    """Trigger the selected PQs (items: list of (guid, name)).

    Returns a trigger-style result the template renders under the "triggered"
    branch, then auto-refreshes the list while generation runs.
    """
    from geocaches.pq_trigger import trigger_pqs_by_guid

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
    from geocaches.pq_trigger import trigger_pqs_by_guid, wait_for_pq_generation

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
            wait_refs, since, poll_interval=20.0, timeout=900.0, task_info=task_info,
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
    from geocaches.pq_trigger import delete_pqs

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
    from geocaches.pq_trigger import trigger_pqs_by_name, get_pq_web_status

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

    Mirrors ``bulk_trigger_and_download`` but driven by a name substring rather
    than an explicit selection.  Fetches both the website PQ list (for GUIDs /
    trigger URLs) and the API list (for reference codes), matches by name, then
    delegates to ``bulk_trigger_and_download``.
    """
    from geocaches.pq_trigger import get_pq_web_status

    if task_info:
        task_info.phase = "Fetching PQ lists"
        task_info.total = 1

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

    api_pqs = list_pocket_queries()
    api_by_name = {pq.get("name", ""): pq for pq in api_pqs}

    items = [
        {"ref": api_by_name[name]["referenceCode"], "guid": web_pq["guid"], "name": name}
        for name, web_pq in matching_web.items()
        if name in api_by_name and api_by_name[name].get("referenceCode")
    ]

    if not items:
        return {
            "status": "no_match",
            "error": (
                f"Found PQs matching '{pattern}' on the website but could not map "
                "them to API reference codes."
            ),
        }

    if task_info:
        task_info.completed = 1

    return bulk_trigger_and_download(items, tag_map=tag_map, task_info=task_info)


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
