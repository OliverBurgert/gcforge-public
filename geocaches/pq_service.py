import logging
import os
import tempfile
import threading
from collections import deque

logger = logging.getLogger(__name__)

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
    """Process the download queue sequentially."""
    results = []
    total_created = 0
    total_updated = 0
    processed = 0

    while True:
        with _queue_lock:
            if not _download_queue:
                break
            ref, name, tag_names = _download_queue.popleft()
            remaining = len(_download_queue)

        processed += 1
        queue_info = f" (+{remaining} queued)" if remaining else ""

        if task_info:
            task_info.phase = f"Downloading {name}{queue_info}"
            task_info.total = processed + remaining
            task_info.completed = processed - 1

        try:
            result = _do_download_and_import(ref, name, tag_names=tag_names,
                                              auto_enrich=True)
            results.append(result)
            total_created += result.get("created", 0)
            total_updated += result.get("updated", 0)

            # Track import
            _mark_pq_imported(ref)
        except Exception as exc:
            logger.warning("Failed to download/import PQ %s: %s", ref, exc)
            results.append({"pq_name": name, "reference_code": ref, "error": str(exc)})

        if task_info:
            task_info.completed = processed

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
    from geocaches.sync.gc_client import GCClient
    client = GCClient()
    return client.get_pocket_queries()


def _do_download_and_import(reference_code, name, tag_names=None, task_info=None,
                            auto_enrich=True):
    from geocaches.sync.gc_client import GCClient
    from geocaches.services import import_and_enrich

    if task_info:
        task_info.phase = f"Downloading {name}"
        task_info.total = 2

    client = GCClient()
    data = client.download_pocket_query(reference_code)

    tmp = tempfile.NamedTemporaryFile(
        suffix=".zip", prefix=f"pq_{reference_code}_", delete=False,
    )
    try:
        tmp.write(data)
        tmp.close()

        if task_info:
            task_info.completed = 1
            task_info.phase = f"Importing {name}"

        result = import_and_enrich("unified_gpx", tmp.name, tag_names,
                                   auto_enrich=auto_enrich)

        if task_info:
            task_info.completed = 2
            task_info.phase = "Done"

        return {
            "pq_name": name,
            "reference_code": reference_code,
            "created": getattr(result, "created", 0),
            "updated": getattr(result, "updated", 0),
            "locked": getattr(result, "locked", 0),
            "errors": getattr(result, "errors", []),
        }
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass


def download_and_import_pq(reference_code, name, tag_names=None, task_info=None):
    result = _do_download_and_import(reference_code, name, tag_names=tag_names,
                                      task_info=task_info, auto_enrich=True)
    _mark_pq_imported(reference_code)
    return result


# ---------------------------------------------------------------------------
# Trigger functions
# ---------------------------------------------------------------------------

def trigger_pq_run(guid, name, task_info=None):
    from geocaches.pq_trigger import trigger_pq

    if task_info:
        task_info.phase = f"Triggering {name}"
        task_info.total = 1

    triggered_name = trigger_pq(guid)

    if task_info:
        task_info.completed = 1
        task_info.phase = "Triggered"

    return {"pq_name": triggered_name, "status": "triggered"}


def trigger_and_download_pq(reference_code, guid, name, tag_names=None, task_info=None):
    from datetime import datetime, timezone
    from geocaches.pq_trigger import trigger_pq, wait_for_pq_generation

    if task_info:
        task_info.phase = f"Triggering {name}"
        task_info.total = 3

    since = datetime.now(timezone.utc)
    trigger_pq(guid)

    if task_info:
        task_info.completed = 1
        task_info.phase = f"Waiting for {name} to generate"

    completed = wait_for_pq_generation(
        [reference_code], since, poll_interval=20.0, timeout=900.0,
        task_info=task_info,
    )

    if not completed.get(reference_code):
        return {
            "pq_name": name,
            "reference_code": reference_code,
            "error": "Timed out waiting for PQ generation",
        }

    if task_info:
        task_info.completed = 2
        task_info.phase = f"Downloading {name}"

    result = _do_download_and_import(reference_code, name, tag_names=tag_names,
                                      auto_enrich=False)
    _mark_pq_imported(reference_code)

    if task_info:
        task_info.completed = 3
        task_info.phase = "Done"

    return result


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
    from datetime import datetime, timezone
    from geocaches.pq_trigger import trigger_pqs_by_name, get_pq_web_status, wait_for_pq_generation

    if task_info:
        task_info.phase = "Fetching PQ list"

    web_pqs, _summary = get_pq_web_status()
    api_pqs = list_pocket_queries()
    pattern_lower = pattern.lower()

    matching_web = [pq for pq in web_pqs if pattern_lower in pq["name"].lower()]
    if not matching_web:
        return {
            "status": "no_match",
            "error": f"No PQs match '{pattern}'.",
        }

    api_by_name = {pq["name"]: pq for pq in api_pqs}

    names = [pq["name"] for pq in matching_web]
    since = datetime.now(timezone.utc)

    if task_info:
        task_info.phase = f"Triggering {len(names)} PQ(s)"
        task_info.total = len(names) * 3

    trigger_results = trigger_pqs_by_name(names)
    # PQs that need to generate — wait for them before downloading
    needs_wait_names = [r["name"] for r in trigger_results if r["status"] in ("triggered", "already_scheduled")]
    # PQs that already ran today — already have fresh data, download immediately
    already_ran_names = [r["name"] for r in trigger_results if r["status"] == "already_ran"]

    if task_info:
        task_info.completed = len(names)

    wait_refs = []
    for name in needs_wait_names:
        api_pq = api_by_name.get(name)
        if api_pq:
            wait_refs.append(api_pq["referenceCode"])

    ready_refs = []
    for name in already_ran_names:
        api_pq = api_by_name.get(name)
        if api_pq:
            ready_refs.append(api_pq["referenceCode"])

    all_refs = wait_refs + ready_refs
    if not all_refs:
        return {
            "results": [{"pq_name": r["name"], "reference_code": "", "error": r["status"]}
                        for r in trigger_results],
            "total_created": 0,
            "total_updated": 0,
            "error": "No PQs could be matched to API reference codes for download.",
        }

    completed_map: dict[str, bool] = {}

    if wait_refs:
        if task_info:
            task_info.phase = f"Waiting for {len(wait_refs)} PQ(s) to generate"

        completed_map = wait_for_pq_generation(
            wait_refs, since, poll_interval=20.0, timeout=900.0, task_info=task_info,
        )

    # already_ran PQs are immediately ready
    for ref in ready_refs:
        completed_map[ref] = True

    if task_info:
        task_info.completed = len(names) * 2

    import_results = []
    total_created = 0
    total_updated = 0
    for i, ref in enumerate(all_refs):
        if not completed_map.get(ref):
            name = next((n for n, pq in api_by_name.items() if pq["referenceCode"] == ref), ref)
            import_results.append({"pq_name": name, "reference_code": ref, "error": "Generation timed out"})
            continue

        api_pq = next((pq for pq in api_pqs if pq["referenceCode"] == ref), {})
        name = api_pq.get("name", ref)
        tag_names = tag_map.get(ref) if tag_map else None

        if task_info:
            task_info.phase = f"Downloading {name} ({i+1}/{len(all_refs)})"

        try:
            result = _do_download_and_import(ref, name, tag_names=tag_names,
                                                      auto_enrich=False)
            import_results.append(result)
            total_created += result.get("created", 0)
            total_updated += result.get("updated", 0)
            _mark_pq_imported(ref)
        except Exception as exc:
            import_results.append({"pq_name": name, "reference_code": ref, "error": str(exc)})

        if task_info:
            task_info.completed = len(names) * 2 + i + 1

    return {
        "results": import_results,
        "total_created": total_created,
        "total_updated": total_updated,
    }


def download_all_ready(pq_list, tag_map=None, task_info=None):
    from geocaches.sync.gc_client import GCClient
    from geocaches.services import import_and_enrich

    ready = [pq for pq in pq_list if pq.get("lastUpdatedDateUtc")]
    if not ready:
        if task_info:
            task_info.phase = "No ready PQs"
            task_info.total = 0
            task_info.completed = 0
        return {"results": [], "total_created": 0, "total_updated": 0}

    if task_info:
        task_info.total = len(ready) * 2
        task_info.completed = 0

    client = GCClient()
    results = []
    total_created = 0
    total_updated = 0

    for i, pq in enumerate(ready):
        ref = pq["referenceCode"]
        name = pq.get("name", ref)

        if task_info:
            if task_info.cancel_event.is_set():
                break
            task_info.phase = f"Downloading {name} ({i+1}/{len(ready)})"

        try:
            data = client.download_pocket_query(ref)
        except Exception as exc:
            logger.warning("Failed to download PQ %s: %s", ref, exc)
            results.append({"pq_name": name, "reference_code": ref, "error": str(exc)})
            if task_info:
                task_info.completed += 2
            continue

        tmp = tempfile.NamedTemporaryFile(
            suffix=".zip", prefix=f"pq_{ref}_", delete=False,
        )
        try:
            tmp.write(data)
            tmp.close()

            if task_info:
                task_info.completed = i * 2 + 1
                task_info.phase = f"Importing {name} ({i+1}/{len(ready)})"

            tag_names = None
            if tag_map and ref in tag_map:
                tag_names = tag_map[ref]

            result = import_and_enrich("unified_gpx", tmp.name, tag_names)

            created = getattr(result, "created", 0)
            updated = getattr(result, "updated", 0)
            total_created += created
            total_updated += updated

            results.append({
                "pq_name": name,
                "reference_code": ref,
                "created": created,
                "updated": updated,
                "locked": getattr(result, "locked", 0),
                "errors": getattr(result, "errors", []),
            })
            _mark_pq_imported(ref)
        except Exception as exc:
            logger.warning("Failed to import PQ %s: %s", ref, exc)
            results.append({"pq_name": name, "reference_code": ref, "error": str(exc)})
        finally:
            try:
                os.unlink(tmp.name)
            except OSError:
                pass

        if task_info:
            task_info.completed = (i + 1) * 2

    return {
        "results": results,
        "total_created": total_created,
        "total_updated": total_updated,
    }
