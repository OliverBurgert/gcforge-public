import json
import logging

from django.conf import settings

from geocaches.tasks import submit_task
from geocaches.tasks.runner import _lock, _registry

logger = logging.getLogger(__name__)


def start_gallery_build(query_string: str, options: dict) -> str:
    """Submit the gallery build task and return the task ID."""

    def _build(qs_str: str, opts: dict, *, task_info):
        from geocaches.models import Geocache
        from geocaches.query import apply_all
        from geocaches.services.gallery import collect_cache_images

        qs = Geocache.objects.all()
        from django.http import QueryDict
        qd = QueryDict(qs_str)
        qs, _ = apply_all(qs, qd)

        total = qs.count()
        task_info.total = total
        task_info.phase = "collecting"

        run_dir = settings.DATA_DIR / "gallery_runs"
        run_dir.mkdir(parents=True, exist_ok=True)
        run_file = run_dir / f"{task_info.id}.json"
        run_file.write_text(json.dumps({"query_string": qs_str, "options": opts}), encoding="utf-8")

        for i, cache in enumerate(qs.iterator()):
            if task_info.cancel_event.is_set():
                task_info.phase = "cancelled"
                return {"phase": "cancelled"}
            try:
                collect_cache_images(cache, opts)
            except Exception as exc:
                logger.warning("gallery: error collecting images for %s: %s", cache.pk, exc)
            task_info.completed = i + 1
            task_info.phase = f"collecting ({i + 1}/{total})"

        task_info.phase = "done"
        return {"task_id": task_info.id, "phase": "done", "total": total}

    task_id = submit_task("Image gallery", _build, query_string, options)

    with _lock:
        info = _registry.get(task_id)
        if info:
            info.phase = "starting"

    return task_id
