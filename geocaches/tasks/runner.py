import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum


class TaskState(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class TaskInfo:
    id: str
    name: str
    state: TaskState = TaskState.PENDING
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    started_at: datetime | None = None
    completed_at: datetime | None = None
    total: int = 0
    completed: int = 0
    phase: str = ""
    error: str = ""
    result: dict | None = None
    cancel_event: threading.Event = field(default_factory=threading.Event)

    @property
    def progress_pct(self) -> int:
        return int(self.completed / self.total * 100) if self.total else 0

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "state": self.state.value,
            "created_at": self.created_at.isoformat(),
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "total": self.total,
            "completed": self.completed,
            "progress_pct": self.progress_pct,
            "phase": self.phase,
            "error": self.error,
            "result": self.result,
        }


_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="gcforge-task")
_registry: dict[str, TaskInfo] = {}
_lock = threading.Lock()


def submit_task(name: str, fn, *args, **kwargs) -> str:
    """Submit a task for background execution. Returns task ID."""
    task_id = uuid.uuid4().hex[:12]
    info = TaskInfo(id=task_id, name=name)

    with _lock:
        _registry[task_id] = info

    def _wrapper():
        with _lock:
            info.state = TaskState.RUNNING
            info.started_at = datetime.now(timezone.utc)
        try:
            from django.db import close_old_connections
            close_old_connections()
            result = fn(*args, task_info=info, **kwargs)
            with _lock:
                if info.state != TaskState.CANCELLED:
                    info.state = TaskState.COMPLETED
                    info.result = result
                    info.completed_at = datetime.now(timezone.utc)
        except Exception as exc:
            import logging
            import traceback
            logging.getLogger("geocaches.tasks").error(
                "Task %s crashed: %s", task_id, exc, exc_info=True
            )
            traceback.print_exc()
            with _lock:
                info.state = TaskState.FAILED
                info.error = str(exc)
                info.completed_at = datetime.now(timezone.utc)

    _executor.submit(_wrapper)
    return task_id


def get_task(task_id: str) -> dict | None:
    with _lock:
        info = _registry.get(task_id)
        return info.to_dict() if info else None


def cancel_task(task_id: str) -> bool:
    with _lock:
        info = _registry.get(task_id)
        if info and info.state == TaskState.RUNNING:
            info.cancel_event.set()
            info.state = TaskState.CANCELLED
            return True
    return False


def list_tasks() -> list[dict]:
    with _lock:
        return [info.to_dict() for info in _registry.values()]


def cleanup_old_tasks(max_age_hours: int = 24):
    """Remove completed/failed tasks older than max_age_hours."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
    with _lock:
        to_remove = [
            tid for tid, info in _registry.items()
            if info.completed_at and info.completed_at < cutoff
        ]
        for tid in to_remove:
            del _registry[tid]
