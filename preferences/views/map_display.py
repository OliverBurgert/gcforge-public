from django.http import HttpResponseNotAllowed, JsonResponse

from preferences.models import ReferencePoint, UserPreference
from ._helpers import _redirect_tab


def save_map_state(request):
    """AJAX POST: save map state preferences (layout, split %, center, zoom)."""
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    _MAP_KEYS = {
        "map_layout", "map_split_pct", "map_center_lat", "map_center_lon", "map_zoom",
    }
    for key in _MAP_KEYS:
        val = request.POST.get(key)
        if val is not None:
            # Store numeric values as numbers
            if key in ("map_split_pct", "map_center_lat", "map_center_lon", "map_zoom"):
                try:
                    val = float(val)
                    if key == "map_split_pct":
                        val = max(20, min(80, int(val)))
                except (ValueError, TypeError):
                    continue
            UserPreference.set(key, val)
    return JsonResponse({"ok": True})


def locations_json(request):
    """GET: return reference points as JSON (for refreshing the map dropdown)."""
    data = [
        {"id": rp.id, "name": rp.name, "lat": rp.latitude, "lon": rp.longitude, "home": rp.is_home}
        for rp in ReferencePoint.objects.all()
    ]
    return JsonResponse(data, safe=False)


def save_map_display(request):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    style = request.POST.get("map_style", "outdoor")
    if style in ("street", "outdoor", "aerial"):
        UserPreference.set("map_style", style)
    UserPreference.set("map_boundary_country", request.POST.get("map_boundary_country") == "1")
    UserPreference.set("map_boundary_state",   request.POST.get("map_boundary_state")   == "1")
    UserPreference.set("map_boundary_county",  request.POST.get("map_boundary_county")  == "1")
    UserPreference.set("map_radius_circle",    request.POST.get("map_radius_circle")    == "1")
    UserPreference.set("map_radius_shade",     request.POST.get("map_radius_shade")     == "1")
    UserPreference.set("map_layer_sep_circles", request.POST.get("map_layer_sep_circles") == "1")
    UserPreference.set("map_layer_alc_circles", request.POST.get("map_layer_alc_circles") == "1")
    UserPreference.set("map_layer_corrected",  request.POST.get("map_layer_corrected")  == "1")
    UserPreference.set("map_layer_waypoints",  request.POST.get("map_layer_waypoints")  == "1")
    UserPreference.set("map_layer_labels",     request.POST.get("map_layer_labels", ""))
    UserPreference.set("map_layer_lod",       request.POST.get("map_layer_lod") == "1")
    request.session["map_display_msg"] = {"ok": True, "text": "Map display defaults saved."}
    return _redirect_tab("map")


def save_location_json(request):
    """Create/update a named location. Used by map context menu and crosshair button."""
    import json
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    try:
        data = json.loads(request.body)
        lat = float(data.get("latitude"))
        lon = float(data.get("longitude"))
        name = (data.get("name") or "").strip()
    except (json.JSONDecodeError, TypeError, ValueError):
        return JsonResponse({"ok": False, "error": "Invalid data"}, status=400)
    if not (-90 <= lat <= 90) or not (-180 <= lon <= 180):
        return JsonResponse({"ok": False, "error": "Coordinates out of range"}, status=400)
    if not name:
        return JsonResponse({"ok": False, "error": "Name required"}, status=400)
    note = (data.get("note") or "").strip()
    rp = ReferencePoint.objects.create(
        name=name, latitude=lat, longitude=lon, note=note,
    )
    return JsonResponse({"ok": True, "id": rp.pk, "name": rp.name})


def save_offline_prefs(request):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    UserPreference.set('offline_maps_dir', request.POST.get('offline_maps_dir', '').strip())
    UserPreference.set('offline_source_url', request.POST.get('offline_source_url', '').strip())
    return _redirect_tab('offline')
