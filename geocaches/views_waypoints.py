from django.shortcuts import get_object_or_404, redirect
from django.views.decorators.http import require_POST

from geocaches.models import Waypoint, WaypointType
from geocaches.coords import parse_lat_lon


def _get_cache(gc_code):
    from geocaches.views import _get_cache as _base
    return _base(gc_code)


@require_POST
def waypoint_save(request, gc_code, wp_id=None):
    """Create or update a waypoint."""
    cache = _get_cache(gc_code)
    waypoint_type = request.POST.get("waypoint_type", WaypointType.OTHER)
    if waypoint_type not in WaypointType.values:
        waypoint_type = WaypointType.OTHER
    name = request.POST.get("name", "").strip()
    note = request.POST.get("note", "").strip()
    lat_str = request.POST.get("latitude", "").strip()
    lon_str = request.POST.get("longitude", "").strip()

    coords = parse_lat_lon(lat_str, lon_str) if (lat_str and lon_str) else None
    lat = coords[0] if coords else None
    lon = coords[1] if coords else None

    if wp_id:
        wp = get_object_or_404(Waypoint, pk=wp_id, geocache=cache)
        wp.waypoint_type = waypoint_type
        wp.name = name
        wp.note = note
        wp.latitude = lat
        wp.longitude = lon
        wp.is_user_modified = True
        wp.save(update_fields=["waypoint_type", "name", "note", "latitude", "longitude", "is_user_modified"])
    else:
        Waypoint.objects.create(
            geocache=cache,
            waypoint_type=waypoint_type,
            name=name,
            note=note,
            latitude=lat,
            longitude=lon,
            is_user_created=True,
        )

    return redirect("geocaches:detail", gc_code=cache.display_code)


@require_POST
def waypoint_hide(request, gc_code, wp_id):
    cache = _get_cache(gc_code)
    wp = get_object_or_404(Waypoint, pk=wp_id, geocache=cache)
    wp.is_hidden = True
    wp.save(update_fields=["is_hidden"])
    return redirect("geocaches:detail", gc_code=cache.display_code)


@require_POST
def waypoint_unhide(request, gc_code, wp_id):
    cache = _get_cache(gc_code)
    wp = get_object_or_404(Waypoint, pk=wp_id, geocache=cache)
    wp.is_hidden = False
    wp.save(update_fields=["is_hidden"])
    return redirect("geocaches:detail", gc_code=cache.display_code)


@require_POST
def waypoint_restore_all(request, gc_code):
    cache = _get_cache(gc_code)
    cache.waypoints.filter(is_hidden=True).update(is_hidden=False)
    return redirect("geocaches:detail", gc_code=cache.display_code)


@require_POST
def waypoint_toggle_complete(request, gc_code, wp_id):
    cache = _get_cache(gc_code)
    wp = get_object_or_404(Waypoint, pk=wp_id, geocache=cache)
    wp.is_completed = not wp.is_completed
    wp.save(update_fields=["is_completed"])
    return redirect("geocaches:detail", gc_code=cache.display_code)


@require_POST
def waypoint_delete(request, gc_code, wp_id):
    """Delete a user-created waypoint."""
    cache = _get_cache(gc_code)
    wp = get_object_or_404(Waypoint, pk=wp_id, geocache=cache)
    if wp.is_user_created:
        wp.delete()
    return redirect("geocaches:detail", gc_code=cache.display_code)
