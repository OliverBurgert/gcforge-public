from pathlib import Path

from django.conf import settings as django_settings
from django.http import JsonResponse, StreamingHttpResponse, Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from .models import OfflineMapArea, UserPreference


def _resolve_offline_maps_dir() -> Path:
    """Resolve the offline maps directory (same logic as the service layer)."""
    offline_maps_dir = UserPreference.get("offline_maps_dir", "")
    if offline_maps_dir:
        return Path(offline_maps_dir)
    db_path = Path(django_settings.DATABASES["default"]["NAME"])
    return db_path.parent / "offline_maps"


def areas_json(request):
    areas = OfflineMapArea.objects.filter(status="ready").values(
        "id", "name", "bbox_min_lon", "bbox_min_lat", "bbox_max_lon", "bbox_max_lat",
        "min_zoom", "max_zoom",
    )
    data = [
        {
            "id": a["id"],
            "name": a["name"],
            "bbox": [a["bbox_min_lon"], a["bbox_min_lat"], a["bbox_max_lon"], a["bbox_max_lat"]],
            "min_zoom": a["min_zoom"],
            "max_zoom": a["max_zoom"],
        }
        for a in areas
    ]
    return JsonResponse(data, safe=False)


def serve_pmtiles(request, pk):
    try:
        area = OfflineMapArea.objects.get(pk=pk)
    except OfflineMapArea.DoesNotExist:
        raise Http404 from None

    if area.status != "ready":
        raise Http404

    maps_dir = _resolve_offline_maps_dir()
    file_path = maps_dir / area.filename

    if not file_path.exists():
        raise Http404

    total_size = file_path.stat().st_size
    range_header = request.META.get("HTTP_RANGE", "").strip()

    if range_header:
        # Parse "bytes=X-Y"
        range_value = range_header.replace("bytes=", "")
        parts = range_value.split("-")
        start = int(parts[0]) if parts[0] else 0
        end = int(parts[1]) if len(parts) > 1 and parts[1] else total_size - 1
        end = min(end, total_size - 1)
        length = end - start + 1

        def file_slice(path, start, length, chunk=65536):
            with open(path, "rb") as f:
                f.seek(start)
                remaining = length
                while remaining > 0:
                    data = f.read(min(chunk, remaining))
                    if not data:
                        break
                    remaining -= len(data)
                    yield data

        response = StreamingHttpResponse(
            file_slice(file_path, start, length),
            status=206,
            content_type="application/octet-stream",
        )
        response["Content-Length"] = str(length)
        response["Content-Range"] = f"bytes {start}-{end}/{total_size}"
        response["Accept-Ranges"] = "bytes"
        return response

    # No Range header — return full file
    def full_file(path, chunk=65536):
        with open(path, "rb") as f:
            while True:
                data = f.read(chunk)
                if not data:
                    break
                yield data

    response = StreamingHttpResponse(
        full_file(file_path),
        status=200,
        content_type="application/octet-stream",
    )
    response["Content-Length"] = str(total_size)
    response["Accept-Ranges"] = "bytes"
    return response


@require_POST
def start_download(request, pk):
    area = get_object_or_404(OfflineMapArea, pk=pk)

    if area.status == "downloading":
        return HttpResponse("Download already in progress", status=409)

    from preferences.offline_task import run_offline_download
    task_id = run_offline_download(area.id)

    if request.headers.get("HX-Request"):
        return HttpResponse(
            f'<span class="text-muted">Download started (task {task_id})</span>',
            status=200,
        )
    return redirect(reverse("preferences:settings") + "#offline")


@require_POST
def delete_area(request, pk):
    area = get_object_or_404(OfflineMapArea, pk=pk)

    if area.filename:
        maps_dir = _resolve_offline_maps_dir()
        file_path = maps_dir / area.filename
        if file_path.exists():
            file_path.unlink()

    area.delete()

    if request.headers.get("HX-Request"):
        return HttpResponse("", status=200)
    return redirect(reverse("preferences:settings") + "#offline")


def areas_partial(request):
    from preferences.models import OfflineMapArea
    areas = OfflineMapArea.objects.all().order_by('-created_at')
    return render(request, 'preferences/_offline_areas_table.html',
                  {'offline_areas': areas})


def create_area(request):
    if request.method != "POST":
        return HttpResponse("Method not allowed", status=405)

    name = request.POST.get("name", "").strip()
    if not name:
        return HttpResponse("Name is required", status=400)

    try:
        bbox_min_lon = float(request.POST["bbox_min_lon"])
        bbox_min_lat = float(request.POST["bbox_min_lat"])
        bbox_max_lon = float(request.POST["bbox_max_lon"])
        bbox_max_lat = float(request.POST["bbox_max_lat"])
        min_zoom = int(request.POST["min_zoom"])
        max_zoom = int(request.POST["max_zoom"])
    except (KeyError, ValueError) as exc:
        return HttpResponse(f"Invalid input: {exc}", status=400)

    source_url = request.POST.get("source_url", "").strip()
    if not source_url:
        from preferences.services.offline_maps import get_latest_protomaps_url
        source_url = (UserPreference.get("offline_source_url", "")
                      or get_latest_protomaps_url()
                      or "")

    area = OfflineMapArea.objects.create(
        name=name,
        bbox_min_lon=bbox_min_lon,
        bbox_min_lat=bbox_min_lat,
        bbox_max_lon=bbox_max_lon,
        bbox_max_lat=bbox_max_lat,
        min_zoom=min_zoom,
        max_zoom=max_zoom,
        source_url=source_url,
        status="pending",
    )

    from preferences.offline_task import run_offline_download
    run_offline_download(area.id)

    return redirect(reverse("preferences:settings") + "#offline")


def estimate_tiles(request):
    try:
        min_lon = float(request.GET["min_lon"])
        min_lat = float(request.GET["min_lat"])
        max_lon = float(request.GET["max_lon"])
        max_lat = float(request.GET["max_lat"])
        min_zoom = int(request.GET["min_zoom"])
        max_zoom = int(request.GET["max_zoom"])
    except (KeyError, ValueError) as exc:
        return JsonResponse({"error": str(exc)}, status=400)

    from preferences.services.offline_maps import estimate_tile_count
    result = estimate_tile_count(min_lon, min_lat, max_lon, max_lat, min_zoom, max_zoom)
    return JsonResponse(result)
