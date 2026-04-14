"""
Reusable query helpers for geocache filtering.
Used by cache_list (views.py) and map_markers (views_map.py).
"""

from django.db.models import Q, QuerySet

from .filters import FILTER_CHAIN


# ---------------------------------------------------------------------------
# Bearing ranges (moved from views.py)
# ---------------------------------------------------------------------------

BEARING_RANGES = {
    "N":  [(338, 360), (0, 23)],
    "NE": [(24, 67)],
    "E":  [(68, 112)],
    "SE": [(113, 157)],
    "S":  [(158, 203)],
    "SW": [(204, 247)],
    "W":  [(248, 292)],
    "NW": [(293, 337)],
}


# ---------------------------------------------------------------------------
# Core query helpers
# ---------------------------------------------------------------------------


def mine_q() -> Q:
    """Q object matching caches owned by any configured UserAccount.

    Prefers stable numeric owner_gc_id; falls back to username string match.
    Returns an always-false Q (pk__in=[]) when no accounts are configured.
    """
    from accounts.models import UserAccount
    accounts = list(UserAccount.objects.all())
    if not accounts:
        return Q(pk__in=[])
    q = Q()
    gc_ids = [int(a.user_id) for a in accounts if a.platform == "gc" and a.user_id.isdigit()]
    if gc_ids:
        q |= Q(owner_gc_id__in=gc_ids)
    # Username fallback for accounts without a user_id, and for OC where owner_gc_id is null
    usernames = [a.username for a in accounts if a.username]
    if usernames:
        q |= Q(owner__in=usernames)
    return q


def apply_scope(qs: QuerySet) -> QuerySet:
    """Apply the persistent 'Now Forging' scope (stored in UserPreference) to a queryset."""
    from preferences.models import UserPreference

    # --- found / my-caches / unfound filter ---
    scope_found     = UserPreference.get("scope_found",     True)
    scope_my_caches = UserPreference.get("scope_my_caches", True)
    scope_unfound   = UserPreference.get("scope_unfound",   True)
    if not (scope_found and scope_my_caches and scope_unfound):
        mq      = mine_q()
        clauses = []
        if scope_found:
            # AL parents are never found=True; include them via completed=True
            clauses.append(Q(found=True) | Q(completed=True))
        if scope_my_caches:
            clauses.append(mq)
        if scope_unfound:
            # Exclude AL adventures whose completed flag is set
            clauses.append(Q(found=False) & Q(completed=False) & ~mq)
        if not clauses:
            return qs.none()
        combined = clauses[0]
        for c in clauses[1:]:
            combined |= c
        qs = qs.filter(combined)

    # --- platform filter ---
    plat_gc    = UserPreference.get("scope_platform_gc",    True)
    plat_lc    = UserPreference.get("scope_platform_lc",    True)
    plat_oc    = UserPreference.get("scope_platform_oc",    True)
    plat_other = UserPreference.get("scope_platform_other", True)
    if not (plat_gc and plat_lc and plat_oc and plat_other):
        plat_q = Q()
        if plat_gc:
            plat_q |= Q(gc_code__istartswith="GC")
        if plat_lc:
            plat_q |= Q(al_code__gt="")
        if plat_oc:
            plat_q |= Q(oc_code__gt="")
        if plat_other:
            plat_q |= ~Q(gc_code__istartswith="GC") & Q(al_code="") & Q(oc_code="")
        if plat_q:
            qs = qs.filter(plat_q)
        else:
            qs = qs.none()
    return qs


def apply_filters(qs: QuerySet, params: dict) -> QuerySet:
    """Apply all GET-param filters via FILTER_CHAIN. No where-clause."""
    for apply_fn in FILTER_CHAIN:
        qs = apply_fn(qs, params)
    return qs


def _qualify_where_sql(sql: str, table: str) -> str:
    """Prefix bare column names with the Geocache table to avoid ambiguity.

    Only qualifies names that appear as bare identifiers (not already dotted)
    and that match actual Geocache column names.
    """
    import re
    from .models import Geocache
    columns = {f.column for f in Geocache._meta.get_fields() if hasattr(f, "column")}
    # Match a bare identifier that is NOT preceded by a dot or table prefix
    # and IS a known Geocache column.
    def _replace(m):
        word = m.group(0)
        if word in columns:
            # Check if preceded by a dot (already qualified)
            start = m.start()
            if start > 0 and sql[start - 1] == ".":
                return word
            return f"{table}.{word}"
        return word
    # Split on strings, parens, operators to find bare identifiers
    return re.sub(r'\b([a-z_][a-z0-9_]*)\b', _replace, sql, flags=re.IGNORECASE)


def apply_where_clause(qs: QuerySet, params: dict) -> tuple:
    """Apply raw WHERE clause. Returns (qs, where_sql, where_error)."""
    from django.db.models import BooleanField
    from django.db.models.expressions import RawSQL
    from .models import Geocache, SavedWhereClause

    table = Geocache._meta.db_table

    where_name = params.get("where_name", "").strip()
    where_sql = params.get("where_sql", "").strip()
    where_error = ""
    if where_name:
        try:
            where_sql = SavedWhereClause.objects.get(name=where_name).sql
        except SavedWhereClause.DoesNotExist:
            pass
    if where_sql:
        try:
            qualified = _qualify_where_sql(where_sql, table)
            new_qs = qs.filter(RawSQL(qualified, (), output_field=BooleanField()))
            new_qs.explain()  # force DB-level validation without fetching rows
            qs = new_qs
            SavedWhereClause.add_recent(where_sql)
        except Exception as exc:
            where_error = str(exc)

    return qs, where_sql, where_error


def annotate_distance(qs: QuerySet, ref) -> QuerySet:
    """Add distance_km and bearing_deg annotations from a reference point.

    Uses the DistanceCache table when populated (fast indexed join).
    Falls back to the Python-based SQLite haversine callback otherwise.
    """
    from .models import DistanceCache

    has_cache = DistanceCache.objects.filter(ref_point_id=ref.pk).exists()
    if has_cache:
        from django.db.models import F, Q
        from django.db.models import FilteredRelation

        return qs.annotate(
            _dc=FilteredRelation(
                "distancecache",
                condition=Q(distancecache__ref_point_id=ref.pk),
            ),
        ).annotate(
            distance_km=F("_dc__distance_km"),
            bearing_deg=F("_dc__bearing_deg"),
        )

    # Fallback: per-row Python haversine (slow for large datasets)
    # Use corrected coords when available via LEFT JOIN on CorrectedCoordinates
    from django.db.models.expressions import RawSQL

    lat_expr = (
        "COALESCE("
        "(SELECT cc.latitude FROM geocaches_correctedcoordinates cc"
        " WHERE cc.geocache_id = geocaches_geocache.id),"
        " geocaches_geocache.latitude)"
    )
    lon_expr = (
        "COALESCE("
        "(SELECT cc.longitude FROM geocaches_correctedcoordinates cc"
        " WHERE cc.geocache_id = geocaches_geocache.id),"
        " geocaches_geocache.longitude)"
    )

    return qs.annotate(
        distance_km=RawSQL(
            f"haversine_km(%s, %s, {lat_expr}, {lon_expr})",
            (ref.latitude, ref.longitude),
        ),
        bearing_deg=RawSQL(
            f"bearing_deg(%s, %s, {lat_expr}, {lon_expr})",
            (ref.latitude, ref.longitude),
        ),
    )


def apply_radius_filter(qs: QuerySet, radius_str: str, distance_unit: str) -> QuerySet:
    """Filter by radius (converts mi to km if needed)."""
    try:
        radius_val = float(radius_str)
        radius_km = radius_val / 1.60934 if distance_unit == "mi" else radius_val
        return qs.filter(distance_km__lte=radius_km)
    except ValueError:
        return qs


def apply_bearing_filter(qs: QuerySet, bearing_csv: str) -> QuerySet:
    """Filter by compass bearing directions (CSV of N,NE,E,SE,S,SW,W,NW)."""
    dirs = [d.strip().upper() for d in bearing_csv.split(",") if d.strip()]
    bq = Q()
    for d in dirs:
        for lo, hi in BEARING_RANGES.get(d, []):
            bq |= Q(bearing_deg__gte=lo, bearing_deg__lte=hi)
    if bq:
        return qs.filter(bq)
    return qs


# ---------------------------------------------------------------------------
# Filter value dict and chips
# ---------------------------------------------------------------------------

_OP_LABELS = {
    "contains": "contains", "not_contains": "not contains",
    "starts_with": "starts with", "not_starts_with": "not starts with",
    "equals": "=", "not_equals": "≠",
    "in_list": "in list", "not_in_list": "not in list",
    "empty": "is empty", "not_empty": "not empty",
}


def build_filter_values(params: dict, where_sql: str = "",
                        where_error: str = "", where_name: str = "") -> dict:
    """Build the fv dict from request params. Same keys as today."""
    return {
        "q": params.get("q", "").strip(),
        "cache_type": params.get("type", ""), "status": params.get("status", ""),
        "size": params.get("size", ""), "found": params.get("found", ""),
        "flag": params.get("flag", ""), "elevation": params.get("elevation", ""),
        "tag": params.get("tag", ""),
        "tags_include": params.get("tags_include", ""),
        "tags_exclude": params.get("tags_exclude", ""),
        "country": params.get("country", ""),
        "country_exclude": params.get("country_exclude", ""),
        "state": params.get("state", ""), "county": params.get("county", ""),
        "state_exclude": params.get("state_exclude", ""),
        "county_exclude": params.get("county_exclude", ""),
        "missing": params.get("missing", ""),
        # advanced
        "fname": params.get("fname", "").strip(), "fname_op": params.get("fname_op", "contains"),
        "fcode": params.get("fcode", "").strip(), "fcode_op": params.get("fcode_op", "contains"),
        "fowner": params.get("fowner", "").strip(), "fowner_op": params.get("fowner_op", "contains"),
        "fplacedby": params.get("fplacedby", "").strip(), "fplacedby_op": params.get("fplacedby_op", "contains"),
        "ftext": params.get("ftext", "").strip(),
        "types": params.get("types", ""), "sizes": params.get("sizes", ""),
        "statuses": params.get("statuses", ""),
        "diff_min": params.get("diff_min", ""), "diff_max": params.get("diff_max", ""),
        "terr_min": params.get("terr_min", ""), "terr_max": params.get("terr_max", ""),
        "fav_min": params.get("fav_min", ""), "fav_max": params.get("fav_max", ""),
        "hidden_from": params.get("hidden_from", ""), "hidden_to": params.get("hidden_to", ""),
        "lf_from": params.get("lf_from", ""), "lf_to": params.get("lf_to", ""),
        "fd_from": params.get("fd_from", ""), "fd_to": params.get("fd_to", ""),
        "flags": params.get("flags", ""), "flags_not": params.get("flags_not", ""),
        "attrs_yes": params.get("attrs_yes", ""), "attrs_no": params.get("attrs_no", ""),
        "bearing": params.get("bearing", ""),
        "radius": params.get("radius", "").strip(),
        "geo": params.get("geo", ""),
        "where_name": where_name, "where_sql": where_sql, "where_error": where_error,
    }


def _match_saved_area(geo_value: str):
    """If the geo param matches a saved area filter, return its name."""
    from .models import SavedAreaFilter

    def _regions_to_geo(regions):
        parts = []
        for r in regions:
            if r.get("type") == "rect" and r.get("bbox"):
                parts.append("rect:" + ",".join(f"{v:.6f}" for v in r["bbox"]))
            elif r.get("type") == "circle" and r.get("center") and r.get("radius_m") is not None:
                parts.append(f"circle:{r['center'][0]:.6f},{r['center'][1]:.6f},{round(r['radius_m'])}")
        return "|".join(parts)

    for area in SavedAreaFilter.objects.only("name", "regions"):
        if _regions_to_geo(area.regions) == geo_value:
            return area.name
    return None


def build_filter_chips(fv: dict) -> list:
    """Return list of (params_to_clear_csv, label, badge_class) for active advanced/hidden filters."""
    chips = []

    def chip(params, label, cls="bg-warning text-dark"):
        chips.append((params, label, cls))

    _NONE = "None"
    if fv.get("state"):
        chip("state", f"State: {_NONE if fv['state'] == '__none__' else fv['state']}")
    if fv.get("county"):
        chip("county", f"County: {_NONE if fv['county'] == '__none__' else fv['county']}")
    if fv.get("country_exclude"):
        from geocaches.countries import iso_to_name
        names = ", ".join(iso_to_name(c) for c in fv["country_exclude"].split(",") if c.strip())
        chip("country_exclude", f"Not in: {names}", "bg-danger text-white")
    if fv.get("state_exclude"):
        chip("state_exclude", f"Not state: {fv['state_exclude']}")
    if fv.get("county_exclude"):
        chip("county_exclude", f"Not county: {fv['county_exclude']}")
    if fv.get("missing"):
        chip("missing", f"Missing: {fv['missing']}")
    if fv.get("fname"):
        chip("fname,fname_op", f"Name {_OP_LABELS.get(fv['fname_op'], fv['fname_op'])}: {fv['fname']}")
    if fv.get("fcode"):
        chip("fcode,fcode_op", f"Code {_OP_LABELS.get(fv['fcode_op'], fv['fcode_op'])}: {fv['fcode']}")
    if fv.get("fowner"):
        chip("fowner,fowner_op", f"Owner {_OP_LABELS.get(fv['fowner_op'], fv['fowner_op'])}: {fv['fowner']}")
    if fv.get("fplacedby"):
        chip("fplacedby,fplacedby_op", f"Placed by {_OP_LABELS.get(fv['fplacedby_op'], fv['fplacedby_op'])}: {fv['fplacedby']}")
    if fv.get("ftext"):
        chip("ftext", f"Text: {fv['ftext']}")
    if fv.get("types"):
        chip("types", f"Types: {fv['types'].replace(',', ', ')}")
    if fv.get("sizes"):
        chip("sizes", f"Sizes: {fv['sizes'].replace(',', ', ')}")
    if fv.get("statuses"):
        chip("statuses", f"Status: {fv['statuses'].replace(',', ', ')}")
    if fv.get("diff_min") or fv.get("diff_max"):
        lo = fv.get("diff_min") or "1"
        hi = fv.get("diff_max") or "5"
        chip("diff_min,diff_max", f"D: {lo}–{hi}")
    if fv.get("terr_min") or fv.get("terr_max"):
        lo = fv.get("terr_min") or "1"
        hi = fv.get("terr_max") or "5"
        chip("terr_min,terr_max", f"T: {lo}–{hi}")
    if fv.get("fav_min") or fv.get("fav_max"):
        lo = fv.get("fav_min") or "0"
        hi = fv.get("fav_max") or "∞"
        chip("fav_min,fav_max", f"Favs: {lo}–{hi}")
    if fv.get("hidden_from") or fv.get("hidden_to"):
        chip("hidden_from,hidden_to", f"Hidden: {fv.get('hidden_from','')}–{fv.get('hidden_to','')}")
    if fv.get("lf_from") or fv.get("lf_to"):
        chip("lf_from,lf_to", f"Last found: {fv.get('lf_from','')}–{fv.get('lf_to','')}")
    if fv.get("fd_from") or fv.get("fd_to"):
        chip("fd_from,fd_to", f"Found: {fv.get('fd_from','')}–{fv.get('fd_to','')}")
    _FLAG_LABELS = {
        "ftf": "FTF", "dnf": "DNF", "user_flag": "Flagged",
        "is_premium": "Premium", "has_trackable": "Has trackable",
        "import_locked": "Import locked", "needs_maintenance": "Needs maintenance",
        "watch": "Watching", "corrected_coords": "Corrected coords",
    }
    for f in (fv.get("flags") or "").split(","):
        f = f.strip()
        if f:
            chip(f"flags={f}", f"✓ {_FLAG_LABELS.get(f, f)}", "bg-success text-white")
    for f in (fv.get("flags_not") or "").split(","):
        f = f.strip()
        if f:
            chip(f"flags_not={f}", f"✗ {_FLAG_LABELS.get(f, f)}", "bg-danger text-white")
    if fv.get("tags_include"):
        tag_list = fv["tags_include"].replace(",", ", ")
        chip("tags_include", f"Tags \u2713: {tag_list}", "bg-success text-white")
    if fv.get("tags_exclude"):
        tag_list = fv["tags_exclude"].replace(",", ", ")
        chip("tags_exclude", f"Tags \u2717: {tag_list}", "bg-danger text-white")
    if fv.get("attrs_yes"):
        chip("attrs_yes", f"Attr ✓: {fv['attrs_yes']}", "bg-success text-white")
    if fv.get("attrs_no"):
        chip("attrs_no", f"Attr ✗: {fv['attrs_no']}", "bg-danger text-white")
    if fv.get("bearing"):
        chip("bearing", f"Bearing: {fv['bearing'].replace(',', ' ')}")
    if fv.get("geo"):
        n = fv["geo"].count("|") + 1
        area_name = _match_saved_area(fv["geo"])
        if area_name:
            chip("geo", f"Map area {area_name} ({n} region{'s' if n > 1 else ''})", "bg-info text-dark")
        else:
            chip("geo", f"Map area ({n} region{'s' if n > 1 else ''})", "bg-info text-dark")
    if fv.get("where_name"):
        style = "bg-danger text-white" if fv.get("where_error") else "bg-info text-dark"
        chip("where_name,where_sql", f"Where: {fv['where_name']}", style)
    elif fv.get("where_sql"):
        style = "bg-danger text-white" if fv.get("where_error") else "bg-info text-dark"
        chip("where_name,where_sql", f"SQL: {fv['where_sql'][:30]}…", style)
    return chips


# ---------------------------------------------------------------------------
# Convenience: apply everything in one call
# ---------------------------------------------------------------------------


def apply_all(qs: QuerySet, params: dict, ref=None,
              distance_unit: str = "km") -> tuple:
    """Convenience: apply scope + filters + where + distance/bearing.
    Returns (filtered_qs, fv_dict).
    Used by both list view and map endpoint.
    """
    qs = apply_scope(qs)
    qs = apply_filters(qs, params)
    qs, where_sql, where_error = apply_where_clause(qs, params)
    where_name = params.get("where_name", "").strip()

    if ref:
        qs = annotate_distance(qs, ref)
        radius_str = params.get("radius", "").strip()
        if radius_str:
            qs = apply_radius_filter(qs, radius_str, distance_unit)
        bearing = params.get("bearing", "")
        if bearing:
            qs = apply_bearing_filter(qs, bearing)

    fv = build_filter_values(params, where_sql, where_error, where_name)
    return qs, fv
