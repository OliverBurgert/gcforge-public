"""
Map marker endpoint — returns compact JSON for MapLibre rendering.
Area filter CRUD endpoints — SavedAreaFilter save/list/delete.
Preview & sync endpoints — API fetch from drawn map regions.
"""

import gzip
import json
import logging
import math
from collections import defaultdict

from django.http import HttpResponse, JsonResponse
from django.utils.cache import patch_vary_headers
from django.views.decorators.http import require_GET, require_POST, require_http_methods

from ..models import CacheType, CacheSize, CacheStatus, Geocache, SavedAreaFilter, SavedRoute, Waypoint, WaypointType
from ..filtering.query import apply_all
from preferences.models import UserPreference

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


# ---------------------------------------------------------------------------
# Marker wire format
# ---------------------------------------------------------------------------
#
# ``map_markers`` emits ``{"fields": [...], "rows": [[...], ...], "count": N}``:
# one array per marker whose values map positionally onto ``fields``, instead of
# one object per marker. For the developer's 68 k-cache profile that is 6.4 MB
# instead of 11.4 MB, and it skips 68 k dict allocations plus DjangoJSONEncoder.
#
# Absent values are ``null``; the optional columns are ordered by how often they
# are actually set, and trailing nulls are trimmed, so most rows stop after the
# eleven always-present columns and a short row simply leaves the remaining keys
# unset. The consumer is static/js/cache-map.js::_gcfDecodeMarkers, which
# expands the rows back into the marker objects the rest of the map modules read
# (m.c / m.la / m.aid / …) — that object shape is unchanged.
MARKER_FIELDS = (
    "c", "n", "la", "lo", "t", "sz", "d", "tr", "f", "s", "m",
    "aid", "sn", "gr", "cla", "clo", "oc", "gc", "dnf", "alp",
)

# Number of leading columns every row carries; the rest may be trimmed.
MARKER_REQUIRED_FIELDS = 11


def _marker_response(request, payload: bytes) -> HttpResponse:
    """JSON response for the marker payload, gzipped when the client accepts it.

    The payload is several MB of highly repetitive text. Level 1 costs ~35 ms
    of CPU on the 68 k-cache profile and cuts 6.4 MB to 2.5 MB — cheaper than
    pushing the extra bytes through the response pipeline, and no marker is
    dropped to get there.
    """
    if "gzip" in request.META.get("HTTP_ACCEPT_ENCODING", ""):
        response = HttpResponse(
            gzip.compress(payload, compresslevel=1, mtime=0),
            content_type="application/json",
        )
        response["Content-Encoding"] = "gzip"
    else:
        response = HttpResponse(payload, content_type="application/json")
    patch_vary_headers(response, ("Accept-Encoding",))
    return response


@require_GET
def map_markers(request):
    """Return compact marker JSON for all caches matching current filters.

    See MARKER_FIELDS above for the wire format.
    """
    qs = Geocache.objects.all()

    # Resolve reference point — same logic as the list view so radius/distance
    # filters are applied consistently.
    from preferences.services import resolve_active_reference_point
    distance_unit = UserPreference.get("distance_unit", "km")
    rrp = resolve_active_reference_point(request)
    ref = rrp.ref_point

    # Only annotate distance when radius or bearing is actually requested.
    radius_str = request.GET.get("radius", "").strip()
    bearing_str = request.GET.get("bearing", "").strip()
    needs_distance = bool(radius_str) or bool(bearing_str) or request.GET.get("flag") == "alc_loggable_at_center"

    # Ensure the distance cache is populated so the query uses a fast
    # indexed join instead of per-row Python haversine callbacks.
    if needs_distance and ref:
        from ..geo.distance_cache import ensure_cached
        ensure_cached(ref)

    qs, _fv = apply_all(
        qs, request.GET,
        ref=ref if needs_distance else None,
        distance_unit=distance_unit,
        # Always expose the active toolbar ref to fx compile fns
        # (e.g. alc.loggable_from_ref), even when we skip the distance
        # annotation for perf.
        compile_ref=ref,
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

    # Map-visibility filter — see docs/map-visibility.md §6.
    # Applied here ONLY (never in apply_all / list view). The list endpoint
    # intentionally shows map-hidden caches; only the map suppresses them.
    from django.db.models import Q
    from ..services.map_visibility import hidden_codes_in_session
    hidden_session = hidden_codes_in_session(request.session)
    qs = qs.filter(map_hidden_always=False)
    if hidden_session:
        qs = qs.exclude(
            Q(gc_code__in=hidden_session)
            | Q(oc_code__in=hidden_session)
            | Q(al_code__in=hidden_session),
        )

    # Pre-compute owner info for is_mine detection.  Same identity source as
    # mine_q() (the list view's ORM annotation) — applied here as a per-row
    # Python predicate over values_list rows for performance.
    from ..filtering.query import resolve_my_identities
    _ids = resolve_my_identities()
    mine_gc_ids = _ids.gc_owner_ids
    mine_usernames = _ids.usernames

    # Fetch only the columns we need via values_list for performance
    fields = (
        "gc_code", "al_code", "oc_code", "name", "latitude", "longitude",
        "cache_type", "size", "difficulty", "terrain",
        "found", "completed", "dnf", "status", "owner", "owner_gc_id",
        "adventure_id", "al_detail__stage_number", "al_detail__geofencing_radius",
        "is_al_parent",
    )

    from ..models import CorrectedCoordinates
    # Corrected coordinates, keyed by cache code. Fetched in one unfiltered
    # query before the marker cursor opens: the table only holds the handful of
    # caches the user has corrected (2,246 of 68,774 on the developer's
    # profile), so reading all of it is far cheaper than the ~77 chunked
    # ``geocache_id__in`` queries this used to need. Narrowing it with
    # ``geocache__in=qs`` is not an option — SQLite cannot optimise that
    # subquery when qs carries a RawSQL filter.
    corrected_map = {}
    for gc_code, al_code, oc_code, clat, clon in CorrectedCoordinates.objects.values_list(
        "geocache__gc_code", "geocache__al_code", "geocache__oc_code", "latitude", "longitude",
    ):
        corrected_map[gc_code or al_code or oc_code] = (round(clat, 6), round(clon, 6))

    # The pk column is unused below but stays in the SELECT: one filter applies
    # .distinct() (filters.py, trackable mentions), and dropping the pk would
    # let two caches that happen to agree on every selected column collapse
    # into one marker. Streamed with iterator() so the 68 k source tuples are
    # never all alive at once alongside the rows being built from them.
    rows = qs.values_list("pk", *fields).iterator(chunk_size=2000)

    # Hoist every lookup out of the per-marker loop — it runs 68 k times.
    type_short, size_short, status_short = TYPE_SHORT, SIZE_SHORT, STATUS_SHORT
    corrected_get = corrected_map.get
    check_mine = bool(mine_gc_ids or mine_usernames)
    n_fields = len(MARKER_FIELDS)

    out_rows = []
    append_row = out_rows.append
    for (_pk, gc_code, al_code, oc_code, name, lat, lon, cache_type, size,
         diff, terr, found, completed, dnf, status, owner, owner_gc_id,
         adventure_id, stage_number, geofencing_radius, is_al_parent) in rows:
        # Skip caches with no coordinates (would crash round())
        if lat is None or lon is None:
            continue

        code = gc_code or al_code or oc_code
        is_found = bool(found or completed)
        is_mine = bool(
            check_mine and (
                (owner_gc_id and owner_gc_id in mine_gc_ids)
                or (owner and owner in mine_usernames)
            ),
        )
        corr = corrected_get(code)

        row = [
            code,
            name[:60],
            round(lat, 6),
            round(lon, 6),
            type_short.get(cache_type, "?"),
            size_short.get(size, "U"),
            diff,
            terr,
            is_found,
            status_short.get(status, "A"),
            is_mine,
            adventure_id,
            stage_number if adventure_id is not None else None,
            geofencing_radius if adventure_id is not None else None,
            corr[0] if corr else None,
            corr[1] if corr else None,
            # Both codes only when the cache has a secondary one (dual links).
            oc_code or None,
            gc_code if (gc_code and oc_code) else None,
            # DNF flag (only when DNFed and not found — found wins over DNF)
            True if (dnf and not is_found) else None,
            # Adventure Lab parent flag — lets the client tell a stage (aid
            # set, alp absent) apart from the parent (aid set, alp true).
            True if is_al_parent else None,
        ]
        end = n_fields
        while end > MARKER_REQUIRED_FIELDS and row[end - 1] is None:
            end -= 1
        del row[end:]
        append_row(row)

    payload = json.dumps(
        {"fields": list(MARKER_FIELDS), "rows": out_rows, "count": len(out_rows)},
        separators=(",", ":"),
    ).encode()
    return _marker_response(request, payload)


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
# Route planning — BRouter road routing (feeds the corridor filter)
# ---------------------------------------------------------------------------


def _parse_lonlats(raw: str) -> list[tuple[float, float]]:
    """Parse a ``lon,lat|lon,lat|…`` string into a list of (lon, lat) tuples."""
    out = []
    for part in raw.split("|"):
        bits = part.split(",")
        if len(bits) >= 2:
            try:
                out.append((float(bits[0]), float(bits[1])))
            except ValueError:
                continue
    return out


@require_http_methods(["GET", "POST"])
def map_route(request):
    """Compute a road route via BRouter.

    POST {lonlats:[[lon,lat],…], profile} → JSON {path, distance_m, duration_s, ascend_m}.
    GET  ?lonlats=lon,lat|…&profile=&format=gpx → GPX file download.
    """
    from ..sync.brouter_client import BRouterError, fetch_route, route_summary

    if request.method == "GET":
        lonlats = _parse_lonlats(request.GET.get("lonlats", "").strip())
        profile = request.GET.get("profile", "hiking-beta").strip() or "hiking-beta"
        if len(lonlats) < 2:
            return JsonResponse({"error": "at least two waypoints are required"}, status=400)
        try:
            gpx = fetch_route(lonlats, profile, "gpx")
        except BRouterError as exc:
            return JsonResponse({"error": str(exc)}, status=502)
        resp = HttpResponse(gpx, content_type="application/gpx+xml")
        resp["Content-Disposition"] = 'attachment; filename="route.gpx"'
        return resp

    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    profile = (data.get("profile") or "hiking-beta").strip() or "hiking-beta"
    try:
        lonlats = [(float(p[0]), float(p[1])) for p in (data.get("lonlats") or [])]
    except (TypeError, ValueError, IndexError):
        return JsonResponse({"error": "invalid lonlats"}, status=400)
    if len(lonlats) < 2:
        return JsonResponse({"error": "at least two waypoints are required"}, status=400)

    try:
        summary = route_summary(lonlats, profile)
    except BRouterError as exc:
        return JsonResponse({"error": str(exc)}, status=502)
    return JsonResponse(summary)


# ---------------------------------------------------------------------------
# Saved route CRUD — named waypoint lists for reuse over time
# ---------------------------------------------------------------------------


@require_GET
def saved_routes_list(request):
    routes = list(SavedRoute.objects.values(
        "id", "name", "waypoints", "profile", "width_m", "path", "updated_at",
    ))
    for r in routes:
        r["updated_at"] = r["updated_at"].isoformat() if r["updated_at"] else None
    return JsonResponse({"routes": routes})


@require_POST
def saved_route_save(request):
    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    name = (data.get("name") or "").strip()
    waypoints = data.get("waypoints")
    if not name:
        return JsonResponse({"error": "name is required"}, status=400)
    if not waypoints or not isinstance(waypoints, list):
        return JsonResponse({"error": "waypoints must be a non-empty list"}, status=400)

    route, created = SavedRoute.objects.update_or_create(
        name=name,
        defaults={
            "waypoints": waypoints,
            "profile": (data.get("profile") or "hiking-beta").strip() or "hiking-beta",
            "width_m": int(data.get("width_m") or 1000),
            "path": data.get("path") or [],
        },
    )
    return JsonResponse({"id": route.pk, "name": route.name, "created": created})


@require_http_methods(["DELETE"])
def saved_route_delete(request, pk):
    try:
        SavedRoute.objects.get(pk=pk).delete()
        return JsonResponse({"ok": True})
    except SavedRoute.DoesNotExist:
        return JsonResponse({"error": "Not found"}, status=404)


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
            "capabilities": _criteria_capabilities(acct),
        })
    return JsonResponse({"providers": providers})


def _criteria_capabilities(acct) -> dict:
    """Per-provider support flags for criteria-search fields (UI gating).

    GC (website search) supports everything. OC has no favourite points, and
    personal found-status filtering needs Level 3 (OAuth) credentials.
    """
    if acct.platform == "gc":
        return {"min_fav": True, "found_status": True}
    from accounts.keyring_util import get_oauth_token
    has_level3 = bool(acct.user_id and get_oauth_token(acct.platform, acct.user_id))
    return {"min_fav": False, "found_status": has_level3}


@require_GET
def map_quota(request):
    """Return remaining daily quota for requested platforms."""
    from datetime import date as date_mod
    from ..models import SyncQuota
    from ..sync.rate_limiter import QuotaTracker

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
                from ..sync.service import refresh_membership_level
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

from ..services.map_sync import (  # noqa: E402
    _best_search_for_polygon,
    _corridor_boxes,
    auto_enrich_synced_codes as _auto_enrich_synced,
    run_preview_task as _run_preview_task,
    run_sync_task as _run_sync_task,
    run_sync_task_web as _run_sync_task_web,
)


def _circle_to_bbox(lat: float, lon: float, radius_m: float) -> tuple[float, float, float, float]:
    """South/west/north/east box bounding a circle.

    Used to narrow a criteria search to a drawn circle: GC's website search and
    OKAPI's criteria endpoint both only accept a rectangular box param, so the
    circle is over-approximated for the query and trimmed to the exact radius
    afterward by ``_filter_previews_to_shape``.
    """
    pad_lat = radius_m / 110540
    pad_lng = radius_m / (111320 * math.cos(math.radians(lat)))
    return (lat - pad_lat, lon - pad_lng, lat + pad_lat, lon + pad_lng)


def _parse_region(region: dict) -> tuple[str, tuple] | None:
    """Parse a region dict into (type, params) for search dispatch.

    Returns one of:
        ("rect",     (south, west, north, east))
        ("circle",   (lat, lon, radius_m))
        ("polygon",  (south, west, north, east, coordinates))   — bbox for API, coords for exact filter
        ("corridor", (south, west, north, east, path, width_m)) — bbox expanded by half-width
        ("criteria", (criteria, bbox, shape_type, shape_params))
            — bbox narrows the API query (None = worldwide, or a plain
              viewport bbox from the "limit to map view" checkbox);
              shape_type/shape_params (one of the tuples above, or None) drive
              the exact-shape post-filter when "search only in drawn shape"
              was checked.
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
    elif rtype == "criteria":
        criteria = region.get("criteria")
        if not isinstance(criteria, dict):
            return None
        shape = region.get("shape")
        if isinstance(shape, dict):
            parsed_shape = _parse_region(shape)
            if parsed_shape:
                shape_type, shape_params = parsed_shape
                if shape_type == "circle":
                    bbox = _circle_to_bbox(*shape_params)
                else:
                    bbox = tuple(shape_params[:4])
                return ("criteria", (criteria, bbox, shape_type, shape_params))
        # No (parseable) shape — fall back to a plain bbox, e.g. from the
        # "limit to current map view" checkbox. None = worldwide.
        raw_bbox = region.get("bbox")
        bbox = (
            tuple(float(x) for x in raw_bbox)
            if raw_bbox and len(raw_bbox) == 4 else None
        )
        return ("criteria", (criteria, bbox, None, None))
    return None


@require_POST
def map_preview(request):
    """Submit preview tasks: search region + light fetch for each region × platform."""
    from ..tasks import submit_task

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
        elif region_type == "criteria":
            _criteria, _bbox, _shape_type, _shape_params = region_params
            task_label = "by criteria" + (
                f" (in drawn {_shape_type})" if _shape_type else
                " (in map view)" if _bbox else ""
            )
        else:
            s, w, n, e = region_params
            task_label = f"({s:.2f},{w:.2f} → {n:.2f},{e:.2f})"

        for platform in platforms:
            try:
                client = _make_preview_client(platform, region_type)
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
    from ..tasks import get_task

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

    # A cancel request may race with the task finishing on its own — the
    # background thread still writes whatever it fetched (or completed) to
    # task_info.result regardless of the cancelled flag, so forward it here
    # instead of discarding it.
    result = info.get("result") or {}
    caches = result.get("caches", [])
    errors = list(result.get("errors", []))
    if result.get("error"):
        errors.append(result["error"])

    if state == "cancelled":
        return JsonResponse({
            "state": "cancelled",
            "caches": caches,
            "count": len(caches),
            "errors": errors,
        })

    # Completed — return data from the task's result field
    # (run_preview_task returns {"caches": [...], "count": N, "errors": [...]})
    return JsonResponse({
        "state": "completed",
        "caches": caches,
        "count": len(caches),
        "errors": errors,
    })


# ---------------------------------------------------------------------------
# Full sync — persistent API fetch with full details
# ---------------------------------------------------------------------------

@require_POST
def map_sync(request):
    """Submit full sync tasks for codes grouped by platform."""
    from ..tasks import submit_task

    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    platforms_codes = data.get("platforms", {})
    tags = data.get("tags", [])
    log_count = data.get("log_count", 0)

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

        if platform == "gc" and _use_web_gc_client():
            task_name = f"Sync gc ({len(codes)} caches, full, via website)"
            task_id = submit_task(
                task_name,
                _run_sync_task_web,
                codes, tag_names,
            )
            task_ids.append(task_id)
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
        from geocaches.sync.gc_access import get_gc_client
        return get_gc_client()
    else:
        from ..sync.oc_client import OCClient
        from accounts.models import UserAccount
        acct = UserAccount.objects.filter(platform=platform).first()
        user_id = acct.user_id if acct else ""
        return OCClient(platform=platform, user_id=user_id)


def _use_web_gc_client() -> bool:
    """True when GC access should go through the website rather than the
    official partner API — ``gc_access_mode`` set to ``web_preferred``, or
    the official API simply not being available in this build. Static
    selection only (no live-failure retry the way
    ``gc_access.submit_gc_log()`` has); shared by ``_make_preview_client()``
    (bbox/circle/corridor/polygon search, added 2026-08-16) and
    ``map_sync()`` (full sync via ``cache_fetch.py``, added the same day).
    """
    from geocaches.sync import gc_access
    from geocaches.feature_flags import gc_api_available
    return gc_access.gc_access_mode() == "web_preferred" or not gc_api_available()


def _make_preview_client(platform: str, region_type: str):
    """Client for the preview (discovery) step.

    GC criteria search always uses the website API proxy (GCWebClient) — the
    partner API v1 can't express owner/type filters, regardless of
    ``gc_access_mode``.

    Plain bbox/circle search is also fully servable by ``GCWebClient``
    (**confirmed live 2026-08-15**: its ``search_lite_by_bbox()``/
    ``search_lite_by_center()`` are just ``search_criteria_lite()`` with an
    empty criteria dict — the ``box`` query param is independent of every
    other filter, so this "just works" with no server-side surprises) — used
    per ``_use_web_gc_client()``.

    Full syncs (saving caches, not just previewing them) can also go
    web-only now — see ``map_sync()`` — composed from this same search plus
    ``cache_fetch.py``'s bookmark-list-GPX mechanism rather than a
    ``BasePlatformClient``-conformant client, so it isn't selected here.
    """
    if platform == "gc":
        if region_type == "criteria" or _use_web_gc_client():
            from geocaches.sync.gc_web.search_client import GCWebClient
            return GCWebClient()
    return _make_client(platform)


# ---------------------------------------------------------------------------
# Adventure Lab fetch endpoints
# ---------------------------------------------------------------------------

@require_POST
def map_al_fetch_circles(request):
    """Start an AL fetch for a list of circles drawn on the map.

    POST body: {"circles": [{"lat": float, "lon": float, "radius_m": int}, ...],
                "tags": [...]}
    """
    from ..tasks import submit_task

    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    circles = data.get("circles", [])
    if not circles:
        return JsonResponse({"error": "No circles provided"}, status=400)

    tags = data.get("tags", [])
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",") if t.strip()]
    tag_names = [t for t in tags if t] or None

    def _run(_circles, _tags, task_info=None):
        from geocaches.sync.service import sync_al_by_circles
        cancel_event = getattr(task_info, "cancel_event", None)
        result = sync_al_by_circles(
            _circles, tags=_tags, cancel_event=cancel_event, task_info=task_info,
        )
        return {
            "created": result.created,
            "updated": result.updated,
            "failed": result.failed,
            "errors": result.errors[:20],
        }

    label = f"Fetch ALCs in {len(circles)} circle{'s' if len(circles) != 1 else ''}"
    task_id = submit_task(label, _run, circles, tag_names)
    return JsonResponse({"task_id": task_id})


@require_POST
def map_al_refresh_bbox(request):
    """Refresh AL adventures already in the DB within a bounding box.

    POST body: {"south": float, "west": float, "north": float, "east": float,
                "tags": [...]}
    """
    from ..tasks import submit_task

    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    try:
        south = float(data["south"])
        west = float(data["west"])
        north = float(data["north"])
        east = float(data["east"])
    except (KeyError, TypeError, ValueError):
        return JsonResponse({"error": "south/west/north/east required"}, status=400)

    tags = data.get("tags", [])
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",") if t.strip()]
    tag_names = [t for t in tags if t] or None

    def _run(_s, _w, _n, _e, _tags, task_info=None):
        from geocaches.sync.service import sync_al_in_bbox
        cancel_event = getattr(task_info, "cancel_event", None)
        result = sync_al_in_bbox(
            _s, _w, _n, _e, tags=_tags, cancel_event=cancel_event, task_info=task_info,
        )
        return {
            "created": result.created,
            "updated": result.updated,
            "failed": result.failed,
            "errors": result.errors[:20],
        }

    task_id = submit_task("Refresh ALCs in area", _run, south, west, north, east, tag_names)
    return JsonResponse({"task_id": task_id})
