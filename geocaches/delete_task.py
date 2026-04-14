import threading
from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass
class DeletionStatus:
    running: bool = False
    started_at: datetime | None = None
    total: int = 0
    deleted: int = 0
    phase: str = ""  # "deleting", "done", "error"
    error: str = ""

    @property
    def progress_pct(self) -> int:
        return int(self.deleted / self.total * 100) if self.total else 0


_status = DeletionStatus()
_lock = threading.Lock()

BATCH = 500


def get_status() -> dict:
    with _lock:
        return {
            "running": _status.running,
            "started_at": _status.started_at.isoformat() if _status.started_at else None,
            "total": _status.total,
            "deleted": _status.deleted,
            "progress_pct": _status.progress_pct,
            "phase": _status.phase,
            "error": _status.error,
        }


def start_deletion(pk_list: list[int]) -> bool:
    """Start batch deletion in a background thread. Returns False if already running."""
    with _lock:
        if _status.running:
            return False
        _status.running = True
        _status.started_at = datetime.now(timezone.utc)
        _status.total = len(pk_list)
        _status.deleted = 0
        _status.phase = "deleting"
        _status.error = ""

    def _run():
        try:
            from django.db import close_old_connections
            close_old_connections()
            from geocaches.models import Geocache

            for i in range(0, len(pk_list), BATCH):
                batch = pk_list[i:i + BATCH]
                Geocache.objects.filter(pk__in=batch).delete()
                with _lock:
                    _status.deleted += len(batch)

            with _lock:
                _status.phase = "done"
        except Exception as exc:
            import logging
            logging.getLogger("geocaches.delete").error(
                "Deletion thread crashed: %s", exc, exc_info=True,
            )
            with _lock:
                _status.phase = "error"
                _status.error = str(exc)
        finally:
            with _lock:
                _status.running = False

    threading.Thread(target=_run, daemon=True).start()
    return True
