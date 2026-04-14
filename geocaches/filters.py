"""
Registry-based filter chain for geocache list queries.

Each filter function has the signature:

    def apply_<name>(qs, params: dict) -> QuerySet

where *params* is ``request.GET`` (a QueryDict).  Functions return the
(possibly narrowed) queryset — they never mutate *params*.
"""

import math

from django.db.models import Q

from .models import SavedWhereClause

# ---------------------------------------------------------------------------
# Helper constants
# ---------------------------------------------------------------------------

FLAG_FIELDS = {
    "ftf": "ftf", "dnf": "dnf", "user_flag": "user_flag",
    "is_premium": "is_premium", "has_trackable": "has_trackable",
    "has_corrected_coordinates": "has_corrected_coordinates",
    "import_locked": "import_locked", "needs_maintenance": "needs_maintenance",
    "watch": "watch",
}

EVENT_TYPES = frozenset({
    "Event", "CITO", "Mega-Event", "Giga-Event",
    "Community Celebration Event", "Geocaching HQ Celebration",
    "Geocaching HQ Block Party",
})

FOUND_LOG_TYPES = ("Found it", "Attended", "Webcam Photo Taken")

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
# Helpers
# ---------------------------------------------------------------------------


def _parse_date(s: str):
    """Parse YYYY-MM-DD string to date, or return None."""
    from datetime import date as _date
    try:
        return _date.fromisoformat(s.strip())
    except (ValueError, AttributeError):
        return None


def _apply_text_op(qs, field: str, op: str, value: str):
    """Apply a text operator to a queryset field. Returns modified qs."""
    if not value:
        return qs
    if op == "not_contains":
        return qs.exclude(**{f"{field}__icontains": value})
    if op == "starts_with":
        return qs.filter(**{f"{field}__istartswith": value})
    if op == "not_starts_with":
        return qs.exclude(**{f"{field}__istartswith": value})
    if op == "equals":
        return qs.filter(**{f"{field}__iexact": value})
    if op == "not_equals":
        return qs.exclude(**{f"{field}__iexact": value})
    if op == "in_list":
        items = [v.strip() for v in value.split(";") if v.strip()]
        return qs.filter(**{f"{field}__in": items}) if items else qs
    if op == "not_in_list":
        items = [v.strip() for v in value.split(";") if v.strip()]
        return qs.exclude(**{f"{field}__in": items}) if items else qs
    if op == "empty":
        return qs.filter(Q(**{field: ""}) | Q(**{f"{field}__isnull": True}))
    if op == "not_empty":
        return qs.exclude(Q(**{field: ""}) | Q(**{f"{field}__isnull": True}))
    # default: contains
    return qs.filter(**{f"{field}__icontains": value})


# ---------------------------------------------------------------------------
# Individual filter functions
# ---------------------------------------------------------------------------


def apply_quick_search(qs, params):
    q = params.get("q", "").strip()
    if q:
        qs = qs.filter(
            Q(gc_code__icontains=q) | Q(oc_code__icontains=q)
            | Q(name__icontains=q) | Q(owner__icontains=q)
        )
    return qs


def apply_type_filter(qs, params):
    cache_type = params.get("type", "")
    if cache_type:
        qs = qs.filter(cache_type=cache_type)
    types = params.get("types", "")
    if types:
        type_list = [t.strip() for t in types.split(",") if t.strip()]
        if type_list:
            qs = qs.filter(cache_type__in=type_list)
    return qs


def apply_status_filter(qs, params):
    status = params.get("status", "")
    if status:
        qs = qs.filter(status=status)
    statuses = params.get("statuses", "")
    if statuses:
        status_list = [s.strip() for s in statuses.split(",") if s.strip()]
        if status_list:
            qs = qs.filter(status__in=status_list)
    return qs


def apply_size_filter(qs, params):
    size = params.get("size", "")
    if size:
        qs = qs.filter(Q(size_override=size) | Q(size_override__isnull=True, size=size))
    sizes = params.get("sizes", "")
    if sizes:
        size_list = [s.strip() for s in sizes.split(",") if s.strip()]
        if size_list:
            qs = qs.filter(
                Q(size_override__in=size_list) | Q(size_override__isnull=True, size__in=size_list)
            )
    return qs


def apply_found_filter(qs, params):
    found = params.get("found", "")
    if found == "1":
        qs = qs.filter(Q(found=True) | Q(completed=True))
    elif found == "0":
        qs = qs.filter(found=False, completed=False)
    return qs


def apply_flag_filter(qs, params):
    flag = params.get("flag", "")
    if flag == "ftf_possible":
        from .query import mine_q
        qs = (
            qs.filter(found=False, completed=False, status="Active")
            .exclude(cache_type__in=EVENT_TYPES)
            .exclude(cache_type="Adventure Lab")
            .exclude(logs__log_type__in=FOUND_LOG_TYPES)
            .exclude(mine_q())
        )
    elif flag in FLAG_FIELDS:
        qs = qs.filter(**{FLAG_FIELDS[flag]: True})
    return qs


def apply_elevation_filter(qs, params):
    elevation = params.get("elevation", "")
    if elevation in ELEVATION_RANGES:
        qs = qs.filter(ELEVATION_RANGES[elevation])
    return qs


def apply_tag_filter(qs, params):
    tag = params.get("tag", "")
    if tag == "__none__":
        qs = qs.filter(tags__isnull=True)
    elif tag:
        qs = qs.filter(tags__name=tag)
    return qs


def apply_tag_advanced_filter(qs, params):
    """Include/exclude multiple tags (from dialog Tags tab)."""
    include = params.get("tags_include", "")
    if include:
        for tag_name in (t.strip() for t in include.split(",") if t.strip()):
            if tag_name == "__none__":
                qs = qs.filter(tags__isnull=True)
            else:
                qs = qs.filter(tags__name=tag_name)

    exclude = params.get("tags_exclude", "")
    if exclude:
        for tag_name in (t.strip() for t in exclude.split(",") if t.strip()):
            if tag_name == "__none__":
                qs = qs.exclude(tags__isnull=True)
            else:
                qs = qs.exclude(tags__name=tag_name)
    return qs


def apply_country_filter(qs, params):
    country = params.get("country", "")
    if country == "__none__":
        qs = qs.filter(iso_country_code="")
    elif country:
        qs = qs.filter(iso_country_code=country)

    # Negative country filter
    country_exc = params.get("country_exclude", "")
    if country_exc:
        for code in (c.strip() for c in country_exc.split(",") if c.strip()):
            qs = qs.exclude(iso_country_code=code)

    state = params.get("state", "")
    if state == "__none__":
        qs = qs.filter(state="")
    elif state:
        qs = qs.filter(state=state)

    # Negative state filter
    state_exc = params.get("state_exclude", "")
    if state_exc:
        for s in (x.strip() for x in state_exc.split(",") if x.strip()):
            qs = qs.exclude(state=s)

    county = params.get("county", "")
    if county == "__none__":
        qs = qs.filter(county="")
    elif county:
        qs = qs.filter(county=county)

    # Negative county filter
    county_exc = params.get("county_exclude", "")
    if county_exc:
        for c in (x.strip() for x in county_exc.split(",") if x.strip()):
            qs = qs.exclude(county=c)

    return qs


def apply_missing_filter(qs, params):
    missing = params.get("missing", "")
    if missing == "any":
        qs = qs.filter(
            Q(elevation__isnull=True, elevation_user__isnull=True)
            | Q(iso_country_code="") | Q(state="") | Q(county="")
        )
    return qs


def apply_text_filters(qs, params):
    fname = params.get("fname", "").strip()
    fname_op = params.get("fname_op", "contains")
    if fname or fname_op in ("empty", "not_empty"):
        qs = _apply_text_op(qs, "name", fname_op, fname)

    fcode = params.get("fcode", "").strip()
    fcode_op = params.get("fcode_op", "contains")
    if fcode:
        if fcode_op == "not_contains":
            qs = qs.exclude(Q(gc_code__icontains=fcode) | Q(oc_code__icontains=fcode))
        else:
            qs = qs.filter(Q(gc_code__icontains=fcode) | Q(oc_code__icontains=fcode))

    fowner = params.get("fowner", "").strip()
    fowner_op = params.get("fowner_op", "contains")
    if fowner or fowner_op in ("empty", "not_empty"):
        qs = _apply_text_op(qs, "owner", fowner_op, fowner)

    fplacedby = params.get("fplacedby", "").strip()
    fplacedby_op = params.get("fplacedby_op", "contains")
    if fplacedby or fplacedby_op in ("empty", "not_empty"):
        qs = _apply_text_op(qs, "placed_by", fplacedby_op, fplacedby)

    ftext = params.get("ftext", "").strip()
    if ftext:
        qs = qs.filter(
            Q(short_description__icontains=ftext)
            | Q(long_description__icontains=ftext)
            | Q(hint__icontains=ftext)
        )
    return qs


def apply_range_filters(qs, params):
    def _float(key):
        try:
            v = params.get(key, "").strip()
            return float(v) if v else None
        except ValueError:
            return None

    def _int(key):
        try:
            v = params.get(key, "").strip()
            return int(v) if v else None
        except ValueError:
            return None

    diff_min = _float("diff_min")
    diff_max = _float("diff_max")
    if diff_min is not None:
        qs = qs.filter(difficulty__gte=diff_min)
    if diff_max is not None:
        qs = qs.filter(difficulty__lte=diff_max)

    terr_min = _float("terr_min")
    terr_max = _float("terr_max")
    if terr_min is not None:
        qs = qs.filter(terrain__gte=terr_min)
    if terr_max is not None:
        qs = qs.filter(terrain__lte=terr_max)

    fav_min = _int("fav_min")
    fav_max = _int("fav_max")
    if fav_min is not None:
        qs = qs.filter(fav_points__gte=fav_min)
    if fav_max is not None:
        qs = qs.filter(fav_points__lte=fav_max)
    return qs


def apply_date_filters(qs, params):
    hidden_from = _parse_date(params.get("hidden_from", ""))
    hidden_to = _parse_date(params.get("hidden_to", ""))
    if hidden_from:
        qs = qs.filter(hidden_date__gte=hidden_from)
    if hidden_to:
        qs = qs.filter(hidden_date__lte=hidden_to)

    lf_from = _parse_date(params.get("lf_from", ""))
    lf_to = _parse_date(params.get("lf_to", ""))
    if lf_from:
        qs = qs.filter(last_found_date__gte=lf_from)
    if lf_to:
        qs = qs.filter(last_found_date__lte=lf_to)

    fd_from = _parse_date(params.get("fd_from", ""))
    fd_to = _parse_date(params.get("fd_to", ""))
    if fd_from:
        qs = qs.filter(found_date__gte=fd_from)
    if fd_to:
        qs = qs.filter(found_date__lte=fd_to)
    return qs


def apply_multi_flag_filter(qs, params):
    flags = params.get("flags", "")
    if flags:
        for fl in (f.strip() for f in flags.split(",") if f.strip()):
            if fl == "corrected_coords":
                qs = qs.filter(has_corrected_coordinates=True)
            elif fl in FLAG_FIELDS:
                qs = qs.filter(**{FLAG_FIELDS[fl]: True})

    flags_not = params.get("flags_not", "")
    if flags_not:
        for fl in (f.strip() for f in flags_not.split(",") if f.strip()):
            if fl == "corrected_coords":
                qs = qs.filter(has_corrected_coordinates=False)
            elif fl in FLAG_FIELDS:
                qs = qs.filter(**{FLAG_FIELDS[fl]: False})
    return qs


def apply_attribute_filter(qs, params):
    attrs_yes = params.get("attrs_yes", "")
    if attrs_yes:
        for attr_id in attrs_yes.split(","):
            try:
                qs = qs.filter(attributes__id=int(attr_id.strip()))
            except ValueError:
                pass

    attrs_no = params.get("attrs_no", "")
    if attrs_no:
        for attr_id in attrs_no.split(","):
            try:
                qs = qs.exclude(attributes__id=int(attr_id.strip()))
            except ValueError:
                pass
    return qs


# ---------------------------------------------------------------------------
# Geographic area filter — rect bbox + circle (haversine) regions
# ---------------------------------------------------------------------------


def _parse_geo_param(geo_str: str) -> list:
    """Parse ?geo= into region dicts.

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


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2)
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _point_in_polygon(lat: float, lon: float, ring: list) -> bool:
    """Ray-casting point-in-polygon. ring is [[lng, lat], ...] closed ring."""
    inside = False
    n = len(ring) - 1
    j = n - 1
    for i in range(n):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        if ((yi > lat) != (yj > lat)) and lon < (xj - xi) * (lat - yi) / (yj - yi + 1e-15) + xi:
            inside = not inside
        j = i
    return inside


def _dist_to_segment_km(lat: float, lon: float, lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Minimum km distance from point to line segment (flat projection for t, haversine for distance)."""
    dx = lon2 - lon1
    dy = lat2 - lat1
    len2 = dx * dx + dy * dy
    if len2 < 1e-14:
        return _haversine_km(lat, lon, lat1, lon1)
    t = max(0.0, min(1.0, ((lon - lon1) * dx + (lat - lat1) * dy) / len2))
    return _haversine_km(lat, lon, lat1 + t * dy, lon1 + t * dx)


def apply_area_filter(qs, params):
    """Filter by geographic regions from the ?geo= param (rect, circle, polygon, corridor)."""
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
                if _haversine_km(lat, lon, clat, clon) <= r["radius_m"] / 1000.0:
                    matched = True
                    break
        if not matched:
            for r in polygon_regions:
                if _point_in_polygon(clat, clon, r["coordinates"]):
                    matched = True
                    break
        if not matched:
            for r in corridor_regions:
                w_km = r["width_m"] / 1000.0
                path = r["path"]
                for k in range(len(path) - 1):
                    lng1, lat1 = path[k]
                    lng2, lat2 = path[k + 1]
                    if _dist_to_segment_km(clat, clon, lat1, lng1, lat2, lng2) <= w_km:
                        matched = True
                        break
                if matched:
                    break
        if matched:
            keep_pks.add(pk)

    return qs.filter(pk__in=keep_pks)


# ---------------------------------------------------------------------------
# Filter chain — order matches the original _apply_explicit_filters() sequence.
# Radius and bearing filters depend on the distance_km/bearing_deg annotation
# added in cache_list(), so they are applied there directly.
# The raw WHERE clause is applied separately in _apply_explicit_filters so that
# SQL errors can be surfaced to the user rather than silently ignored.
# ---------------------------------------------------------------------------

FILTER_CHAIN = [
    apply_quick_search,
    apply_type_filter,
    apply_status_filter,
    apply_size_filter,
    apply_found_filter,
    apply_flag_filter,
    apply_elevation_filter,
    apply_tag_filter,
    apply_tag_advanced_filter,
    apply_country_filter,
    apply_missing_filter,
    apply_text_filters,
    apply_range_filters,
    apply_date_filters,
    apply_multi_flag_filter,
    apply_attribute_filter,
    apply_area_filter,
]
