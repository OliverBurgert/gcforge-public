"""
Background task for bulk API updates (light refresh, log fetching).
"""
import logging
from geocaches.tasks import submit_task, get_task, cancel_task, TaskState

logger = logging.getLogger(__name__)

_current_task_id: str | None = None


def start_update(queryset, action: str, **kwargs) -> bool:
    """Start a bulk update in a background thread. Returns False if already running."""
    global _current_task_id
    if _current_task_id:
        info = get_task(_current_task_id)
        if info and info["state"] == TaskState.RUNNING.value:
            return False

    _current_task_id = submit_task(
        f"Update ({action})",
        _run_update,
        queryset, action, kwargs,
    )
    if _current_task_id:
        from geocaches.tasks.runner import _registry, _lock
        with _lock:
            info_obj = _registry.get(_current_task_id)
        if info_obj:
            info_obj.total = queryset.count()
            info_obj.phase = "starting"
    return True


def _run_update(qs, action: str, kwargs: dict, *, task_info):
    """Main update dispatcher — runs in background thread."""
    if action == "light_update":
        return _light_update(qs, task_info)
    elif action == "oc_link_refresh":
        return _oc_link_refresh(qs, task_info)
    elif action == "gc_recent_logs":
        return _gc_recent_logs(qs, task_info)
    elif action == "gc_all_logs":
        return _gc_all_logs(qs, task_info)
    elif action == "oc_logs":
        count = kwargs.get("count", 50)
        return _oc_fetch_logs(qs, count, task_info)
    elif action == "verify_ftf":
        return _verify_ftf(qs, task_info)
    else:
        task_info.phase = f"unknown action: {action}"
        return {"error": f"Unknown action: {action}"}


def _light_update(qs, task_info):
    """Light-mode refresh: update all light fields for GC + OC caches."""
    from geocaches.services import save_geocache
    from geocaches.sync.base import SyncMode

    gc_codes = list(qs.filter(gc_code__startswith="GC")
                     .values_list("gc_code", flat=True))
    oc_caches = list(qs.exclude(oc_code="").values_list("oc_code", "pk"))
    oc_codes = [c[0] for c in oc_caches]

    skipped_al = qs.filter(al_code__gt="").count()
    total = len(gc_codes) + len(oc_codes)
    task_info.total = total
    completed = 0
    updated = 0

    logger.info("Light update starting: %d GC + %d OC caches", len(gc_codes), len(oc_codes))
    task_info.phase = f"Starting: {len(gc_codes)} GC + {len(oc_codes)} OC caches"

    # --- GC light update ---
    if gc_codes:
        task_info.phase = f"GC light update ({len(gc_codes)} caches)"
        try:
            from geocaches.sync.gc_client import GCClient
            client = GCClient()
            results = client.get_caches(gc_codes, SyncMode.LIGHT)
            for data in results:
                if task_info.cancel_event.is_set():
                    break
                try:
                    kw = dict(data)
                    kw["fields"] = dict(data["fields"])
                    save_geocache(**kw)
                    updated += 1
                except Exception as exc:
                    logger.warning("Light update failed for %s: %s",
                                   data.get("gc_code", "?"), exc)
                completed += 1
                task_info.completed = completed
        except Exception as exc:
            logger.error("GC light update batch failed: %s", exc)
            completed += len(gc_codes)
            task_info.completed = completed

    # --- OC light update ---
    if oc_codes and not task_info.cancel_event.is_set():
        task_info.phase = f"OC light update ({len(oc_codes)} caches)"
        try:
            from geocaches.sync.oc_client import OCClient
            from accounts.models import UserAccount
            # Group by platform
            platform_codes: dict[str, list[str]] = {}
            for code in oc_codes:
                prefix = code[:2].upper()
                from geocaches.models import Geocache
                plat = Geocache._OC_PREFIX_TO_PLATFORM.get(prefix, "oc_de")
                platform_codes.setdefault(plat, []).append(code)

            for platform, codes in platform_codes.items():
                if task_info.cancel_event.is_set():
                    break
                acct = UserAccount.objects.filter(platform=platform).first()
                user_id = acct.user_id if acct else ""
                client = OCClient(platform=platform, user_id=user_id)
                results = client.get_caches(codes, SyncMode.LIGHT)
                for data in results:
                    if task_info.cancel_event.is_set():
                        break
                    try:
                        kw = dict(data)
                        kw["fields"] = dict(data["fields"])
                        save_geocache(**kw)
                        updated += 1
                    except Exception as exc:
                        logger.warning("OC light update failed for %s: %s",
                                       data.get("oc_code", "?"), exc)
                    completed += 1
                    task_info.completed = completed
        except Exception as exc:
            logger.error("OC light update batch failed: %s", exc)

    failed = total - completed if completed < total else (completed - updated)
    summary = f"Updated {updated} cache(es)"
    if skipped_al:
        summary += f", skipped {skipped_al} Adventure Lab(s)"
    done_phase = f"Done: {updated} updated, {total - updated} unchanged"
    if skipped_al:
        done_phase += f", {skipped_al} AL skipped"
    logger.info("Light update done: %s", done_phase)
    task_info.phase = done_phase
    return {"summary": summary, "updated": updated, "skipped_al": skipped_al}


def _oc_link_refresh(qs, task_info):
    """Fetch OC data for fused caches, update related_gc_code and auto_linked.

    Only hits the OC API — skips GC entirely. Deduplicates OC codes so fused
    caches (which carry both gc_code and oc_code) don't cause OKAPI 400 errors.
    """
    from geocaches.sync.base import SyncMode
    from geocaches.services import save_geocache

    # Deduplicate: each OC code must appear exactly once
    seen = set()
    oc_pairs = []
    for oc_code, pk in qs.exclude(oc_code="").values_list("oc_code", "pk"):
        if oc_code not in seen:
            seen.add(oc_code)
            oc_pairs.append(oc_code)

    total = len(oc_pairs)
    task_info.total = total
    task_info.phase = f"OC link refresh ({total} caches)"
    completed = updated = 0

    if not oc_pairs:
        task_info.phase = "Done: no OC caches"
        return {"updated": 0}

    try:
        from geocaches.sync.oc_client import OCClient
        from geocaches.models import Geocache
        from accounts.models import UserAccount

        platform_codes: dict[str, list[str]] = {}
        for code in oc_pairs:
            prefix = code[:2].upper()
            plat = Geocache._OC_PREFIX_TO_PLATFORM.get(prefix, "oc_de")
            platform_codes.setdefault(plat, []).append(code)

        for platform, codes in platform_codes.items():
            if task_info.cancel_event.is_set():
                break
            acct = UserAccount.objects.filter(platform=platform).first()
            user_id = acct.user_id if acct else ""
            client = OCClient(platform=platform, user_id=user_id)
            results = client.get_caches(codes, SyncMode.LIGHT)
            for data in results:
                if task_info.cancel_event.is_set():
                    break
                try:
                    kw = dict(data)
                    kw["fields"] = dict(data["fields"])
                    save_geocache(**kw)
                    updated += 1
                except Exception as exc:
                    logger.warning("OC link refresh failed for %s: %s",
                                   data.get("oc_code", "?"), exc)
                completed += 1
                task_info.completed = completed
    except Exception as exc:
        logger.error("OC link refresh batch failed: %s", exc)

    done_phase = f"Done: {updated} updated, {total - updated} unchanged"
    logger.info("OC link refresh done: %s", done_phase)
    task_info.phase = done_phase
    return {"updated": updated}


def _gc_recent_logs(qs, task_info):
    """Fetch 50 most recent GC logs for all GC caches in the queryset."""
    from geocaches.sync.gc_client import GCClient
    from geocaches.sync.log_fetch import fetch_recent_gc_logs

    gc_codes = list(qs.filter(gc_code__startswith="GC")
                     .values_list("gc_code", flat=True))
    task_info.total = len(gc_codes)
    task_info.phase = f"Fetching recent GC logs ({len(gc_codes)} caches)"
    logger.info("GC recent log fetch starting: %d caches", len(gc_codes))
    total_new = 0
    client = GCClient()

    for i, code in enumerate(gc_codes):
        if task_info.cancel_event.is_set():
            break
        try:
            saved, _ = fetch_recent_gc_logs(client, code, count=50)
            total_new += saved
        except Exception as exc:
            logger.warning("Recent log fetch failed for %s: %s", code, exc)
        task_info.completed = i + 1

    done_phase = f"Done: {total_new} new log(s) from {len(gc_codes)} caches"
    logger.info("GC recent log fetch done: %s", done_phase)
    task_info.phase = done_phase
    return {"summary": f"Fetched {total_new} recent log(s)", "new_logs": total_new}


def _gc_all_logs(qs, task_info):
    """Fetch ALL GC logs for all GC caches in the queryset."""
    from geocaches.sync.gc_client import GCClient
    from geocaches.sync.log_fetch import fetch_all_gc_logs

    gc_codes = list(qs.filter(gc_code__startswith="GC")
                     .values_list("gc_code", flat=True))
    task_info.total = len(gc_codes)
    task_info.phase = f"Fetching all GC logs ({len(gc_codes)} caches)"
    logger.info("GC all-logs fetch starting: %d caches", len(gc_codes))
    total_new = 0
    client = GCClient()

    for i, code in enumerate(gc_codes):
        if task_info.cancel_event.is_set():
            break
        try:
            saved = fetch_all_gc_logs(client, code)
            total_new += saved
        except Exception as exc:
            logger.warning("All-logs fetch failed for %s: %s", code, exc)
        task_info.completed = i + 1

    done_phase = f"Done: {total_new} new log(s) from {len(gc_codes)} caches"
    logger.info("GC all-logs fetch done: %s", done_phase)
    task_info.phase = done_phase
    return {"summary": f"Fetched {total_new} log(s) (all)", "new_logs": total_new}


def _oc_fetch_logs(qs, count: int, task_info):
    """Fetch N newest OC logs for all OC caches in the queryset."""
    from geocaches.sync.oc_client import OCClient
    from geocaches.sync.log_fetch import fetch_oc_logs
    from accounts.models import UserAccount
    from geocaches.models import Geocache

    oc_codes = list(qs.exclude(oc_code="").values_list("oc_code", flat=True))
    task_info.total = len(oc_codes)
    task_info.phase = f"Fetching OC logs ({len(oc_codes)} caches)"
    logger.info("OC log fetch starting: %d caches", len(oc_codes))
    total_new = 0

    # Group by platform
    platform_codes: dict[str, list[str]] = {}
    for code in oc_codes:
        prefix = code[:2].upper()
        plat = Geocache._OC_PREFIX_TO_PLATFORM.get(prefix, "oc_de")
        platform_codes.setdefault(plat, []).append(code)

    completed = 0
    for platform, codes in platform_codes.items():
        if task_info.cancel_event.is_set():
            break
        acct = UserAccount.objects.filter(platform=platform).first()
        user_id = acct.user_id if acct else ""
        client = OCClient(platform=platform, user_id=user_id)
        for code in codes:
            if task_info.cancel_event.is_set():
                break
            try:
                saved = fetch_oc_logs(client, code, count=count)
                total_new += saved
            except Exception as exc:
                logger.warning("OC log fetch failed for %s: %s", code, exc)
            completed += 1
            task_info.completed = completed

    done_phase = f"Done: {total_new} new OC log(s) from {len(oc_codes)} caches"
    logger.info("OC log fetch done: %s", done_phase)
    task_info.phase = done_phase
    return {"summary": f"Fetched {total_new} new OC log(s)", "new_logs": total_new}


def _verify_ftf(qs, task_info):
    """Fetch recent logs for FTF candidates to verify they have no found logs."""
    from geocaches.filters import EVENT_TYPES, FOUND_LOG_TYPES

    # The queryset is already filtered by the tool view; just extract codes.
    candidates = list(
        qs.values_list("gc_code", "oc_code", named=True)
    )

    gc_candidates = [c.gc_code for c in candidates if c.gc_code]
    oc_candidates = [c.oc_code for c in candidates if c.oc_code]
    total = len(gc_candidates) + len(oc_candidates)
    task_info.total = total
    logger.info("FTF verify starting: %d GC + %d OC candidates", len(gc_candidates), len(oc_candidates))
    task_info.phase = f"Verifying FTF: {len(gc_candidates)} GC + {len(oc_candidates)} OC"

    completed = 0
    total_new = 0

    # Fetch recent GC logs
    if gc_candidates:
        from geocaches.sync.gc_client import GCClient
        from geocaches.sync.log_fetch import fetch_recent_gc_logs
        client = GCClient()
        for code in gc_candidates:
            if task_info.cancel_event.is_set():
                break
            try:
                saved, _ = fetch_recent_gc_logs(client, code, count=10)
                total_new += saved
            except Exception as exc:
                logger.warning("FTF verify log fetch failed for %s: %s", code, exc)
            completed += 1
            task_info.completed = completed

    # Fetch recent OC logs
    if oc_candidates and not task_info.cancel_event.is_set():
        from geocaches.sync.oc_client import OCClient
        from geocaches.sync.log_fetch import fetch_oc_logs
        from accounts.models import UserAccount
        from geocaches.models import Geocache as _Gc

        platform_codes: dict[str, list[str]] = {}
        for code in oc_candidates:
            prefix = code[:2].upper()
            plat = _Gc._OC_PREFIX_TO_PLATFORM.get(prefix, "oc_de")
            platform_codes.setdefault(plat, []).append(code)

        for platform, codes in platform_codes.items():
            if task_info.cancel_event.is_set():
                break
            acct = UserAccount.objects.filter(platform=platform).first()
            user_id = acct.user_id if acct else ""
            client = OCClient(platform=platform, user_id=user_id)
            for code in codes:
                if task_info.cancel_event.is_set():
                    break
                try:
                    saved = fetch_oc_logs(client, code, count=10)
                    total_new += saved
                except Exception as exc:
                    logger.warning("FTF verify OC log fetch failed for %s: %s", code, exc)
                completed += 1
                task_info.completed = completed

    done_phase = f"Done: fetched {total_new} log(s) for {total} candidates"
    logger.info("FTF verify done: %s", done_phase)
    task_info.phase = done_phase
    return {"summary": done_phase, "new_logs": total_new, "candidates": total}


def get_status() -> dict:
    """Return a snapshot of current update task status."""
    if _current_task_id:
        info = get_task(_current_task_id)
        if info:
            return {
                "running": info["state"] == TaskState.RUNNING.value,
                "started_at": info["started_at"],
                "total": info["total"],
                "completed": info["completed"],
                "progress_pct": info["progress_pct"],
                "phase": info["phase"] or ("done" if info["state"] == TaskState.COMPLETED.value else info["state"]),
                "error": info["error"],
                "result": info.get("result"),
            }
    return {
        "running": False,
        "started_at": None,
        "total": 0,
        "completed": 0,
        "progress_pct": 0,
        "phase": "",
        "error": "",
        "result": None,
    }


def cancel_update():
    """Signal the update thread to stop."""
    if _current_task_id:
        cancel_task(_current_task_id)
        from geocaches.tasks.runner import _registry, _lock
        with _lock:
            info = _registry.get(_current_task_id)
            if info:
                info.phase = "cancelled"
