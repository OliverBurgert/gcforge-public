"""
Map marker endpoint — returns compact JSON for MapLibre rendering.
Area filter CRUD endpoints — SavedAreaFilter save/list/delete.
Preview & sync endpoints — API fetch from drawn map regions.
"""

import json
import logging
import math
from collections import defaultdict

from django.http import JsonResponse
from django.views.decorators.http import require_GET, require_POST, require_http_methods

from .models import CacheType, CacheSize, CacheStatus, Geocache, SavedAreaFilter, Waypoint, WaypointType
from .query import apply_all, mine_q
from preferences.models import ReferencePoint, UserPreference

logger = logging.getLogger(__name__)
sync_log = logging.getLogger("geocaches.sync")


# ---------------------------------------------------------------------------
# Short-code mappings
# ---------------------------------------------------------------------------

TYPE_SHORT = {
    CacheType.TRADITIONAL: "T",
    CacheType.MULTI: "M",
    CacheType.MYSTERY: "U",
    CacheType.VIRTUAL: "V",
    CacheType.EARTH: "E",
    CacheType.EVENT: "Ev",
    CacheType.CITO: "CI",
    CacheType.WEBCAM: "W",
    CacheType.WHERIGO: "Wh",
    CacheType.LAB: "L",
    CacheType.LETTERBOX: "B",
    CacheType.MEGA_EVENT: "ME",
    CacheType.GIGA_EVENT: "GE",
    CacheType.LOCATIONLESS: "Lo",
    CacheType.GPS_ADVENTURES: "GA",
    CacheType.COMMUNITY_CELEBRATION: "CC",
    CacheType.GC_HQ: "HQ",
    CacheType.GC_HQ_CELEBRATION: "HQ",
    CacheType.GC_HQ_BLOCK_PARTY: "HQ",
    CacheType.PROJECT_APE: "PA",
    CacheType.BENCHMARK: "BM",
    CacheType.DRIVE_IN: "DI",
    CacheType.MATH_PHYSICS: "MP",
    CacheType.MOVING: "Mo",
    CacheType.OWN: "O",
    CacheType.PODCAST: "Po",
    CacheType.UNKNOWN: "?",
}

SIZE_SHORT = {
    CacheSize.NANO: "N",
    CacheSize.MICRO: "Mi",
    CacheSize.SMALL: "S",
    CacheSize.REGULAR: "R",
    CacheSize.LARGE: "L",
    CacheSize.XLARGE: "X",
    CacheSize.VIRTUAL: "V",
    CacheSize.OTHER: "O",
    CacheSize.UNKNOWN: "U",
    CacheSize.NONE: "No",
}

STATUS_SHORT = {
    CacheStatus.ACTIVE: "A",
    CacheStatus.DISABLED: "D",
    CacheStatus.ARCHIVED: "X",
    CacheStatus.UNPUBLISHED: "U",
    CacheStatus.LOCKED: "L",
}

WP_TYPE_SHORT = {
    WaypointType.PARKING: "P",
    WaypointType.STAGE: "S",
    WaypointType.QUESTION: "Q",
    WaypointType.FINAL: "F",
    WaypointType.TRAILHEAD: "T",
    WaypointType.REFERENCE: "R",
    WaypointType.OTHER: "O",
}


@require_GET
def map_markers(request):
    """Return compact marker JSON for all caches matching current filters."""
    qs = Geocache.objects.all()

    # Resolve reference point — same logic as the list view so radius/distance
    # filters are applied consistently.
    distance_unit = UserPreference.get("distance_unit", "km")
    ref_points = list(ReferencePoint.objects.all())
    ref_id = request.GET.get("ref", "")
    if ref_id:
        ref = next((r for r in ref_points if str(r.pk) == ref_id), None)
    else:
        ref = next((r for r in ref_points if r.is_default), None) or (ref_points[0] if ref_points else None)

    # Only annotate distance when radius or bearing is actually requested.
    radius_str = request.GET.get("radius", "").strip()
    bearing_str = request.GET.get("bearing", "").strip()
    needs_distance = bool(radius_str) or bool(bearing_str)

    # Ensure the distance cache is populated so the query uses a fast
    # indexed join instead of per-row Python haversine callbacks.
    if needs_distance and ref:
        from .distance_cache import ensure_cached
        ensure_cached(ref)

    qs, _fv = apply_all(
        qs, request.GET,
        ref=ref if needs_distance else None,
        distance_unit=distance_unit,
    )

    # Optional bounding box filter: ?bbox=south,west,north,east
    bbox = request.GET.get("bbox", "").strip()
    if bbox:
        try:
            parts = [float(x) for x in bbox.split(",")]
            if len(parts) == 4:
                south, west, north, east = parts
                qs = qs.filter(
                    latitude__gte=south, latitude__lte=north,
                    longitude__gte=west, longitude__lte=east,
                )
        except ValueError:
            pass

    # Pre-compute owner info for is_mine detection
    from accounts.models import UserAccount
    accounts = list(UserAccount.objects.all())
    mine_gc_ids = set()
    mine_usernames = set()
    if accounts:
        mine_gc_ids = {
            int(a.user_id) for a in accounts
            if a.platform == "gc" and a.user_id.isdigit()
        }
        mine_usernames = {a.username for a in accounts if a.username}

    # Fetch only the columns we need via values_list for performance
    fields = (
        "gc_code", "al_code", "oc_code", "name", "latitude", "longitude",
        "cache_type", "size", "difficulty", "terrain",
        "found", "completed", "status", "owner", "owner_gc_id",
        "adventure_id", "stage_number",
    )

    from .models import CorrectedCoordinates
    # Always materialise rows and use a PK-list for corrected coordinates.
    # Using geocache__in=qs as a subquery is slow when qs contains a RawSQL
    # filter (e.g. where_sql) because SQLite cannot optimise the nested loop.
    corrected_map = {}
    rows = list(qs.values_list("pk", *fields))
    pk_set = [r[0] for r in rows]
    if pk_set:
        corrected_qs = CorrectedCoordinates.objects.filter(
            geocache_id__in=pk_set,
        ).values_list("geocache__gc_code", "geocache__al_code", "geocache__oc_code", "latitude", "longitude")
        for gc_code, al_code, oc_code, clat, clon in corrected_qs:
            corrected_map[gc_code or al_code or oc_code] = (clat, clon)
    # Strip the leading pk from each row for uniform iteration below
    rows = [r[1:] for r in rows]

    markers = []
    for (gc_code, al_code, oc_code, name, lat, lon, cache_type, size,
         diff, terr, found, completed, status, owner, owner_gc_id,
         adventure_id, stage_number) in rows:
        code = gc_code or al_code or oc_code

        # Skip caches with no coordinates (would crash round())
        if lat is None or lon is None:
            continue

        # is_mine check
        is_mine = False
        if mine_gc_ids or mine_usernames:
            if owner_gc_id and owner_gc_id in mine_gc_ids:
                is_mine = True
            elif owner and owner in mine_usernames:
                is_mine = True

        corr = corrected_map.get(code)

        marker = {
            "c": code,
            "n": name[:60],
            "la": round(lat, 6),
            "lo": round(lon, 6),
            "t": TYPE_SHORT.get(cache_type, "?"),
            "sz": SIZE_SHORT.get(size, "U"),
            "d": diff,
            "tr": terr,
            "f": found or completed,
            "s": STATUS_SHORT.get(status, "A"),
            "m": is_mine,
        }
        # Include both codes when cache has a secondary code (for dual external links)
        if gc_code and oc_code:
            marker["gc"] = gc_code
            marker["oc"] = oc_code
        elif oc_code:
            marker["oc"] = oc_code
        if corr:
            marker["cla"] = round(corr[0], 6)
            marker["clo"] = round(corr[1], 6)
        if adventure_id is not None:
            marker["aid"] = adventure_id
            if stage_number is not None:
                marker["sn"] = stage_number

        markers.append(marker)

    return JsonResponse({"markers": markers, "count": len(markers)})


# ---------------------------------------------------------------------------
# Waypoints for visible caches
# ---------------------------------------------------------------------------

MAX_WAYPOINT_CODES = 200


@require_GET
def map_waypoints(request):
    """Return child waypoints for the given cache codes."""
    raw = request.GET.get("codes", "").strip()
    if not raw:
        return JsonResponse({"waypoints": []})

    codes = [c.strip() for c in raw.split(",") if c.strip()][:MAX_WAYPOINT_CODES]
    if not codes:
        return JsonResponse({"waypoints": []})

    from django.db.models import Q
    qs = Waypoint.objects.filter(
        Q(geocache__gc_code__in=codes) | Q(geocache__al_code__in=codes) | Q(geocache__oc_code__in=codes),
        latitude__isnull=False,
        longitude__isnull=False,
        is_hidden=False,
    ).values_list(
        "geocache__gc_code", "geocache__al_code", "geocache__oc_code",
        "waypoint_type", "name", "latitude", "longitude",
    )

    grouped = defaultdict(list)
    for gc_code, al_code, oc_code, wp_type, name, lat, lon in qs:
        code = gc_code or al_code or oc_code
        grouped[code].append({
            "t": WP_TYPE_SHORT.get(wp_type, "O"),
            "n": name[:60],
            "la": round(lat, 6),
            "lo": round(lon, 6),
        })

    waypoints = [{"code": code, "wp": wps} for code, wps in grouped.items()]
    return JsonResponse({"waypoints": waypoints})


# ---------------------------------------------------------------------------
# Saved area filter CRUD
# ---------------------------------------------------------------------------


@require_GET
def saved_areas_list(request):
    areas = list(SavedAreaFilter.objects.values("id", "name", "regions", "created_at"))
    # Ensure created_at is serialisable
    for a in areas:
        a["created_at"] = a["created_at"].isoformat() if a["created_at"] else None
    return JsonResponse({"areas": areas})


@require_POST
def saved_area_save(request):
    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    name = (data.get("name") or "").strip()
    regions = data.get("regions")
    if not name:
        return JsonResponse({"error": "name is required"}, status=400)
    if not regions or not isinstance(regions, list):
        return JsonResponse({"error": "regions must be a non-empty list"}, status=400)

    area, created = SavedAreaFilter.objects.update_or_create(
        name=name,
        defaults={"regions": regions},
    )
    return JsonResponse({"id": area.pk, "name": area.name, "created": created})


@require_http_methods(["DELETE"])
def saved_area_delete(request, pk):
    try:
        SavedAreaFilter.objects.get(pk=pk).delete()
        return JsonResponse({"ok": True})
    except SavedAreaFilter.DoesNotExist:
        return JsonResponse({"error": "Not found"}, status=404)


# ---------------------------------------------------------------------------
# Providers & quota helper endpoints
# ---------------------------------------------------------------------------

@require_GET
def map_providers(request):
    """Return configured platform accounts for the fetch dialog."""
    from accounts.models import UserAccount

    PLATFORM_LABELS = dict(UserAccount.PLATFORM_CHOICES)
    accounts = UserAccount.objects.all()
    providers = []
    for acct in accounts:
        providers.append({
            "platform": acct.platform,
            "label": PLATFORM_LABELS.get(acct.platform, acct.platform),
            "username": acct.username,
            "account_label": acct.get_label(),
        })
    return JsonResponse({"providers": providers})


@require_GET
def map_quota(request):
    """Return remaining daily quota for requested platforms."""
    from datetime import date as date_mod
    from .models import SyncQuota
    from .sync.rate_limiter import QuotaTracker

    platforms = [p.strip() for p in request.GET.get("platforms", "").split(",") if p.strip()]
    if not platforms:
        return JsonResponse({"error": "platforms parameter required"}, status=400)

    valid_platforms = {"gc"} | QuotaTracker._KNOWN_OC_PLATFORMS
    unknown = [p for p in platforms if p not in valid_platforms]
    if unknown:
        return JsonResponse({"error": f"Unknown platform(s): {', '.join(unknown)}"}, status=400)

    # Auto-refresh GC membership level if unknown (sets correct full-mode quota)
    if "gc" in platforms:
        from accounts.models import UserAccount
        gc_account = UserAccount.objects.filter(platform="gc").first()
        if gc_account and gc_account.membership_level == 0:
            try:
                from .sync.service import refresh_membership_level
                refresh_membership_level()
            except Exception:
                pass  # API unavailable — fall back to conservative default
        # Ensure today's full-mode quota matches the stored membership level
        if gc_account:
            full_limit = 16_000 if gc_account.membership_level >= 2 else 3
            QuotaTracker.set_limit("gc", "full", full_limit)

    today = date_mod.today()
    result = {}
    for platform in platforms:
        result[platform] = {}
        for mode in ("light", "full"):
            # Ensure the quota record exists (remaining() creates it if needed)
            remaining = QuotaTracker.remaining(platform, mode)
            quota = SyncQuota.objects.filter(
                platform=platform, mode=mode, date=today,
            ).first()
            result[platform][mode] = {
                "used": quota.used if quota else 0,
                "limit": quota.limit if quota else 0,
                "remaining": remaining,
            }
    return JsonResponse(result)


# ---------------------------------------------------------------------------
# Preview — lightweight API fetch, returns ghost markers (not saved to DB)
# ---------------------------------------------------------------------------

# In-memory cache of preview results keyed by task_id.
# Cleared when results are fetched or on next preview submit.
_preview_results: dict[str, dict] = {}  # task_id → {"caches": [...], "errors": [...]}


def _haversine_m(lat1, lon1, lat2, lon2):
    """Return distance in metres between two lat/lon points."""
    r = 6_371_000
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    return r * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _estimate_max_results(region_type, region_params):
    """Estimate a reasonable max_results for the GC API based on area size.

    The GC API v1 search doesn't support radius/box, so it returns the
    nearest N caches by distance.  We estimate how many to fetch based
    on area size (assuming ~20 caches/km² in a dense urban area) with
    generous headroom, capped at 500.
    """
    if region_type == "circle":
        _lat, _lon, radius_m = region_params
        area_km2 = math.pi * (radius_m / 1000) ** 2
    else:
        south, west, north, east = region_params[:4]  # first 4 are always bbox
        center_lat = (south + north) / 2
        height_km = (north - south) * 111.32
        width_km = (east - west) * 111.32 * math.cos(math.radians(center_lat))
        area_km2 = height_km * width_km
    # ~20 caches/km² × 5x headroom, min 100, max 500
    return max(100, min(500, int(area_km2 * 100)))


def _best_search_for_polygon(coords):
    """Given polygon coords [[lng, lat], ...], return the smaller API search shape.

    Compares bbox area vs circumscribed circle area (centroid + max vertex distance).
    Returns {'type': 'rect', 's', 'w', 'n', 'e'} or {'type': 'circle', 'lat', 'lon', 'radius_m'}.
    """
    end = len(coords)
    if end > 1 and coords[0] == coords[-1]:
        end -= 1
    c_lat = sum(c[1] for c in coords[:end]) / end
    c_lng = sum(c[0] for c in coords[:end]) / end
    r_m = max(_haversine_m(c_lat, c_lng, c[1], c[0]) for c in coords[:end])
    circle_area = math.pi * r_m ** 2
    lats = [c[1] for c in coords[:end]]
    lngs = [c[0] for c in coords[:end]]
    s, w, n, e = min(lats), min(lngs), max(lats), max(lngs)
    center_lat = (s + n) / 2
    h_m = (n - s) * 110540
    w_m = (e - w) * 111320 * math.cos(math.radians(center_lat))
    bbox_area = h_m * w_m
    if circle_area < bbox_area:
        return {"type": "circle", "lat": c_lat, "lon": c_lng, "radius_m": math.ceil(r_m)}
    return {"type": "rect", "s": s, "w": w, "n": n, "e": e}


def _simplify_path(path, min_spacing_m):
    """Skip points closer than min_spacing_m to the last kept point. O(n).

    Always keeps the first and last point.
    path: [[lng, lat], ...]; returns a new list.
    """
    if len(path) < 2:
        return path
    result = [path[0]]
    for pt in path[1:-1]:
        last = result[-1]
        if _haversine_m(last[1], last[0], pt[1], pt[0]) >= min_spacing_m:
            result.append(pt)
    result.append(path[-1])
    return result


def _corridor_boxes(path, width_m):
    """Compute per-segment API search shapes for a corridor.

    Path is first simplified so no segment is shorter than width_m, preventing
    redundant overlapping search shapes from dense GPS tracks.
    Each remaining segment is subdivided so the shorter box dimension ≤ 2×width_m.
    For each sub-piece the smaller of bbox vs circumscribed circle is chosen.
    path: [[lng, lat], ...]; width_m: half-width in metres.
    Returns [{'type': 'rect', ...} | {'type': 'circle', ...}, ...].
    """
    path = _simplify_path(path, width_m)
    max_minor_m = 2 * width_m
    searches = []
    for i in range(len(path) - 1):
        lng0, lat0 = path[i]
        lng1, lat1 = path[i + 1]
        avg_lat = (lat0 + lat1) / 2
        cos_lat = math.cos(math.radians(avg_lat))
        dx_m = (lng1 - lng0) * 111320 * cos_lat
        dy_m = (lat1 - lat0) * 110540
        minor = min(abs(dx_m), abs(dy_m))
        n = max(1, math.ceil(minor / max_minor_m))
        for j in range(n):
            t0, t1 = j / n, (j + 1) / n
            slat0 = lat0 + t0 * (lat1 - lat0)
            slng0 = lng0 + t0 * (lng1 - lng0)
            slat1 = lat0 + t1 * (lat1 - lat0)
            slng1 = lng0 + t1 * (lng1 - lng0)
            mid_lat = (slat0 + slat1) / 2
            mid_lng = (slng0 + slng1) / 2
            pad_lat = width_m / 110540
            pad_lng = width_m / (111320 * math.cos(math.radians(mid_lat)))
            # Bounding box
            s = min(slat0, slat1) - pad_lat
            w = min(slng0, slng1) - pad_lng
            n_lat = max(slat0, slat1) + pad_lat
            e = max(slng0, slng1) + pad_lng
            h_m = (n_lat - s) * 110540
            w_m2 = (e - w) * 111320 * math.cos(math.radians(mid_lat))
            bbox_area = h_m * w_m2
            # Circumscribed circle: r = sqrt(half_seg² + width_m²)
            half_len_m = _haversine_m(mid_lat, mid_lng, slat0, slng0)
            r_m = math.sqrt(half_len_m ** 2 + width_m ** 2)
            circle_area = math.pi * r_m ** 2
            if circle_area < bbox_area:
                searches.append({"type": "circle", "lat": mid_lat, "lon": mid_lng, "radius_m": math.ceil(r_m)})
            else:
                searches.append({"type": "rect", "s": s, "w": w, "n": n_lat, "e": e})
    return searches


def _run_preview_task(client, region_type, region_params, task_info=None, cancel_event=None):
    """Background task wrapper for preview search. Stores results for later retrieval."""
    from .sync.service import preview_by_bbox, preview_by_center, preview_by_boxes
    from .filters import _point_in_polygon, _dist_to_segment_km

    # For GC, limit search results based on area size (API has no bbox/radius)
    max_results = _estimate_max_results(region_type, region_params)

    if region_type == "circle":
        lat, lon, radius_m = region_params
        previews = preview_by_center(
            client, lat, lon, radius_m,
            cancel_event=cancel_event,
            task_info=task_info,
            max_results=max_results,
        )
    elif region_type == "corridor":
        _s, _w, _n, _e, path, width_m = region_params
        searches = _corridor_boxes(path, width_m)
        max_per_box = max(50, min(500, max_results // max(1, len(searches))))
        n_circles = sum(1 for s in searches if s["type"] == "circle")
        sync_log.info("  Corridor: %d searches (%d rects, %d circles), max_per=%d",
                      len(searches), len(searches) - n_circles, n_circles, max_per_box)
        previews = preview_by_boxes(
            client, searches,
            cancel_event=cancel_event,
            task_info=task_info,
            max_results_per_box=max_per_box,
        )
    elif region_type == "polygon":
        _s, _w, _n, _e, coords = region_params
        search = _best_search_for_polygon(coords)
        if search["type"] == "circle":
            sync_log.info("  Polygon: using circumscribed circle (r=%.0fm)", search["radius_m"])
            previews = preview_by_center(
                client, search["lat"], search["lon"], search["radius_m"],
                cancel_event=cancel_event,
                task_info=task_info,
                max_results=max_results,
            )
        else:
            previews = preview_by_bbox(
                client, _s, _w, _n, _e,
                cancel_event=cancel_event,
                task_info=task_info,
                max_results=max_results,
            )
    else:
        # rect
        south, west, north, east = region_params
        previews = preview_by_bbox(
            client, south, west, north, east,
            cancel_event=cancel_event,
            task_info=task_info,
            max_results=max_results,
        )

    # Filter previews to only include caches within the drawn region
    # (GC API search returns nearest-by-distance, not bounded by region)
    pre_filter = len(previews)
    if previews and region_type == "circle":
        lat, lon, radius_m = region_params
        previews = [
            p for p in previews
            if _haversine_m(lat, lon, p["lat"], p["lon"]) <= radius_m
        ]
    elif previews and region_type == "rect":
        south, west, north, east = region_params
        if previews:
            sample = previews[:3]
            sync_log.info("  Filter bounds: S=%.4f W=%.4f N=%.4f E=%.4f | sample: %s",
                         south, west, north, east,
                         [(p["code"], p["lat"], p["lon"]) for p in sample])
        previews = [
            p for p in previews
            if south <= p["lat"] <= north and west <= p["lon"] <= east
        ]
    elif previews and region_type == "polygon":
        _s, _w, _n, _e, coords = region_params
        previews = [p for p in previews if _point_in_polygon(p["lat"], p["lon"], coords)]
    elif previews and region_type == "corridor":
        _s, _w, _n, _e, path, width_m = region_params
        width_km = width_m / 1000
        previews = [
            p for p in previews
            if any(
                _dist_to_segment_km(p["lat"], p["lon"],
                                    path[i][1], path[i][0],
                                    path[i + 1][1], path[i + 1][0]) <= width_km
                for i in range(len(path) - 1)
            )
        ]
    if pre_filter != len(previews):
        sync_log.info("  Filtered %s previews: %d → %d within drawn region",
                       client.platform, pre_filter, len(previews))

    # Check which codes already exist in the database
    if previews:
        codes = [p["code"] for p in previews]
        existing_gc = set(
            Geocache.objects.filter(gc_code__in=codes).values_list("gc_code", flat=True)
        )
        existing_oc = set(
            Geocache.objects.filter(oc_code__in=codes).values_list("oc_code", flat=True)
        )
        existing = existing_gc | existing_oc
        for p in previews:
            p["in_db"] = p["code"] in existing

    # Store results for retrieval via map_preview_result
    if task_info:
        errors = task_info.result.get("errors", []) if task_info.result else []
        _preview_results[task_info.id] = {"caches": previews, "errors": errors}
        task_info.result = {"count": len(previews), "errors": errors}


def _parse_region(region: dict) -> tuple[str, tuple] | None:
    """Parse a region dict into (type, params) for search dispatch.

    Returns one of:
        ("rect",     (south, west, north, east))
        ("circle",   (lat, lon, radius_m))
        ("polygon",  (south, west, north, east, coordinates))   — bbox for API, coords for exact filter
        ("corridor", (south, west, north, east, path, width_m)) — bbox expanded by half-width
        None if invalid.
    """
    rtype = region.get("type")
    if rtype == "rect":
        bbox = region.get("bbox")
        if bbox and len(bbox) == 4:
            return ("rect", tuple(float(x) for x in bbox))
    elif rtype == "circle":
        center = region.get("center")
        radius_m = region.get("radius_m", 0)
        if center and len(center) == 2 and radius_m > 0:
            return ("circle", (float(center[0]), float(center[1]), float(radius_m)))
    elif rtype == "polygon":
        coords = region.get("coordinates", [])
        if len(coords) >= 3:
            lats = [c[1] for c in coords]
            lngs = [c[0] for c in coords]
            return ("polygon", (min(lats), min(lngs), max(lats), max(lngs), coords))
    elif rtype == "corridor":
        path = region.get("path", [])
        width_m = float(region.get("width_m", 1000))
        if len(path) >= 2:
            lats = [p[1] for p in path]
            lngs = [p[0] for p in path]
            avg_lat = sum(lats) / len(lats)
            pad_lat = width_m / 110540
            pad_lng = width_m / (111320 * math.cos(math.radians(avg_lat)))
            bbox = (min(lats) - pad_lat, min(lngs) - pad_lng,
                    max(lats) + pad_lat, max(lngs) + pad_lng)
            return ("corridor", bbox + (path, width_m))
    return None


@require_POST
def map_preview(request):
    """Submit preview tasks: search region + light fetch for each region × platform."""
    from .tasks import submit_task

    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    regions = data.get("regions", [])
    platforms = data.get("platforms", [])

    if not regions:
        return JsonResponse({"error": "No regions specified"}, status=400)
    if not platforms:
        return JsonResponse({"error": "No platforms selected"}, status=400)

    task_ids = []
    for region in regions:
        parsed = _parse_region(region)
        if not parsed:
            continue
        region_type, region_params = parsed

        # Build a human-readable task name
        if region_type == "circle":
            lat, lon, radius_m = region_params
            task_label = f"({lat:.2f},{lon:.2f} r={radius_m:.0f}m)"
        elif region_type == "corridor":
            s, w, n, e, path, width_m = region_params
            searches = _corridor_boxes(path, width_m)
            n_circles = sum(1 for sh in searches if sh["type"] == "circle")
            task_label = f"corridor ({len(searches)} searches: {len(searches)-n_circles}r+{n_circles}c, w={width_m:.0f}m)"
        elif region_type == "polygon":
            _s, _w, _n, _e, coords = region_params
            search = _best_search_for_polygon(coords)
            task_label = f"polygon ({search['type']} search)"
        else:
            s, w, n, e = region_params
            task_label = f"({s:.2f},{w:.2f} → {n:.2f},{e:.2f})"

        for platform in platforms:
            try:
                client = _make_client(platform)
            except Exception as exc:
                logger.error("Failed to create client for %s: %s", platform, exc)
                continue

            task_name = f"Preview {platform} {task_label}"
            task_id = submit_task(
                task_name,
                _run_preview_task,
                client, region_type, region_params,
            )
            task_ids.append(task_id)

    return JsonResponse({"task_ids": task_ids})


@require_GET
def map_preview_result(request, task_id):
    """Return preview results for a completed preview task."""
    from .tasks import get_task

    info = get_task(task_id)
    if not info:
        return JsonResponse({"error": "Task not found"}, status=404)

    state = info["state"]
    if state in ("pending", "running"):
        return JsonResponse({
            "state": state,
            "progress": info.get("progress_pct", 0),
            "phase": info.get("phase", ""),
        })

    if state == "failed":
        return JsonResponse({
            "state": "failed",
            "error": info.get("error", "Unknown error"),
        })

    if state == "cancelled":
        return JsonResponse({"state": "cancelled"})

    # Completed — return cached preview data
    stored = _preview_results.pop(task_id, {})
    caches = stored.get("caches", [])
    errors = stored.get("errors", [])
    return JsonResponse({
        "state": "completed",
        "caches": caches,
        "count": len(caches),
        "errors": errors,
    })


# ---------------------------------------------------------------------------
# Full sync — persistent API fetch with full details
# ---------------------------------------------------------------------------

def _run_sync_task(
    client, codes, tag_names, log_count, task_info=None, cancel_event=None,
):
    """Background task wrapper for sync_caches in FULL mode."""
    from .sync.base import SyncMode
    from .sync.service import sync_caches

    result = sync_caches(
        client, codes, SyncMode.FULL,
        tag_names=tag_names,
        cancel_event=cancel_event,
        task_info=task_info,
        log_count=log_count,
    )

    # Return the result dict so the task runner preserves it
    # (enrichment is triggered separately after page reload)
    return task_info.result if task_info else None


def _auto_enrich_synced(codes, platform):
    """Start enrichment for caches that were just API-synced."""
    from preferences.models import UserPreference
    if not UserPreference.get("enrich_auto", True):
        return
    fields = set()
    if UserPreference.get("enrich_elevation", True):
        fields.add("elevation")
    if UserPreference.get("enrich_location", True):
        fields.add("location")
    if not fields:
        return

    from .models import Geocache
    from django.db.models import Q
    if platform.startswith("oc"):
        qs = Geocache.objects.filter(oc_code__in=codes)
    else:
        qs = Geocache.objects.filter(gc_code__in=codes)
    if not qs.exists():
        return

    from .enrich_task import start_enrichment
    start_enrichment(qs, fields)


@require_POST
def map_sync(request):
    """Submit full sync tasks for codes grouped by platform."""
    from .tasks import submit_task

    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    platforms_codes = data.get("platforms", {})
    tags = data.get("tags", [])
    log_count = data.get("log_count", 5)

    if not platforms_codes:
        return JsonResponse({"error": "No platform codes specified"}, status=400)

    # Sanitise tags
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",") if t.strip()]
    tag_names = [t for t in tags if t] if tags else None

    task_ids = []
    for platform, codes in platforms_codes.items():
        if not codes:
            continue
        try:
            client = _make_client(platform)
        except Exception as exc:
            logger.error("Failed to create client for %s: %s", platform, exc)
            continue

        task_name = f"Sync {platform} ({len(codes)} caches, full)"
        task_id = submit_task(
            task_name,
            _run_sync_task,
            client, codes, tag_names, log_count,
        )
        task_ids.append(task_id)

    return JsonResponse({"task_ids": task_ids})


# ---------------------------------------------------------------------------
# Post-sync enrichment (triggered by JS after page reload)
# ---------------------------------------------------------------------------

@require_POST
def map_auto_enrich(request):
    """Start background enrichment for recently synced caches.

    Called by JS after the sync-complete page reload so the map redraws
    immediately with the synced caches while enrichment runs in the background.
    """
    try:
        platforms_codes = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    started = False
    for platform, codes in platforms_codes.items():
        if codes:
            _auto_enrich_synced(codes, platform)
            started = True

    return JsonResponse({"ok": True, "started": started})


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_client(platform: str):
    """Instantiate the appropriate sync client for a platform."""
    if platform == "gc":
        from .sync.gc_client import GCClient
        return GCClient()
    else:
        from .sync.oc_client import OCClient
        from accounts.models import UserAccount
        acct = UserAccount.objects.filter(platform=platform).first()
        user_id = acct.user_id if acct else ""
        return OCClient(platform=platform, user_id=user_id)
