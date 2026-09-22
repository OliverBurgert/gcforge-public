from geocaches.tasks import submit_task, get_task, cancel_task, TaskState

_current_task_id: str | None = None


def start_enrichment(queryset, fields: set[str], overwrite: set[str] | None = None) -> bool:
    """Start enrichment in a background thread. Returns False if already running."""
    global _current_task_id
    if overwrite is None:
        overwrite = set()

    if _current_task_id:
        info = get_task(_current_task_id)
        if info and info["state"] == TaskState.RUNNING.value:
            return False

    def _enrich_fn(qs, flds, ow, *, task_info):
        from geocaches.enrichment import enrich_queryset

        def _progress(completed: int, phase: str):
            task_info.completed = completed
            task_info.phase = phase

        enrich_queryset(
            qs, flds, ow,
            progress_callback=_progress,
            cancel_event=task_info.cancel_event,
        )
        # Set final phase
        if not task_info.cancel_event.is_set():
            task_info.phase = "done"
        return {"phase": task_info.phase}

    _current_task_id = submit_task("Enrichment", _enrich_fn, queryset, fields, overwrite)
    # Set initial state visible before the thread starts
    if _current_task_id:
        info_obj = None
        from geocaches.tasks.runner import _registry, _lock
        with _lock:
            info_obj = _registry.get(_current_task_id)
        if info_obj:
            info_obj.total = queryset.count()
            info_obj.phase = "starting"
    return True


def get_status() -> dict:
    """Return a snapshot of current enrichment status (safe to call from any thread)."""
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
            }
    return {
        "running": False,
        "started_at": None,
        "total": 0,
        "completed": 0,
        "progress_pct": 0,
        "phase": "",
        "error": "",
    }


def cancel_enrichment():
    """Signal the enrichment thread to stop."""
    if _current_task_id:
        cancel_task(_current_task_id)
        # Update phase immediately for responsive UI
        from geocaches.tasks.runner import _registry, _lock
        with _lock:
            info = _registry.get(_current_task_id)
            if info:
                info.phase = "cancelled"
