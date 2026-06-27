"""
Find-statistics service for the dashboard's Statistics tab.

Computes GSAK-findstatgen-style aggregates over the user's finds.  A "find"
is any cache with ``found=True`` or ``completed=True`` (ALC parents use the
completed flag).  All aggregation runs in Python over compact value tuples
— the found set is at most a few tens of thousands of rows, so this is
cheap and avoids a pile of DB-specific date SQL.
"""

from __future__ import annotations

import calendar
import datetime as _dt
import math
from collections import Counter

from django.db.models import Q

from ..geo import bearing_deg, haversine_km
from ..models import CacheSize, CacheType, Geocache


def stats_platform_settings() -> tuple[bool, bool]:
    """Return ``(include_oc, include_al)`` from the dashboard preferences.

    Defaults: OC on, Adventure Lab off.
    """
    from preferences.models import UserPreference
    return (
        bool(UserPreference.get("stats_include_oc", True)),
        bool(UserPreference.get("stats_include_al", False)),
    )

# Difficulty / terrain axis: 1.0 .. 5.0 in 0.5 steps.
_DT_AXIS = [1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 4.5, 5.0]
_MONTHS = list(range(1, 13))
_MONTH_LABELS = [calendar.month_abbr[m] for m in _MONTHS]


def _found_q() -> Q:
    return Q(found=True) | Q(completed=True)


def find_queryset(cache_type: str | None = None, *, apply_platform_filter: bool = True):
    """Queryset of the user's finds, optionally narrowed to one cache type.

    By default the queryset honours the dashboard "Include OC.xx caches" and
    "Include Adventure Lab caches" settings so every Statistics-tab figure
    stays consistent.  The "By platform" summary panel is the one caller that
    needs the full set — pass ``apply_platform_filter=False`` there.
    """
    qs = Geocache.objects.filter(_found_q())
    # ALC parents are marked found=True once all stages are found (mirrored by
    # recompute_adventure_completed), but we want stage count only, not parent+stages.
    qs = qs.exclude(adventure__isnull=False, al_detail__isnull=True)
    if apply_platform_filter:
        include_oc, include_al = stats_platform_settings()
        if not include_oc:
            # OC-only finds carry oc_code but neither gc_code nor al_code.
            qs = qs.exclude(Q(gc_code="") & Q(al_code="") & ~Q(oc_code=""))
        if not include_al:
            # Adventure Lab finds carry al_code but neither gc_code nor oc_code.
            qs = qs.exclude(Q(gc_code="") & Q(oc_code="") & ~Q(al_code=""))
    if cache_type:
        qs = qs.filter(cache_type=cache_type)
    return qs


def type_options() -> list[tuple[str, str]]:
    """(value, label) pairs for the Type dropdown — only types actually found,
    prefixed with an "all types" entry (value "").

    Adventure Lab is omitted when excluded from statistics in settings.
    OC caches share types with GC (Traditional, Mystery, ...) so no exclusion there.
    """
    _, include_al = stats_platform_settings()
    found_types = set(
        find_queryset(apply_platform_filter=False)
        .values_list("cache_type", flat=True).distinct()
    )
    opts = [("", "All types")]
    for value, label in CacheType.choices:
        if value not in found_types:
            continue
        if not include_al and value == CacheType.LAB:
            continue
        opts.append((value, label))
    return opts


# ---------------------------------------------------------------------------
# Heatmap colouring — green (low) -> red (high), matching GSAK's scale.
# ---------------------------------------------------------------------------

def _heat(count: int, max_count: int) -> str:
    """Return an inline background colour for a heatmap cell, or '' for 0.

    Uses a log scale so single dominant days don't wash out the rest.
    """
    if not count or max_count <= 0:
        return ""
    t = math.log1p(count) / math.log1p(max_count)
    hue = 120 * (1.0 - t)          # 120 = green, 0 = red
    return f"hsl({hue:.0f}, 70%, 42%)"


# ---------------------------------------------------------------------------
# Summary headline numbers
# ---------------------------------------------------------------------------

def summary_stats() -> dict:
    qs = find_queryset()
    total = qs.count()
    dates = list(
        qs.exclude(found_date__isnull=True).values_list("found_date", flat=True)
    )
    by_day = Counter(dates)
    distinct_days = len(by_day)
    best_day_count = max(by_day.values()) if by_day else 0
    best_day = max(by_day, key=by_day.get) if by_day else None
    this_year = _dt.date.today().year
    finds_this_year = sum(1 for d in dates if d.year == this_year)

    # Platform split — always over the full found set; the "By platform"
    # panel exists precisely to show what the include/exclude settings are
    # currently hiding from the rest of the Statistics tab.
    qs_all = find_queryset(apply_platform_filter=False)
    gc = qs_all.exclude(gc_code="").count()
    oc = qs_all.exclude(oc_code="").filter(gc_code="").count()
    al = qs_all.exclude(al_code="").filter(gc_code="", oc_code="").count()

    include_oc, include_al = stats_platform_settings()
    return {
        "total_finds": total,
        "first_find": min(dates) if dates else None,
        "last_find": max(dates) if dates else None,
        "distinct_days": distinct_days,
        "avg_per_caching_day": round(total / distinct_days, 1) if distinct_days else 0,
        "best_day": best_day,
        "best_day_count": best_day_count,
        "finds_this_year": finds_this_year,
        "platform_gc": gc,
        "platform_oc": oc,
        "platform_al": al,
        "include_gc": True,
        "include_oc": include_oc,
        "include_al": include_al,
    }


# ---------------------------------------------------------------------------
# Simple one-dimension breakdowns (type-independent)
# ---------------------------------------------------------------------------

def _add_shares(rows: list[dict]) -> list[dict]:
    """Annotate each row with ``pct`` (share of total) and ``bar`` (share of the
    max row) for CSS percent-bars.  ``bar`` scales the longest bar to full width
    so the breakdown stays readable; ``pct`` is the true percentage label."""
    total = sum(r["count"] for r in rows) or 1
    mx = max((r["count"] for r in rows), default=0) or 1
    for r in rows:
        r["pct"] = r["count"] / total * 100
        r["bar"] = r["count"] / mx * 100
    return rows


def finds_by_type() -> list[dict]:
    counts = Counter(find_queryset().values_list("cache_type", flat=True))
    labels = dict(CacheType.choices)
    rows = [
        {"value": t, "label": labels.get(t, t), "count": c}
        for t, c in counts.items()
    ]
    rows.sort(key=lambda r: -r["count"])
    return _add_shares(rows)


def finds_by_size() -> list[dict]:
    counts = Counter(find_queryset().values_list("size", flat=True))
    labels = dict(CacheSize.choices)
    rows = [
        {"value": s, "label": labels.get(s, s), "count": c}
        for s, c in counts.items()
    ]
    rows.sort(key=lambda r: -r["count"])
    return _add_shares(rows)


def finds_by_rating(field: str, prefix: str = "") -> list[dict]:
    """field = 'difficulty' or 'terrain' → list of {rating, label, count, pct, bar}.

    *prefix* ('D'/'T') builds the ``label`` used for pie-chart slice names.
    """
    counts = Counter(
        find_queryset().exclude(**{f"{field}__isnull": True})
        .values_list(field, flat=True)
    )
    return _add_shares([
        {"rating": r, "label": f"{prefix} {r}".strip(), "count": counts.get(r, 0)}
        for r in _DT_AXIS
    ])


def finds_by_country_iso() -> dict[str, int]:
    """Find counts keyed by ISO 3166-1 alpha-2 (uppercase) for the world map.

    Caches with no enriched country code are skipped.  The keys join directly to
    the ``iso_a2`` property of the bundled world-country GeoJSON.
    """
    codes = (
        find_queryset().exclude(iso_country_code="")
        .values_list("iso_country_code", flat=True)
    )
    return dict(Counter(c.upper() for c in codes))


def all_by_country_iso() -> dict[str, int]:
    """All caches (not just finds) keyed by ISO alpha-2, so the world map can
    offer a "show unfound" filter for countries with no finds yet."""
    codes = (
        Geocache.objects.exclude(iso_country_code="")
        .values_list("iso_country_code", flat=True)
    )
    return dict(Counter(c.upper() for c in codes))


def finds_by_state(iso2: str) -> dict[str, int]:
    """Find counts keyed by enriched state/region name within one country."""
    rows = (
        find_queryset().filter(iso_country_code=iso2.upper())
        .exclude(state="").values_list("state", flat=True)
    )
    return dict(Counter(rows))


def finds_by_county(iso2: str) -> dict[str, int]:
    """Find counts keyed by enriched county name within one country."""
    rows = (
        find_queryset().filter(iso_country_code=iso2.upper())
        .exclude(county="").values_list("county", flat=True)
    )
    return dict(Counter(rows))


def finds_by_district(iso2: str, state: str) -> dict[str, int]:
    """Find counts keyed by county (the district/Bezirk/ward) within one state.

    Used for the sub-county district map of single-county states (Berlin
    Bezirke, DC wards, …) whose finds are stored under the district name.
    """
    rows = (
        find_queryset().filter(iso_country_code=iso2.upper(), state=state)
        .exclude(county="").values_list("county", flat=True)
    )
    return dict(Counter(rows))


def all_by_district(iso2: str, state: str) -> dict[str, int]:
    """All caches keyed by county/district within one state (see
    :func:`finds_by_district`) — for find-less district trip planning."""
    rows = (
        Geocache.objects.filter(iso_country_code=iso2.upper(), state=state)
        .exclude(county="").values_list("county", flat=True)
    )
    return dict(Counter(rows))


def finds_by_state_county(iso2: str) -> dict[tuple[str, str], int]:
    """Find counts keyed by ``(state, county)`` within one country.

    The composite key disambiguates name collisions across states — both
    "San Juan, Utah" and "San Juan, Puerto Rico" exist as US counties, and
    a bare-name join would merge them.
    """
    rows = (
        find_queryset().filter(iso_country_code=iso2.upper())
        .exclude(county="").values_list("state", "county")
    )
    return dict(Counter(rows))


def all_by_state(iso2: str) -> dict[str, int]:
    """All caches (not just finds) keyed by state, within one country.

    Used to populate a region's filter keys even where there are no finds yet,
    so the dashboard map can offer a "show unfound" filter for trip planning.
    """
    rows = (
        Geocache.objects.filter(iso_country_code=iso2.upper())
        .exclude(state="").values_list("state", flat=True)
    )
    return dict(Counter(rows))


def all_by_state_county(iso2: str) -> dict[tuple[str, str], int]:
    """All caches keyed by ``(state, county)`` within one country (see
    :func:`all_by_state`)."""
    rows = (
        Geocache.objects.filter(iso_country_code=iso2.upper())
        .exclude(county="").values_list("state", "county")
    )
    return dict(Counter(rows))


def finds_in_state_county_keys(iso2: str, keys) -> list[dict]:
    """Found caches whose location matches one of *keys*, within one country.

    Each key is either a bare ``state`` string (region tier) or a
    ``(state, county)`` tuple (county tier).  Used to list the caches behind a
    choropleth tier's "unmapped" note so the user can open them or jump to a
    filtered list view and fix the bad state/county value.
    """
    if not keys:
        return []
    q = Q()
    for key in keys:
        if isinstance(key, tuple):
            q |= Q(state=key[0], county=key[1])
        else:
            q |= Q(state=key)
    rows = (
        find_queryset().filter(iso_country_code=iso2.upper()).filter(q)
        .values("gc_code", "al_code", "oc_code", "name", "state", "county")
        .order_by("state", "county", "gc_code")
    )
    return [
        {
            # Canonical code (mirrors Geocache.display_code) — drives the detail
            # link and the list-view filter, so OC/lab finds work too.
            "code": r["gc_code"] or r["al_code"] or r["oc_code"],
            "name": r["name"],
            "state": r["state"],
            "county": r["county"],
        }
        for r in rows
    ]


def finds_by_year() -> list[dict]:
    years = Counter(
        d.year for d in find_queryset()
        .exclude(found_date__isnull=True)
        .values_list("found_date", flat=True)
    )
    if not years:
        return []
    lo, hi = min(years), max(years)
    return [{"year": y, "count": years.get(y, 0)} for y in range(lo, hi + 1)]


def finds_by_month() -> list[dict]:
    """Finds by calendar month (Jan–Dec), aggregated across all years — the
    seasonal distribution of finding activity."""
    counts = Counter(
        d.month for d in find_queryset()
        .exclude(found_date__isnull=True)
        .values_list("found_date", flat=True)
    )
    return [
        {"month": m, "label": _MONTH_LABELS[m - 1], "count": counts.get(m, 0)}
        for m in _MONTHS
    ]


def finds_cumulative_by_month(queryset=None) -> dict:
    """Per-month find counts + running cumulative total, for a time-series chart.

    Returns a continuous month axis (no gaps) from the first find's month to the
    current month, so the chart reads as a real timeline.  ``months`` are
    ``YYYY-MM`` labels; ``monthly`` is finds in that month; ``cumulative`` is the
    running total.  Empty dict shape when there are no dated finds.

    *queryset* overrides the default find set (used by the Adventure Lab tab,
    which counts found lab stages regardless of the platform setting).
    """
    qs = queryset if queryset is not None else find_queryset()
    dates = (
        qs
        .exclude(found_date__isnull=True)
        .values_list("found_date", flat=True)
    )
    by_ym = Counter((d.year, d.month) for d in dates)
    if not by_ym:
        return {"months": [], "monthly": [], "cumulative": []}

    lo_y, lo_m = min(by_ym)
    today = _dt.date.today()
    hi_y, hi_m = today.year, today.month

    months, monthly, cumulative = [], [], []
    running = 0
    y, m = lo_y, lo_m
    while (y, m) <= (hi_y, hi_m):
        c = by_ym.get((y, m), 0)
        running += c
        months.append(f"{y:04d}-{m:02d}")
        monthly.append(c)
        cumulative.append(running)
        m += 1
        if m > 12:
            y, m = y + 1, 1
    return {"months": months, "monthly": monthly, "cumulative": cumulative}


def _resolve_ref_point(ref_point_id: int | None = None):
    """Reference point for bearing stats.

    An explicit *ref_point_id* wins (the per-chart selector); otherwise fall
    back to the default point, else any.  None when none exist / the id is bad.
    """
    from preferences.models import ReferencePoint
    if ref_point_id:
        ref = ReferencePoint.objects.filter(pk=ref_point_id).first()
        if ref is not None:
            return ref
    return (
        ReferencePoint.objects.filter(is_default=True).first()
        or ReferencePoint.objects.first()
    )


def finds_by_bearing(ref_point_id: int | None = None, sectors: int = 12) -> dict | None:
    """Wind-rose data: finds binned into compass sectors around a reference
    point, with per-sector find count and average distance (km).

    *ref_point_id* selects the point (per-chart selector); without it the
    default point is used.  Bins are centred on the labels (0° = N, 30°, 60°,
    …): a bearing of 350° falls in the 0° (North) bin.  Returns ``None`` when no
    reference point is configured; a zero-filled dict when the point simply has
    no located finds.
    """
    from geocaches.geo import distance_cache
    from geocaches.models import DistanceCache

    ref = _resolve_ref_point(ref_point_id)
    if ref is None:
        return None
    distance_cache.ensure_cached(ref)

    rows = DistanceCache.objects.filter(
        ref_point=ref, geocache__in=find_queryset()
    ).values_list("bearing_deg", "distance_km")

    width = 360 / sectors
    counts = [0] * sectors
    dist_sums = [0.0] * sectors
    for bearing, dist in rows.iterator():
        idx = int(((bearing + width / 2) % 360) // width) % sectors
        counts[idx] += 1
        dist_sums[idx] += dist

    labels = [f"{int(round(i * width))}°" for i in range(sectors)]
    avg_distance = [
        round(dist_sums[i] / counts[i], 1) if counts[i] else 0
        for i in range(sectors)
    ]
    return {
        "ref_id": ref.pk,
        "ref_name": ref.name,
        "labels": labels,
        "counts": counts,
        "avg_distance": avg_distance,
        "total": sum(counts),
    }


def finds_360(ref_point_id: int | None = None, max_km: float = 100.0,
              top_n: int = 10, use_corrected: bool = False) -> dict | None:
    """360-sectors-from-a-location data (project-gc "360home" style).

    Splits the compass into 360 one-degree sectors around the chosen reference
    point and, for finds within *max_km*, returns per-sector counts + the
    nearest *top_n* find codes, plus the raw points for a density map.

    Distance/bearing are computed here (not from :class:`DistanceCache`) so the
    *use_corrected* flag can choose original vs corrected coordinates — the
    cache always uses corrected, which is the opposite of this view's default.
    The goal-dependent statistics are derived client-side so the goal slider
    needs no round-trip.  ``None`` when no reference point is configured.
    """
    ref = _resolve_ref_point(ref_point_id)
    if ref is None:
        return None
    rlat, rlon = ref.latitude, ref.longitude

    rows = (
        find_queryset()
        .exclude(latitude__isnull=True).exclude(longitude__isnull=True)
        .values_list("pk", "gc_code", "al_code", "oc_code", "latitude", "longitude")
    )
    corr = {}
    if use_corrected:
        from geocaches.models import CorrectedCoordinates
        corr = {
            gid: (clat, clon)
            for gid, clat, clon in CorrectedCoordinates.objects.values_list(
                "geocache_id", "latitude", "longitude"
            )
        }

    sector_codes: list[list] = [[] for _ in range(360)]  # (dist, code) per sector
    counts = [0] * 360
    points = []
    for pk, gc, al, oc, lat, lon in rows.iterator():
        if use_corrected and pk in corr:
            lat, lon = corr[pk]
        dist = haversine_km(rlat, rlon, lat, lon)
        if dist > max_km:
            continue
        idx = int(bearing_deg(rlat, rlon, lat, lon)) % 360
        code = gc or al or oc
        counts[idx] += 1
        sector_codes[idx].append((dist, code))
        points.append({"lat": lat, "lon": lon, "code": code})

    sectors = [
        {
            "i": i,
            "count": counts[i],
            "codes": [c for _, c in sorted(sector_codes[i])[:top_n]],
        }
        for i in range(360)
    ]
    return {
        "ref_id": ref.pk,
        "ref_name": ref.name,
        "ref_lat": rlat,
        "ref_lon": rlon,
        "max_km": max_km,
        "use_corrected": use_corrected,
        "sectors": sectors,
        "points": points,
        "total": len(points),
    }


def build_360_missing_where_sql(ref_id: int, max_km: float, sectors,
                                use_corrected: bool = False) -> str:
    """WHERE clause selecting *unfound* caches within *max_km* of the reference
    point whose bearing falls in one of *sectors* (the under-goal angles).

    The geo test runs against the :class:`DistanceCache` subquery (compact +
    URL-safe vs enumerating every PK).  That cache is built from corrected
    coordinates where present, so:

    * ``use_corrected=True`` — the subquery is exactly right.
    * ``use_corrected=False`` (original coords) — caches that *carry* corrected
      coords are sitting at the wrong spot in the cache, so we drop them from
      the subquery (one compact ``NOT IN`` sub-select) and add back, by PK, only
      the small set whose **original** coords actually qualify.  Solved-but-
      unfound mysteries are exactly this set, so the toggle is honoured.

    Returns ``"1 = 0"`` when nothing qualifies.
    """
    if not sectors:
        return "1 = 0"
    from geocaches.geo import distance_cache
    from geocaches.models import CorrectedCoordinates, DistanceCache

    ref = _resolve_ref_point(ref_id)
    if ref is None:
        return "1 = 0"
    distance_cache.ensure_cached(ref)
    table = DistanceCache._meta.db_table
    sset = {int(s) for s in sectors}
    csv = ", ".join(str(s) for s in sorted(sset))
    geo = (
        f"id IN (SELECT geocache_id FROM {table} "
        f"WHERE ref_point_id = {int(ref.pk)} AND distance_km <= {float(max_km)} "
        f"AND CAST(bearing_deg AS INTEGER) IN ({csv}))"
    )

    if not use_corrected:
        corr_table = CorrectedCoordinates._meta.db_table
        # Re-place corrected-coord caches by their ORIGINAL coordinates.
        rlat, rlon = ref.latitude, ref.longitude
        orig_hits = [
            gid
            for gid, olat, olon in (
                CorrectedCoordinates.objects
                .filter(geocache__found=False, geocache__completed=False)
                .exclude(geocache__latitude__isnull=True)
                .exclude(geocache__longitude__isnull=True)
                .values_list("geocache_id", "geocache__latitude", "geocache__longitude")
                .iterator()
            )
            if haversine_km(rlat, rlon, olat, olon) <= max_km
            and int(bearing_deg(rlat, rlon, olat, olon)) % 360 in sset
        ]
        geo = f"({geo} AND id NOT IN (SELECT geocache_id FROM {corr_table}))"
        if orig_hits:
            incl = ", ".join(str(p) for p in orig_hits)
            geo = f"({geo} OR id IN ({incl}))"

    return f"{geo} AND found = 0 AND completed = 0"


# ---------------------------------------------------------------------------
# Grid search — nudge the centre on the decimal-minutes lattice to minimise the
# number of under-goal sectors (project-gc "optimize home coordinates").
# ---------------------------------------------------------------------------

_EARTH_KM = 6371.0


def _dm_components(value: float) -> tuple[int, int, float]:
    """(sign, integer degrees, decimal minutes) of a decimal-degree value."""
    sign = 1 if value >= 0 else -1
    v = abs(value)
    deg = int(v)
    return sign, deg, (v - deg) * 60.0


def _offset_dm(value: float, steps: int) -> float:
    """Snap *value* to the thousandth-of-a-minute lattice and shift by *steps*
    thousandths — the axis used to build the grid (e.g. N48 23.123 ± steps)."""
    sign, deg, minutes = _dm_components(value)
    thou = round(minutes * 1000) + steps
    return sign * (deg + (thou / 1000.0) / 60.0)


def _format_dm_compact(lat: float, lon: float) -> str:
    """Compact degrees-decimal-minutes, e.g. ``N48 23.123 E009 12.456``."""
    def one(v, pos, neg, degw):
        _, deg, minutes = _dm_components(v)
        return f"{pos if v >= 0 else neg}{deg:0{degw}d} {minutes:06.3f}"
    return f"{one(lat, 'N', 'S', 2)} {one(lon, 'E', 'W', 3)}"


def _sectors_missing_at(finds_rad: list, glat: float, glon: float,
                        max_km: float, goal: int) -> int:
    """Count sectors with < *goal* finds for a candidate centre.

    *finds_rad* is a precomputed list of ``(phi, lam, sin_phi, cos_phi)`` in
    radians — building it once and reusing it across the grid keeps the hot
    loop free of repeated ``math.radians`` work.
    """
    cphi = math.radians(glat)
    clam = math.radians(glon)
    s_cphi = math.sin(cphi)
    c_cphi = math.cos(cphi)
    counts = [0] * 360
    for phi, lam, sphi, cphi_f in finds_rad:
        dlam = lam - clam
        dphi = phi - cphi
        a = math.sin(dphi / 2) ** 2 + c_cphi * cphi_f * math.sin(dlam / 2) ** 2
        if 2 * _EARTH_KM * math.asin(math.sqrt(min(a, 1.0))) > max_km:
            continue
        x = math.sin(dlam) * cphi_f
        y = c_cphi * sphi - s_cphi * cphi_f * math.cos(dlam)
        counts[int((math.degrees(math.atan2(x, y)) + 360.0) % 360.0)] += 1
    return sum(1 for c in counts if c < goal)


def grid_search_360(ref_point_id: int | None = None, max_km: float = 100.0,
                    goal: int = 1, grid_width: int = 9,
                    use_corrected: bool = False) -> dict | None:
    """Evaluate a ``grid_width`` × ``grid_width`` lattice of candidate centres
    (±half the width in thousandths-of-a-minute on each axis) around the
    location, reporting the under-goal sector count for each.

    Returns the candidates sorted best-first (fewest missing sectors, then
    nearest).  ``None`` when no reference point is configured.
    """
    ref = _resolve_ref_point(ref_point_id)
    if ref is None:
        return None
    grid_width = max(3, min(21, int(grid_width)))
    if grid_width % 2 == 0:
        grid_width += 1
    clat, clon = ref.latitude, ref.longitude

    rows = (
        find_queryset()
        .exclude(latitude__isnull=True).exclude(longitude__isnull=True)
        .values_list("pk", "latitude", "longitude")
    )
    corr = {}
    if use_corrected:
        from geocaches.models import CorrectedCoordinates
        corr = {
            gid: (a, b)
            for gid, a, b in CorrectedCoordinates.objects.values_list(
                "geocache_id", "latitude", "longitude"
            )
        }
    # Pre-filter to finds that could fall within max_km of any candidate centre
    # (the grid spans only tens of metres), and precompute their radians.
    margin = max_km + 0.3
    finds_rad = []
    for pk, lat, lon in rows.iterator():
        if use_corrected and pk in corr:
            lat, lon = corr[pk]
        if haversine_km(clat, clon, lat, lon) <= margin:
            phi, lam = math.radians(lat), math.radians(lon)
            finds_rad.append((phi, lam, math.sin(phi), math.cos(phi)))

    h = (grid_width - 1) // 2
    results = []
    for di in range(-h, h + 1):
        glat = _offset_dm(clat, di)
        for dj in range(-h, h + 1):
            glon = _offset_dm(clon, dj)
            results.append({
                "lat": glat,
                "lon": glon,
                "coord": _format_dm_compact(glat, glon),
                "missing": _sectors_missing_at(finds_rad, glat, glon, max_km, goal),
                "dist_m": round(haversine_km(clat, clon, glat, glon) * 1000, 1),
            })
    results.sort(key=lambda r: (r["missing"], r["dist_m"]))
    return {
        "center": _format_dm_compact(clat, clon),
        "grid_width": grid_width,
        "results": results,
    }


# ---------------------------------------------------------------------------
# Type-filterable tables (the four GSAK staples)
# ---------------------------------------------------------------------------

def dt_matrix(cache_type: str | None = None) -> dict:
    """9x9 Difficulty(row) x Terrain(col) matrix of find counts + totals."""
    pairs = Counter(
        find_queryset(cache_type)
        .exclude(difficulty__isnull=True).exclude(terrain__isnull=True)
        .values_list("difficulty", "terrain")
    )
    max_count = max(pairs.values()) if pairs else 0
    rows = []
    col_totals = [0] * len(_DT_AXIS)
    for d in _DT_AXIS:
        cells = []
        row_total = 0
        for ci, t in enumerate(_DT_AXIS):
            c = pairs.get((d, t), 0)
            row_total += c
            col_totals[ci] += c
            cells.append({"count": c, "color": _heat(c, max_count)})
        rows.append({"rating": d, "cells": cells, "total": row_total})
    return {
        "axis": _DT_AXIS,
        "rows": rows,
        "col_totals": col_totals,
        "grand_total": sum(col_totals),
    }


def _calendar(field: str, cache_type: str | None = None, *, queryset=None) -> dict:
    """Month(row) x day-of-month(col) calendar of counts for a date field.

    *queryset* overrides the default find set (used by the Adventure Lab tab).
    """
    qs = queryset if queryset is not None else find_queryset(cache_type)
    dates = (
        qs
        .exclude(**{f"{field}__isnull": True})
        .values_list(field, flat=True)
    )
    by_md = Counter((d.month, d.day) for d in dates)
    max_count = max(by_md.values()) if by_md else 0
    rows = []
    for m in _MONTHS:
        # Leap year (2000) so 29 Feb counts as a valid day.
        days_in_month = calendar.monthrange(2000, m)[1]
        cells = []
        row_total = 0
        for day in range(1, 32):
            c = by_md.get((m, day), 0)
            row_total += c
            cells.append({
                "count": c,
                "color": _heat(c, max_count),
                "valid": day <= days_in_month,
            })
        rows.append({"month": _MONTH_LABELS[m - 1], "cells": cells, "total": row_total})
    return {"days": list(range(1, 32)), "rows": rows, "grand_total": sum(by_md.values())}


def finds_by_found_date(cache_type: str | None = None) -> dict:
    return _calendar("found_date", cache_type)


def finds_by_placed_date(cache_type: str | None = None) -> dict:
    return _calendar("hidden_date", cache_type)


def finds_by_placed_month(cache_type: str | None = None) -> dict:
    """Year(row) x month(col) grid of finds by the cache's placed/hidden date."""
    dates = (
        find_queryset(cache_type)
        .exclude(hidden_date__isnull=True)
        .values_list("hidden_date", flat=True)
    )
    by_ym = Counter((d.year, d.month) for d in dates)
    if not by_ym:
        return {"months": _MONTH_LABELS, "rows": [], "grand_total": 0}
    max_count = max(by_ym.values())
    lo = min(y for y, _ in by_ym)
    hi = max(y for y, _ in by_ym)
    rows = []
    for y in range(lo, hi + 1):
        cells = []
        row_total = 0
        for m in _MONTHS:
            c = by_ym.get((y, m), 0)
            row_total += c
            cells.append({"count": c, "color": _heat(c, max_count)})
        rows.append({"year": y, "cells": cells, "total": row_total})
    return {"months": _MONTH_LABELS, "rows": rows, "grand_total": sum(by_ym.values())}


# ---------------------------------------------------------------------------
# "Find missing in DB" — build a raw WHERE clause matching unfound caches
# that fill the gaps of a stat table (cells with finds < minimum).
# ---------------------------------------------------------------------------

def _missing_dt_cells(cache_type, minimum):
    m = dt_matrix(cache_type)
    out = []
    for row in m["rows"]:
        for ci, cell in enumerate(row["cells"]):
            if cell["count"] < minimum:
                out.append((row["rating"], _DT_AXIS[ci]))
    return out


def _missing_pm_cells(cache_type, minimum):
    pm = finds_by_placed_month(cache_type)
    out = []
    for row in pm["rows"]:
        for mi, cell in enumerate(row["cells"]):
            if cell["count"] < minimum:
                out.append((row["year"], mi + 1))
    return out


def _missing_pd_cells(cache_type, minimum):
    cal = finds_by_placed_date(cache_type)
    out = []
    for mi, row in enumerate(cal["rows"]):
        for di, cell in enumerate(row["cells"]):
            if cell["valid"] and cell["count"] < minimum:
                out.append((mi + 1, di + 1))
    return out


def _dt_sql(cells) -> str:
    from collections import defaultdict
    by_d: dict[float, list[float]] = defaultdict(list)
    for d, t in cells:
        by_d[d].append(t)
    parts = []
    for d in sorted(by_d):
        terr = ", ".join(f"{t}" for t in sorted(by_d[d]))
        parts.append(f"(difficulty = {d} AND terrain IN ({terr}))")
    return " OR ".join(parts)


# NOTE: the ``%`` in strftime format strings MUST be doubled to ``%%``.
# These clauses run through RawSQL in apply_where_clause, and Django applies
# %-style parameter substitution to the combined query (the list view always
# carries params via its distance annotations) — an un-doubled ``%Y`` is read
# as a format placeholder and raises "not enough arguments for format string",
# which silently drops the whole clause.
def _pm_sql(cells) -> str:
    vals = ", ".join(f"'{y:04d}-{m:02d}'" for y, m in cells)
    return f"strftime('%%Y-%%m', hidden_date) IN ({vals})"


def _pd_sql(cells) -> str:
    vals = ", ".join(f"'{m:02d}-{d:02d}'" for m, d in cells)
    return f"strftime('%%m-%%d', hidden_date) IN ({vals})"


def build_missing_where_sql(which: str, cache_type: str | None = None,
                            minimum: int = 1) -> str:
    """Build a WHERE clause selecting *unfound* caches that fill stat gaps.

    ``which``:
      * "all" — union of D/T, placed-month and placed-date gaps (count 0).
      * "dt" / "placed_month" / "placed_date" — that single dimension's
        cells with finds < ``minimum``.

    ``cache_type`` narrows both the gap computation and the result set.
    Returns "" when nothing is missing.
    """
    subclauses = []
    if which in ("all", "dt"):
        cells = _missing_dt_cells(cache_type, 1 if which == "all" else minimum)
        if cells:
            subclauses.append("(" + _dt_sql(cells) + ")")
    if which in ("all", "placed_month"):
        cells = _missing_pm_cells(cache_type, 1 if which == "all" else minimum)
        if cells:
            subclauses.append("(" + _pm_sql(cells) + ")")
    if which in ("all", "placed_date"):
        cells = _missing_pd_cells(cache_type, 1 if which == "all" else minimum)
        if cells:
            subclauses.append("(" + _pd_sql(cells) + ")")
    if not subclauses:
        return ""

    parts = ["(" + " OR ".join(subclauses) + ")", "found = 0", "completed = 0"]
    # Only honour a known cache type — guards against SQL injection via the
    # URL param (value is interpolated, so validate against the choices).
    if cache_type and cache_type in dict(CacheType.choices):
        parts.append(f"cache_type = '{cache_type}'")
    return " AND ".join(parts)


def type_filterable_tables(cache_type: str | None = None) -> dict:
    """Bundle the four type-filterable tables for the given type filter."""
    return {
        "dt_matrix": dt_matrix(cache_type),
        "found_date": finds_by_found_date(cache_type),
        "placed_month": finds_by_placed_month(cache_type),
        "placed_date": finds_by_placed_date(cache_type),
        "selected_type": cache_type or "",
    }


# ---------------------------------------------------------------------------
# Adventure Lab statistics — its own dashboard tab.
#
# A "find" here is a completed lab *stage* (a Geocache backed by ALStageDetail
# with found=True), matching how stages count everywhere else; the parent
# adventure row is never counted.  Unlike the rest of the Statistics tab these
# helpers IGNORE the "Include Adventure Lab caches" preference — the AL tab
# always shows lab finds regardless of that toggle.
# ---------------------------------------------------------------------------

def alc_find_queryset():
    """Found Adventure Lab stages, independent of the platform-inclusion setting."""
    return Geocache.objects.filter(found=True, al_detail__isnull=False)


def alc_summary() -> dict:
    """Headline numbers for the Adventure Lab tab."""
    qs = alc_find_queryset()
    total = qs.count()
    dates = list(qs.exclude(found_date__isnull=True).values_list("found_date", flat=True))
    this_year = _dt.date.today().year
    adventures = (
        qs.exclude(adventure__isnull=True)
        .values_list("adventure_id", flat=True).distinct().count()
    )
    return {
        "total_finds": total,
        "adventures": adventures,
        "distinct_days": len(set(dates)),
        "first_find": min(dates) if dates else None,
        "last_find": max(dates) if dates else None,
        "finds_this_year": sum(1 for d in dates if d.year == this_year),
    }


def alc_finds_by_country() -> list[dict]:
    """Found lab stages grouped by country, with per-country adventure tallies.

    Each row carries ``count`` (found stages), ``completed`` (adventures whose
    every stage is found) and ``incomplete`` (adventures with at least one — but
    not all — stages found).  Country uses the stage's own enriched ISO code,
    falling back to the parent adventure's country when a stage (or the parent)
    hasn't been location-enriched yet.
    """
    from django.db.models import Count

    from geocaches.geo.countries import iso_to_name

    # Parent rows carry the enriched location for the whole adventure.
    parent_iso = {
        adv_id: (iso or "").upper()
        for adv_id, iso in Geocache.objects.filter(
            adventure__isnull=False, al_detail__isnull=True
        ).values_list("adventure_id", "iso_country_code")
    }

    # Found-stage counts per country + a per-adventure stage-country fallback.
    finds: Counter = Counter()
    stage_iso_fallback: dict[int, str] = {}
    for adv_id, stage_iso in alc_find_queryset().values_list(
        "adventure_id", "iso_country_code"
    ):
        stage_iso = (stage_iso or "").upper()
        code = stage_iso or parent_iso.get(adv_id, "")
        if code:
            finds[code] += 1
        if adv_id is not None and stage_iso and not stage_iso_fallback.get(adv_id):
            stage_iso_fallback[adv_id] = stage_iso

    # Completed vs partially-found adventures, per country.
    completed: Counter = Counter()
    incomplete: Counter = Counter()
    stage_stats = (
        Geocache.objects.filter(al_detail__isnull=False, adventure__isnull=False)
        .values("adventure_id")
        .annotate(total=Count("id"), found_n=Count("id", filter=Q(found=True)))
    )
    for row in stage_stats:
        if not row["found_n"]:
            continue  # no finds → outside this finds-by-country view
        adv_id = row["adventure_id"]
        iso = parent_iso.get(adv_id) or stage_iso_fallback.get(adv_id, "")
        if not iso:
            continue
        if row["found_n"] >= row["total"]:
            completed[iso] += 1
        else:
            incomplete[iso] += 1

    rows = [
        {
            "iso": iso,
            "name": iso_to_name(iso),
            "count": finds.get(iso, 0),
            "completed": completed.get(iso, 0),
            "incomplete": incomplete.get(iso, 0),
        }
        for iso in set(finds) | set(completed) | set(incomplete)
    ]
    rows.sort(key=lambda r: -r["count"])
    return _add_shares(rows)


def alc_finds_by_found_date() -> dict:
    """Month x day-of-month calendar of lab-stage finds (for the AL tab)."""
    return _calendar("found_date", queryset=alc_find_queryset())


def alc_cumulative_by_month() -> dict:
    """Cumulative lab-stage finds over time (for the AL tab)."""
    return finds_cumulative_by_month(alc_find_queryset())


def _alc_adventures() -> list[dict]:
    """One row per adventure (parent): ``{parent_id, themes, status}``.

    ``status`` is ``completed`` (every stage found), ``incomplete`` (some but
    not all), or ``not_started`` (none).  Covers every imported adventure so the
    theme breakdown can report all three states per theme.
    """
    from django.db.models import Count

    stage_stats = {
        r["adventure_id"]: (r["total"], r["found_n"])
        for r in (
            Geocache.objects.filter(al_detail__isnull=False, adventure__isnull=False)
            .values("adventure_id")
            .annotate(total=Count("id"), found_n=Count("id", filter=Q(found=True)))
        )
    }
    rows = []
    for gid, adv_id, themes in Geocache.objects.filter(
        adventure__isnull=False, al_detail__isnull=True
    ).values_list("id", "adventure_id", "adventure__themes"):
        total, found_n = stage_stats.get(adv_id, (0, 0))
        if found_n == 0:
            status = "not_started"
        elif total > 0 and found_n >= total:
            status = "completed"
        else:
            status = "incomplete"
        rows.append({
            "parent_id": gid,
            "themes": [t for t in (themes or []) if t],
            "status": status,
        })
    return rows


def alc_theme_breakdown() -> list[dict]:
    """Adventures grouped by theme with per-state adventure counts + a stage count.

    Each row: ``{value, label, icon, completed, incomplete, not_started, stages}``.
    ``completed/incomplete/not_started`` count adventures (parents) — one with
    several themes is counted under each — while ``stages`` counts found lab
    stages of that theme (the per-find view).  Sorted by adventure total desc,
    with a trailing "No theme" bucket; tokens prettified via
    ``al_themes.theme_display``.
    """
    from django.utils.translation import gettext as _

    from ..al_themes import theme_display

    by_status = {"completed": Counter(), "incomplete": Counter(), "not_started": Counter()}
    no_theme = {"completed": 0, "incomplete": 0, "not_started": 0}
    for adv in _alc_adventures():
        counter = by_status[adv["status"]]
        if adv["themes"]:
            for t in adv["themes"]:
                counter[t] += 1
        else:
            no_theme[adv["status"]] += 1

    stages: Counter = Counter()
    no_theme_stages = 0
    for themes in alc_find_queryset().values_list("adventure__themes", flat=True):
        if themes:
            for t in themes:
                if t:
                    stages[t] += 1
        else:
            no_theme_stages += 1

    tokens = set(stages)
    for c in by_status.values():
        tokens |= set(c)
    rows = []
    for token in tokens:
        label, icon = theme_display(token)
        rows.append({
            "value": token, "label": label, "icon": icon,
            "completed": by_status["completed"].get(token, 0),
            "incomplete": by_status["incomplete"].get(token, 0),
            "not_started": by_status["not_started"].get(token, 0),
            "stages": stages.get(token, 0),
        })
    if any(no_theme.values()) or no_theme_stages:
        rows.append({
            "value": "", "label": _("No theme"), "icon": "",
            "completed": no_theme["completed"], "incomplete": no_theme["incomplete"],
            "not_started": no_theme["not_started"], "stages": no_theme_stages,
        })
    rows.sort(key=lambda r: -(r["completed"] + r["incomplete"] + r["not_started"]))
    return rows


def alc_theme_parent_ids(theme_token: str, status: str) -> list[int]:
    """Parent geocache ids of adventures carrying *theme_token* (empty token =
    the "no theme" bucket) in *status* (completed | incomplete | not_started)."""
    ids = []
    for adv in _alc_adventures():
        if adv["status"] != status:
            continue
        if theme_token:
            if theme_token in adv["themes"]:
                ids.append(adv["parent_id"])
        elif not adv["themes"]:
            ids.append(adv["parent_id"])
    return ids


def alc_theme_stage_ids(theme_token: str) -> list[int]:
    """Found lab-stage geocache ids carrying *theme_token* (empty = no-theme),
    for the "By stages" column's filter link."""
    ids = []
    for sid, themes in alc_find_queryset().values_list("id", "adventure__themes"):
        tokens = [t for t in (themes or []) if t]
        if theme_token:
            if theme_token in tokens:
                ids.append(sid)
        elif not tokens:
            ids.append(sid)
    return ids
