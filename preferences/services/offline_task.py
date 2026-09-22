from geocaches.tasks import submit_task, get_task, TaskState
from preferences.services.offline_maps import download_area

_task_registry: dict[int, str] = {}  # area_id -> task_id


def run_offline_download(area_id: int) -> str:
    """Submit a background download task for the given area. Returns the task ID."""

    def _task(*, task_info):
        def _progress(pct: int):
            task_info.completed = pct
            task_info.total = 100

        download_area(area_id, progress_callback=_progress)
        task_info.phase = "done"
        return {"area_id": area_id}

    task_id = submit_task(f"Download offline area {area_id}", _task)
    _task_registry[area_id] = task_id
    return task_id


def get_download_status(area_id: int) -> dict | None:
    """Return task status dict for the given area, or None if no task exists."""
    task_id = _task_registry.get(area_id)
    if not task_id:
        return None
    return get_task(task_id)


def is_downloading(area_id: int) -> bool:
    """Return True if a download task for this area is currently running."""
    info = get_download_status(area_id)
    return bool(info and info["state"] == TaskState.RUNNING.value)
