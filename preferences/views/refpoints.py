import json

from django.http import HttpResponseNotAllowed, JsonResponse

from preferences.models import ReferencePoint
from ._helpers import _redirect_tab


def add_refpoint(request):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    from geocaches.geo.coords import parse_lat_lon
    name = request.POST.get("rp_name", "").strip()
    lat_str = request.POST.get("rp_lat", "").strip()
    lon_str = request.POST.get("rp_lon", "").strip()
    note = request.POST.get("rp_note", "").strip()
    valid_from_str = request.POST.get("rp_valid_from", "").strip() or None
    is_default = request.POST.get("rp_default") == "1"
    is_home = request.POST.get("rp_home") == "1"
    result = parse_lat_lon(lat_str, lon_str)
    if name and result:
        lat, lon = result
        rp = ReferencePoint.objects.create(
            name=name,
            latitude=lat,
            longitude=lon,
            note=note,
            valid_from=valid_from_str,
            is_default=is_default,
            is_home=is_home,
        )
        if is_default:
            ReferencePoint.objects.exclude(pk=rp.pk).update(is_default=False)
    return _redirect_tab("reference-points")


def edit_refpoint(request):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    from geocaches.geo.coords import parse_lat_lon
    rp_id = request.POST.get("rp_id")
    name = request.POST.get("rp_name", "").strip()
    lat_str = request.POST.get("rp_lat", "").strip()
    lon_str = request.POST.get("rp_lon", "").strip()
    note = request.POST.get("rp_note", "").strip()
    valid_from_str = request.POST.get("rp_valid_from", "").strip() or None
    is_home = request.POST.get("rp_home") == "1"
    result = parse_lat_lon(lat_str, lon_str)
    if rp_id and name and result:
        lat, lon = result
        ReferencePoint.objects.filter(id=rp_id).update(
            name=name,
            latitude=lat,
            longitude=lon,
            note=note,
            valid_from=valid_from_str,
            is_home=is_home,
        )
    return _redirect_tab("reference-points")


def delete_refpoint(request):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    ReferencePoint.objects.filter(id=request.POST.get("rp_id")).delete()
    return _redirect_tab("reference-points")


def set_default_refpoint(request):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    rp_id = request.POST.get("rp_id")
    ReferencePoint.objects.all().update(is_default=False)
    ReferencePoint.objects.filter(id=rp_id).update(is_default=True)
    return _redirect_tab("reference-points")


def set_current_location(request):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    try:
        data = json.loads(request.body)
        lat = float(data.get("latitude"))
        lon = float(data.get("longitude"))
    except (json.JSONDecodeError, TypeError, ValueError):
        return JsonResponse({"ok": False, "error": "Invalid coordinates"}, status=400)
    if not (-90 <= lat <= 90) or not (-180 <= lon <= 180):
        return JsonResponse({"ok": False, "error": "Coordinates out of range"}, status=400)
    rp, _created = ReferencePoint.objects.update_or_create(
        name="Current Location",
        defaults={"latitude": lat, "longitude": lon},
    )
    from geocaches.geo.distance_cache import recompute_distances
    recompute_distances(rp)
    return JsonResponse({"ok": True, "id": rp.pk})
