import json
import logging
from datetime import date

from django.conf import settings
from django.http import HttpResponse
from django.shortcuts import redirect, render

from geocaches.tasks import get_task, TaskState
from .list import _filtered_qs

logger = logging.getLogger(__name__)

_DEFAULT_OPTIONS = {
    "include_short_description": False,
    "include_long_description": False,
    "include_coords": False,
    "include_map": False,
    "map_zoom": 13,
    "user_notes": True,
    "image_size": "page_width",
    "max_width_px": 800,
    "notes_box": False,
    "include_log_images": True,
}


def _load_run(task_id: str) -> dict | None:
    run_file = settings.DATA_DIR / "gallery_runs" / f"{task_id}.json"
    if not run_file.exists():
        return None
    try:
        return json.loads(run_file.read_text(encoding="utf-8"))
    except Exception:
        return None


def _qs_from_run(run: dict):
    from django.http import QueryDict
    from geocaches.models import Geocache
    from geocaches.query import apply_all

    qs = Geocache.objects.all()
    qd = QueryDict(run.get("query_string", ""))
    qs, _ = apply_all(qs, qd)
    return qs


def tools_image_gallery_config(request):
    qs, _ = _filtered_qs(request)
    query_string = request.GET.urlencode()
    count = qs.count()
    return render(request, "geocaches/tools/image_gallery_config.html", {
        "query_string": query_string,
        "cache_count": count,
        "defaults": _DEFAULT_OPTIONS,
    })


def tools_image_gallery_generate(request):
    if request.method != "POST":
        return redirect("geocaches:tools_image_gallery_config")

    query_string = request.POST.get("query_string", "")

    try:
        zoom = int(request.POST.get("map_zoom", 13) or 13)
    except (TypeError, ValueError):
        zoom = 13
    zoom = max(1, min(19, zoom))

    options = {
        "include_short_description": request.POST.get("include_short_description") == "on",
        "include_long_description": request.POST.get("include_long_description") == "on",
        "include_coords": request.POST.get("include_coords") == "on",
        "include_map": request.POST.get("include_map") == "on",
        "map_zoom": zoom,
        "user_notes": request.POST.get("user_notes") == "on",
        "image_size": request.POST.get("image_size", "page_width"),
        "max_width_px": int(request.POST.get("max_width_px", 800) or 800),
        "notes_box": request.POST.get("notes_box") == "on",
        "include_log_images": request.POST.get("include_log_images") == "on",
    }

    from geocaches.tasks.image_gallery import start_gallery_build
    task_id = start_gallery_build(query_string, options)
    return redirect("geocaches:tools_image_gallery_view", task_id=task_id)


def tools_image_gallery_view(request, task_id: str):
    task = get_task(task_id)
    if task is None:
        return render(request, "geocaches/tools/tools_result.html", {
            "title": "Image Gallery",
            "message": "Task not found.",
        })

    state = task["state"]

    if state in (TaskState.PENDING.value, TaskState.RUNNING.value):
        return render(request, "geocaches/tools/image_gallery_page.html", {
            "task": task,
            "task_running": True,
        })

    if state in (TaskState.FAILED.value, TaskState.CANCELLED.value):
        return render(request, "geocaches/tools/tools_result.html", {
            "title": "Image Gallery",
            "message": f"Gallery build {state}: {task.get('error', '')}",
        })

    # Completed
    run = _load_run(task_id)
    if run is None:
        return render(request, "geocaches/tools/tools_result.html", {
            "title": "Image Gallery",
            "message": "Run data not found. The task may have expired.",
        })

    options = run.get("options", _DEFAULT_OPTIONS)
    qs = _qs_from_run(run)

    from geocaches.services.gallery import collect_cache_images, image_url_for_display

    sections = []
    for cache in qs:
        imgs = collect_cache_images(cache, options)
        display_imgs = [
            {**item, "display_url": image_url_for_display(item["url"], cache)}
            for item in imgs
        ]
        notes = (
            list(cache.notes.filter(note_type="note"))
            if options.get("user_notes")
            else []
        )
        sections.append({"cache": cache, "images": display_imgs, "notes": notes})

    return render(request, "geocaches/tools/image_gallery_page.html", {
        "task": task,
        "task_running": False,
        "task_id": task_id,
        "sections": sections,
        "options": options,
        "cache_count": len(sections),
    })


def tools_image_gallery_export_html(request, task_id: str):
    run = _load_run(task_id)
    if run is None:
        return HttpResponse("Run data not found.", status=404)

    options = run.get("options", _DEFAULT_OPTIONS)
    qs = list(_qs_from_run(run))

    from geocaches.exporters.gallery_html import build_html_zip
    data = build_html_zip(qs, options)
    filename = f"gallery_{date.today().isoformat()}.zip"
    resp = HttpResponse(data, content_type="application/zip")
    resp["Content-Disposition"] = f'attachment; filename="{filename}"'
    return resp


def tools_image_gallery_export_odf(request, task_id: str):
    run = _load_run(task_id)
    if run is None:
        return HttpResponse("Run data not found.", status=404)

    options = run.get("options", _DEFAULT_OPTIONS)
    qs = list(_qs_from_run(run))

    from geocaches.exporters.gallery_odf import build_odf
    data = build_odf(qs, options)
    filename = f"gallery_{date.today().isoformat()}.odt"
    resp = HttpResponse(data, content_type="application/vnd.oasis.opendocument.text")
    resp["Content-Disposition"] = f'attachment; filename="{filename}"'
    return resp
