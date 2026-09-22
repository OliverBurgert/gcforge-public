import logging

from geocaches.tasks import submit_task, get_task, TaskState

logger = logging.getLogger(__name__)

_current_task_id: str | None = None


def start_refresh(scope: dict) -> bool:
    """Start an ignore-list refresh in a background thread. Returns False if already running."""
    global _current_task_id
    if _current_task_id:
        info = get_task(_current_task_id)
        if info and info["state"] == TaskState.RUNNING.value:
            return False

    _current_task_id = submit_task("Ignore list refresh", _run_refresh, scope)
    if _current_task_id:
        from geocaches.tasks.runner import _registry, _lock
        with _lock:
            info_obj = _registry.get(_current_task_id)
        if info_obj:
            info_obj.phase = "starting"
    return True


def _run_refresh(scope: dict, *, task_info):
    from geocaches.models import IgnoreListEntry, IgnoreSource
    from geocaches.services.ignore_list import (
        sync_gc_ignore_list,
        sync_oc_ignore_list,
        refresh_statuses,
        _LOCAL_SOURCES,
    )

    refresh_all = scope.get("all")
    source = scope.get("source")
    oc_platform = scope.get("oc_platform")

    if refresh_all or source == IgnoreSource.GC:
        task_info.phase = "Re-syncing GC ignore list"
        try:
            count = sync_gc_ignore_list()
            task_info.phase = f"GC sync done: {count} entries"
        except Exception as exc:
            logger.error("GC ignore list sync failed: %s", exc)
            task_info.phase = f"GC sync failed: {exc}"
        if task_info.cancel_event.is_set():
            return {"phase": task_info.phase}

    if refresh_all or source == IgnoreSource.OC:
        from accounts.models import UserAccount
        platforms = (
            [oc_platform] if oc_platform else
            list(UserAccount.objects.filter(platform__startswith="oc_")
                 .values_list("platform", flat=True).distinct())
        )
        for plat in platforms:
            if task_info.cancel_event.is_set():
                break
            task_info.phase = f"Re-syncing {plat} ignore list"
            try:
                count = sync_oc_ignore_list(plat)
                task_info.phase = f"{plat} sync done: {count} entries"
            except Exception as exc:
                logger.error("OC ignore list sync failed for %s: %s", plat, exc)
                task_info.phase = f"{plat} sync failed: {exc}"

    if task_info.cancel_event.is_set():
        return {"phase": task_info.phase}

    if refresh_all or source in (IgnoreSource.INTERNAL, IgnoreSource.GSAK, None):
        from django.db.models import Q as _Q
        entries = IgnoreListEntry.objects.filter(source__in=_LOCAL_SOURCES)
        if source and not refresh_all:
            entries = entries.filter(source=source)
        if scope.get("status"):
            entries = entries.filter(status=scope["status"])
        if scope.get("q"):
            sq = scope["q"]
            entries = entries.filter(
                _Q(code__icontains=sq) | _Q(name__icontains=sq) | _Q(notes__icontains=sq)
            )
        task_info.total = entries.count()
        refresh_statuses(entries, task_info=task_info)

    if not task_info.cancel_event.is_set():
        task_info.phase = "done"
    return {"phase": task_info.phase}


def get_status() -> dict:
    if _current_task_id:
        info = get_task(_current_task_id)
        if info:
            return {
                "running": info["state"] == TaskState.RUNNING.value,
                "total": info["total"],
                "completed": info["completed"],
                "progress_pct": info["progress_pct"],
                "phase": info["phase"] or ("done" if info["state"] == TaskState.COMPLETED.value else info["state"]),
                "error": info["error"],
            }
    return {"running": False, "total": 0, "completed": 0, "progress_pct": 0, "phase": "", "error": ""}
