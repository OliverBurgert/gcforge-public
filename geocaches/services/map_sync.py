"""Map preview + sync task implementations (non-HTTP).

Previously lived inline in geocaches/views/map.py as `_run_preview_task`,
`_run_sync_task`, and `_auto_enrich_synced`, with preview results held in
a module-level `_preview_results` dict. Preview results are now returned
by the task fn so the existing task runner (geocaches.tasks.runner) stores
them on `TaskInfo.result` instead of a side channel.
"""
import logging
import math

from ..geo import haversine_m
from ..models import Geocache

sync_log = logging.getLogger("geocaches.sync")


def _estimate_max_results(region_type, region_params):
    """Estimate max_results for the GC API given area size."""
    if region_type == "circle":
        _lat, _lon, radius_m = region_params
        area_km2 = math.pi * (radius_m / 1000) ** 2
    else:
        south, west, north, east = region_params[:4]
        center_lat = (south + north) / 2
        height_km = (north - south) * 111.32
        width_km = (east - west) * 111.32 * math.cos(math.radians(center_lat))
        area_km2 = height_km * width_km
    return max(100, min(500, int(area_km2 * 100)))


def _best_search_for_polygon(coords):
    """Given polygon coords [[lng, lat], ...], return the smaller API search shape."""
    end = len(coords)
    if end > 1 and coords[0] == coords[-1]:
        end -= 1
    c_lat = sum(c[1] for c in coords[:end]) / end
    c_lng = sum(c[0] for c in coords[:end]) / end
    r_m = max(haversine_m(c_lat, c_lng, c[1], c[0]) for c in coords[:end])
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
    """Skip points closer than min_spacing_m to the last kept point. Keeps first and last."""
    if len(path) < 2:
        return path
    result = [path[0]]
    for pt in path[1:-1]:
        last = result[-1]
        if haversine_m(last[1], last[0], pt[1], pt[0]) >= min_spacing_m:
            result.append(pt)
    result.append(path[-1])
    return result


def _corridor_boxes(path, width_m):
    """Compute per-segment API search shapes for a corridor."""
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
            s = min(slat0, slat1) - pad_lat
            w = min(slng0, slng1) - pad_lng
            n_lat = max(slat0, slat1) + pad_lat
            e = max(slng0, slng1) + pad_lng
            h_m = (n_lat - s) * 110540
            w_m2 = (e - w) * 111320 * math.cos(math.radians(mid_lat))
            bbox_area = h_m * w_m2
            half_len_m = haversine_m(mid_lat, mid_lng, slat0, slng0)
            r_m = math.sqrt(half_len_m ** 2 + width_m ** 2)
            circle_area = math.pi * r_m ** 2
            if circle_area < bbox_area:
                searches.append({"type": "circle", "lat": mid_lat, "lon": mid_lng, "radius_m": math.ceil(r_m)})
            else:
                searches.append({"type": "rect", "s": s, "w": w, "n": n_lat, "e": e})
    return searches


def run_preview_task(client, region_type, region_params, task_info=None, cancel_event=None):
    """Background task fn for preview search.

    Returns a dict: {"caches": [...], "errors": [...], "count": N}.
    The task runner stores the return value on task_info.result.
    """
    from ..sync.service import (
        preview_by_bbox, preview_by_center, preview_by_boxes, preview_by_criteria,
    )
    from ..geo import point_in_polygon, dist_to_segment_km

    max_results = 500 if region_type == "criteria" else _estimate_max_results(region_type, region_params)

    if region_type == "criteria":
        # region_params = (criteria_dict, bbox_or_None). Results are already
        # scoped by the search itself, so no geographic post-filter applies.
        criteria, bbox = region_params
        previews = preview_by_criteria(
            client, criteria, bbox=bbox,
            cancel_event=cancel_event,
            task_info=task_info,
            max_results=max_results,
        )
    elif region_type == "circle":
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
        sync_log.info(
            "  Corridor: %d searches (%d rects, %d circles), max_per=%d",
            len(searches), len(searches) - n_circles, n_circles, max_per_box,
        )
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
            if haversine_m(lat, lon, p["lat"], p["lon"]) <= radius_m
        ]
    elif previews and region_type == "rect":
        south, west, north, east = region_params
        if previews:
            sample = previews[:3]
            sync_log.info(
                "  Filter bounds: S=%.4f W=%.4f N=%.4f E=%.4f | sample: %s",
                south, west, north, east,
                [(p["code"], p["lat"], p["lon"]) for p in sample],
            )
        previews = [
            p for p in previews
            if south <= p["lat"] <= north and west <= p["lon"] <= east
        ]
    elif previews and region_type == "polygon":
        _s, _w, _n, _e, coords = region_params
        previews = [p for p in previews if point_in_polygon(p["lat"], p["lon"], coords)]
    elif previews and region_type == "corridor":
        _s, _w, _n, _e, path, width_m = region_params
        width_km = width_m / 1000
        previews = [
            p for p in previews
            if any(
                dist_to_segment_km(
                    p["lat"], p["lon"],
                    path[i][1], path[i][0],
                    path[i + 1][1], path[i + 1][0],
                ) <= width_km
                for i in range(len(path) - 1)
            )
        ]
    if pre_filter != len(previews):
        sync_log.info(
            "  Filtered %s previews: %d → %d within drawn region",
            client.platform, pre_filter, len(previews),
        )

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

    errors = []
    if task_info and task_info.result:
        errors = task_info.result.get("errors", [])

    return {"caches": previews, "count": len(previews), "errors": errors}


def run_sync_task(client, codes, tag_names, log_count, task_info=None, cancel_event=None):
    """Background task fn for sync_caches in FULL mode."""
    from ..sync.base import SyncMode
    from ..sync.service import sync_caches

    sync_caches(
        client, codes, SyncMode.FULL,
        tag_names=tag_names,
        cancel_event=cancel_event,
        task_info=task_info,
        log_count=log_count,
    )
    return task_info.result if task_info else None


def auto_enrich_synced_codes(codes, platform):
    """Start enrichment for caches that were just API-synced.

    Mirrors services._start_auto_enrich but filters by gc_code/oc_code
    rather than last_gpx_date, since API sync does not touch GPX fields.
    """
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

    if platform.startswith("oc"):
        qs = Geocache.objects.filter(oc_code__in=codes)
    else:
        qs = Geocache.objects.filter(gc_code__in=codes)
    if not qs.exists():
        return

    from ..tasks.enrich import start_enrichment
    start_enrichment(qs, fields)
