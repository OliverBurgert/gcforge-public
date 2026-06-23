"""
Residual URL-param filter helpers — the intentional, final survivors.

The canonical filter system is the ``?fx=`` expression tree
(:mod:`geocaches.filter_expr`), compiled to Django ``Q`` objects by
``compile_tree``.  Every common filter (``?type=`` / ``?status=`` /
``?fname=`` / dates / ranges / tags / attributes / …) lives inside ``?fx=``.

This module is **not** a deprecated codepath waiting to be retired.  It holds
the handful of URL params that genuinely cannot become pure-Q tree conditions
and therefore remain by design.  Each one has a single reason it stays:

  * ``?q=`` — multi-field quick search.  A free-text box OR-ing across
    gc/oc/al code + name + owner; not a single tree field/operator.
  * ``?elevation=`` — named-band buckets (``none``, ``lt0``, ``0-50``,
    ``gt3000``, …).  The ``none`` band needs a two-column predicate
    (``elevation`` *and* ``elevation_user`` both null) that the fx range
    compiler — which only sees ``elevation`` — can't express losslessly, so
    migrating it would silently change which caches count as "Not set".
  * ``?flag=`` — exotic flags only (``my_tb_inside``); simple booleans went to
    fx, and ``ftf_possible`` / ``alc_in_progress`` now ship as fx-tree
    built-in saved filters.  ``my_tb_inside`` needs a Python-built
    ``Trackable`` subquery keyed on the user's owned-trackable reference
    codes — not a tree field.
  * ``?geo=`` — drawn map areas (rect / circle / polygon / corridor).
    Circle / polygon / corridor require haversine / point-in-polygon math in
    Python over the narrowed queryset, so they can't be pure-Q.

ALC helpers ``apply_alc_in_progress_filter`` and ``apply_alc_loggable_filter``
are kept because the filter-expression compiler and ``apply_all`` reference
them directly (they back the ``alc.in_progress`` / ``alc.loggable_from_ref``
tree conditions).
"""

import math

from django.db import models
from django.db.models import Q

from .geo import dist_to_segment_km, haversine_km, point_in_polygon
from .models import EVENT_CACHE_TYPES, FOUND_LOG_TYPES


# ---------------------------------------------------------------------------
# Shared constants (used by tools_ftf.py / services/ftf.py — keep stable)
# ---------------------------------------------------------------------------

# Re-exported from models.enums (single source of truth). Kept under these
# legacy names so existing importers (tools_ftf, services/ftf, filter_expr)
# don't need to change their import paths.
EVENT_TYPES = EVENT_CACHE_TYPES

ELEVATION_RANGES = {
    "none":      Q(elevation__isnull=True, elevation_user__isnull=True),
    "lt0":       Q(elevation__lt=0),
    "eq0":       Q(elevation=0),
    "0-50":      Q(elevation__gte=0,    elevation__lt=50),
    "50-100":    Q(elevation__gte=50,   elevation__lt=100),
    "100-500":   Q(elevation__gte=100,  elevation__lt=500),
    "500-1000":  Q(elevation__gte=500,  elevation__lt=1000),
    "1000-3000": Q(elevation__gte=1000, elevation__lt=3000),
    "gt3000":    Q(elevation__gte=3000),
}


# ---------------------------------------------------------------------------
# URL-param → queryset functions
# ---------------------------------------------------------------------------

def apply_quick_search(qs, params):
    q = params.get("q", "").strip()
    if q:
        qs = qs.filter(
            Q(gc_code__icontains=q) | Q(oc_code__icontains=q) | Q(al_code__icontains=q)
            | Q(name__icontains=q) | Q(owner__icontains=q)
        )
    return qs


def apply_flag_filter(qs, params):
    """Exotic ``?flag=`` values only.

    The live toolbar emitter is ``my_tb_inside`` (a ``Trackable`` subquery the
    fx tree can't express).  Simple boolean flags are absorbed into ``?fx=`` by
    the normaliser, and ``ftf_possible`` / ``alc_in_progress`` now ship as
    fx-tree built-in saved filters — so the matching branches below are reached
    only by stale ``?flag=`` bookmarks from before the v2 cutover.  They are
    retained (cheap, lossless) so those URLs keep working rather than silently
    no-op-ing.  ``alc_loggable_at_center`` is handled by ``apply_all`` because
    it needs the reference point.
    """
    flag = params.get("flag", "")
    if flag == "alc_in_progress":
        return apply_alc_in_progress_filter(qs)
    if flag == "ftf_possible":
        from .query import mine_q
        return (
            qs.filter(found=False, completed=False, status="Active")
            .exclude(cache_type__in=EVENT_TYPES)
            .exclude(cache_type="Adventure Lab")
            .exclude(logs__log_type__in=FOUND_LOG_TYPES)
            .exclude(mine_q())
        )
    if flag == "my_tb_inside":
        return apply_my_tb_inside_filter(qs)
    return qs


def apply_my_tb_inside_filter(qs):
    """Caches whose CacheTrackableMention list includes a TB the user owns.

    "Owns" = ``Trackable.owner_name`` matches one of the user's GC/OC
    ``UserAccount`` usernames (case-insensitive), with the ``gc_username``
    preference as fallback.
    """
    from accounts.models import UserAccount
    from preferences.models import UserPreference

    names = {u.lower() for u in UserAccount.objects.values_list("username", flat=True) if u}
    fallback = (UserPreference.get("gc_username", "") or "").strip().lower()
    if fallback:
        names.add(fallback)
    if not names:
        return qs.none()

    from .models import Trackable
    my_refs = list(
        Trackable.objects
        .annotate(_owner_lc=models.functions.Lower("owner_name"))
        .filter(_owner_lc__in=names)
        .values_list("reference_code", flat=True)
    )
    if not my_refs:
        return qs.none()
    return qs.filter(trackable_mentions__ref_code__in=my_refs).distinct()


def apply_elevation_filter(qs, params):
    elevation = params.get("elevation", "")
    if elevation in ELEVATION_RANGES:
        qs = qs.filter(ELEVATION_RANGES[elevation])
    return qs


# ---------------------------------------------------------------------------
# Geographic area filter — rect bbox + circle/polygon/corridor (haversine)
# ---------------------------------------------------------------------------


def _parse_geo_param(geo_str: str) -> list:
    """Parse ``?geo=`` into region dicts.

    Supported formats (pipe-separated):
      rect:s,w,n,e
      circle:lat,lon,radius_m
      polygon:lng1,lat1,lng2,lat2,...
      corridor:width_m:lng1,lat1,lng2,lat2,...
    """
    regions = []
    for part in geo_str.split("|"):
        part = part.strip()
        if not part:
            continue
        try:
            if part.startswith("rect:"):
                coords = [float(x) for x in part[5:].split(",")]
                if len(coords) == 4:
                    regions.append({"type": "rect", "bbox": coords})
            elif part.startswith("circle:"):
                coords = [float(x) for x in part[7:].split(",")]
                if len(coords) == 3:
                    regions.append({"type": "circle", "center": [coords[0], coords[1]], "radius_m": coords[2]})
            elif part.startswith("polygon:"):
                vals = [float(x) for x in part[8:].split(",")]
                if len(vals) >= 6 and len(vals) % 2 == 0:
                    ring = [[vals[i], vals[i + 1]] for i in range(0, len(vals), 2)]
                    if ring[0] != ring[-1]:
                        ring.append(ring[0])  # close ring
                    regions.append({"type": "polygon", "coordinates": ring})
            elif part.startswith("corridor:"):
                rest = part[9:]
                colon = rest.index(":")
                width_m = float(rest[:colon])
                vals = [float(x) for x in rest[colon + 1:].split(",")]
                if len(vals) >= 4 and len(vals) % 2 == 0:
                    path = [[vals[i], vals[i + 1]] for i in range(0, len(vals), 2)]
                    regions.append({"type": "corridor", "path": path, "width_m": width_m})
        except ValueError:
            pass
    return regions


def apply_area_filter(qs, params):
    """Filter by geographic regions from the ``?geo=`` param (rect, circle, polygon, corridor)."""
    geo = params.get("geo", "").strip()
    if not geo:
        return qs

    regions = _parse_geo_param(geo)
    if not regions:
        return qs

    rect_regions     = [r for r in regions if r["type"] == "rect"]
    circle_regions   = [r for r in regions if r["type"] == "circle"]
    polygon_regions  = [r for r in regions if r["type"] == "polygon"]
    corridor_regions = [r for r in regions if r["type"] == "corridor"]

    need_python = circle_regions or polygon_regions or corridor_regions

    if not need_python:
        # Pure rect — handled entirely in DB
        q = Q()
        for r in rect_regions:
            s, w, n, e = r["bbox"]
            q |= Q(latitude__gte=s, latitude__lte=n, longitude__gte=w, longitude__lte=e)
        return qs.filter(q) if q else qs

    # Build union bounding box for DB pre-filter
    bbox_q = Q()
    for r in rect_regions:
        s, w, n, e = r["bbox"]
        bbox_q |= Q(latitude__gte=s, latitude__lte=n, longitude__gte=w, longitude__lte=e)
    for r in circle_regions:
        lat, lon = r["center"]
        r_km = r["radius_m"] / 1000.0
        dlat = r_km / 110.574
        dlon = r_km / (111.320 * math.cos(math.radians(lat)) + 1e-10)
        bbox_q |= Q(
            latitude__gte=lat - dlat, latitude__lte=lat + dlat,
            longitude__gte=lon - dlon, longitude__lte=lon + dlon,
        )
    for r in polygon_regions:
        lngs = [c[0] for c in r["coordinates"]]
        lats = [c[1] for c in r["coordinates"]]
        bbox_q |= Q(
            latitude__gte=min(lats), latitude__lte=max(lats),
            longitude__gte=min(lngs), longitude__lte=max(lngs),
        )
    for r in corridor_regions:
        lngs = [c[0] for c in r["path"]]
        lats = [c[1] for c in r["path"]]
        mid_lat = sum(lats) / len(lats)
        w_km = r["width_m"] / 1000.0
        dlat = w_km / 110.574
        dlon = w_km / (111.320 * math.cos(math.radians(mid_lat)) + 1e-10)
        bbox_q |= Q(
            latitude__gte=min(lats) - dlat, latitude__lte=max(lats) + dlat,
            longitude__gte=min(lngs) - dlon, longitude__lte=max(lngs) + dlon,
        )

    candidates = list(qs.filter(bbox_q).values_list("pk", "latitude", "longitude"))

    keep_pks = set()
    for pk, clat, clon in candidates:
        matched = False
        for r in rect_regions:
            s, w, n, e = r["bbox"]
            if s <= clat <= n and w <= clon <= e:
                matched = True
                break
        if not matched:
            for r in circle_regions:
                lat, lon = r["center"]
                if haversine_km(lat, lon, clat, clon) <= r["radius_m"] / 1000.0:
                    matched = True
                    break
        if not matched:
            for r in polygon_regions:
                if point_in_polygon(clat, clon, r["coordinates"]):
                    matched = True
                    break
        if not matched:
            for r in corridor_regions:
                w_km = r["width_m"] / 1000.0
                path = r["path"]
                for k in range(len(path) - 1):
                    lng1, lat1 = path[k]
                    lng2, lat2 = path[k + 1]
                    if dist_to_segment_km(clat, clon, lat1, lng1, lat2, lng2) <= w_km:
                        matched = True
                        break
                if matched:
                    break
        if matched:
            keep_pks.add(pk)

    return qs.filter(pk__in=keep_pks)


# ---------------------------------------------------------------------------
# ALC helpers — referenced from filter_expr (alc.in_progress, alc.loggable_from_ref)
# and from query.apply_all (legacy ?flag=alc_loggable_at_center path).
# ---------------------------------------------------------------------------


def apply_alc_in_progress_filter(qs):
    """Adventures with at least one completed stage and at least one remaining stage.

    Returns the parent Geocache AND all stages for qualifying adventures.
    """
    from django.db.models import Exists, OuterRef
    from .models import Adventure, Geocache as _Geocache

    stages = _Geocache.objects.filter(
        adventure_id=OuterRef("pk"),
        al_detail__isnull=False,
    )
    in_progress = Adventure.objects.annotate(
        has_done=Exists(stages.filter(Q(completed=True) | Q(found=True))),
        has_todo=Exists(stages.filter(completed=False, found=False)),
    ).filter(has_done=True, has_todo=True)

    return qs.filter(adventure__in=in_progress)


def apply_alc_loggable_filter(qs, ref):
    """Filter to ALC stage caches reachable from ref (distance ≤ geofencing_radius).

    Requires ``distance_km`` annotation (applied by ``annotate_distance``
    before this call).  Returns ``qs.none()`` when no reference point is
    active.
    """
    if ref is None:
        return qs.none()

    from django.db.models import ExpressionWrapper, F, FloatField
    return qs.filter(
        al_detail__geofencing_radius__isnull=False,
        al_detail__geofencing_radius__gt=0,
        distance_km__lte=ExpressionWrapper(
            F("al_detail__geofencing_radius") / 1000.0,
            output_field=FloatField(),
        ),
    )


# ---------------------------------------------------------------------------
# Filter chain — the intentional residuals.  Used by ``query.apply_filters``.
# These are the URL params that can't become pure-Q tree conditions (see the
# module docstring for the per-param reason); everything else lives in ``?fx=``.
# ---------------------------------------------------------------------------

FILTER_CHAIN = [
    apply_quick_search,
    apply_flag_filter,
    apply_elevation_filter,
    apply_area_filter,
]
