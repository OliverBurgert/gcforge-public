import logging

from django.http import HttpResponse, JsonResponse

from .list import _filtered_qs

logger = logging.getLogger("geocaches.export")


def cache_export_gpx(request):
    from datetime import datetime, timezone
    from geocaches.models import Waypoint
    from geocaches.services import export_caches

    qs, _ = _filtered_qs(request)

    from preferences.models import UserPreference, GPX_EXPORT_DEFAULTS
    gc_username = UserPreference.get("gc_username", "")
    saved_opts = UserPreference.get("gpx_export_settings", {}) or {}
    opts = {**GPX_EXPORT_DEFAULTS, **saved_opts}

    cache_ids = list(qs.values_list('pk', flat=True))
    cache_count = len(cache_ids)
    wp_count = Waypoint.objects.filter(geocache_id__in=cache_ids).count()

    data = export_caches(qs, username=gc_username, opts=opts)
    filename = f"gcforge-export-{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}.gpx"

    dest = request.GET.get("dest", "").strip()
    if dest:
        from pathlib import Path
        dest_path = Path(dest) / filename
        try:
            dest_path.write_bytes(data)
        except Exception as exc:
            return JsonResponse({"error": str(exc)}, status=400)
        # Save to recent export folders
        recent = UserPreference.get("recent_export_folders", [])
        recent = [r for r in recent if r["path"] != dest]
        recent.insert(0, {
            "path": dest,
            "date": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M"),
        })
        UserPreference.set("recent_export_folders", recent[:5])
        logger.info("--- GPX export done: %d caches, %d waypoints → %s", cache_count, wp_count, dest_path)
        return JsonResponse({"ok": True, "file": str(dest_path), "cache_count": cache_count, "wp_count": wp_count})

    response = HttpResponse(data, content_type="application/gpx+xml")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


def cache_detail_export_gpx(request, gc_code):
    from datetime import datetime, timezone
    from geocaches.models import Geocache
    from geocaches.services import export_caches
    from geocaches.views.detail import _get_cache
    from preferences.models import UserPreference, GPX_EXPORT_DEFAULTS

    cache = _get_cache(gc_code)
    qs = Geocache.objects.filter(pk=cache.pk)
    gc_username = UserPreference.get("gc_username", "")
    saved_opts = UserPreference.get("gpx_export_settings", {}) or {}
    opts = {**GPX_EXPORT_DEFAULTS, **saved_opts}

    data = export_caches(qs, username=gc_username, opts=opts)
    filename = f"{gc_code}.gpx"

    dest = request.GET.get("dest", "").strip()
    if dest:
        from pathlib import Path
        dest_path = Path(dest) / filename
        try:
            dest_path.write_bytes(data)
        except Exception as exc:
            return JsonResponse({"error": str(exc)}, status=400)
        recent = UserPreference.get("recent_export_folders", [])
        recent = [r for r in recent if r["path"] != dest]
        recent.insert(0, {
            "path": dest,
            "date": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M"),
        })
        UserPreference.set("recent_export_folders", recent[:5])
        return JsonResponse({"ok": True, "file": str(dest_path), "cache_count": 1, "wp_count": 0})

    response = HttpResponse(data, content_type="application/gpx+xml")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


def export_recent_folders(request):
    from preferences.models import UserPreference
    recent = UserPreference.get("recent_export_folders", [])
    return JsonResponse({"folders": recent})
