"""
Reusable query helpers for geocache filtering.
Used by cache_list and map_markers (geocaches/views/).
"""

from dataclasses import dataclass, field

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


def _all_accounts():
    """Return cached list of all UserAccount rows."""
    from accounts.models import UserAccount
    return list(UserAccount.objects.all())


@dataclass
class MyIdentities:
    """Resolved "which identities count as me" sets, derived once from the
    configured :class:`~accounts.models.UserAccount` rows.

    A single source of truth so the several places that decide "is this
    mine?" can't drift.  Each consumer *applies* these sets differently
    (ORM ``Q`` for caches vs logs, a Python predicate over ``values_list``
    rows, or a per-source log filter) — that's intentional; only the
    underlying identity data is shared.

    Fields:
      * ``gc_owner_ids`` — numeric GC ids (``platform == "gc"`` and the
        ``user_id`` is all digits), as ints.  Matches the stable
        ``Geocache.owner_gc_id`` column.
      * ``usernames`` — every account ``username`` (any platform).  Used as
        the owner/finder name fallback.
      * ``finder_ids`` — every account ``user_id`` string (any platform), for
        matching ``Log.user_id``.
      * ``platform_map`` — ``{platform: (user_id set, username set)}`` keyed by
        platform, for per-source log matching.  When a ``gc_username`` fallback
        is supplied it is folded into ``platform_map["gc"]``'s name set.
      * ``all_ids`` / ``all_names`` — unions across ``platform_map`` (i.e. the
        same data as ``finder_ids`` / ``usernames``, plus any ``gc_username``
        fallback) for blank-``source`` logs.
      * ``has_any`` — True when at least one id or name is configured.
    """
    gc_owner_ids: set[int] = field(default_factory=set)
    usernames: set[str] = field(default_factory=set)
    finder_ids: set[str] = field(default_factory=set)
    platform_map: dict[str, tuple[set[str], set[str]]] = field(default_factory=dict)
    all_ids: set[str] = field(default_factory=set)
    all_names: set[str] = field(default_factory=set)

    @property
    def has_any(self) -> bool:
        return bool(self.all_ids or self.all_names)


def resolve_my_identities(gc_username: str = "") -> MyIdentities:
    """Resolve the identity sets that count as "me" from configured accounts.

    Reads every :class:`~accounts.models.UserAccount` once and projects it
    into the several shapes consumers need.  ``gc_username`` is an optional
    legacy preference fallback folded into the GC platform's name set (and
    therefore into ``all_names``); pass it only where that fallback applies
    (the detail-page "my" log filter).  Callers that never used the fallback
    (``mine_q`` / ``mine_finder_q`` / the map's per-row check) leave it empty,
    preserving their current behaviour exactly.
    """
    ids = MyIdentities()
    for acc in _all_accounts():
        pm = ids.platform_map.setdefault(acc.platform, (set(), set()))
        if acc.user_id:
            ids.finder_ids.add(acc.user_id)
            ids.all_ids.add(acc.user_id)
            pm[0].add(acc.user_id)
        if acc.username:
            ids.usernames.add(acc.username)
            ids.all_names.add(acc.username)
            pm[1].add(acc.username)
        if acc.platform == "gc" and acc.user_id.isdigit():
            ids.gc_owner_ids.add(int(acc.user_id))
    if gc_username:
        pm = ids.platform_map.setdefault("gc", (set(), set()))
        pm[1].add(gc_username)
        ids.all_names.add(gc_username)
    return ids


def mine_q() -> Q:
    """Q object matching caches owned by any configured UserAccount.

    Prefers stable numeric owner_gc_id; falls back to username string match.
    Returns an always-false Q (pk__in=[]) when no accounts are configured.
    """
    ids = resolve_my_identities()
    if not ids.platform_map:
        return Q(pk__in=[])
    q = Q()
    if ids.gc_owner_ids:
        q |= Q(owner_gc_id__in=ids.gc_owner_ids)
    # Username fallback for accounts without a user_id, and for OC where owner_gc_id is null
    if ids.usernames:
        q |= Q(owner__in=ids.usernames)
    return q


def mine_finder_q() -> tuple[Q, bool]:
    """Q object matching Log rows authored by any configured UserAccount.

    Returns (q, has_accounts). The Q matches on user_id and/or user_name.
    """
    ids = resolve_my_identities()
    if not ids.platform_map:
        return Q(pk__in=[]), False
    q = Q()
    if ids.finder_ids:
        q |= Q(user_id__in=ids.finder_ids)
    if ids.usernames:
        q |= Q(user_name__in=ids.usernames)
    return q, ids.has_any


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
    """Apply the residual GET-param filters via ``FILTER_CHAIN``.

    The canonical filter system is the ``?fx=`` tree (applied by
    :func:`apply_filter_expr`).  This chain handles only the URL params that
    can't become pure-Q tree conditions and therefore remain by design:
    ``?q=`` quick search (multi-field OR), exotic ``?flag=`` (``my_tb_inside``
    Trackable subquery), ``?elevation=`` named bands (the ``none`` band needs a
    two-column null predicate), and ``?geo=`` drawn areas (circle / polygon /
    corridor need Python haversine / point-in-polygon).  See
    :mod:`geocaches.filters` for the per-param rationale.
    """
    for apply_fn in FILTER_CHAIN:
        qs = apply_fn(qs, params)
    return qs


def _qualify_where_sql(sql: str, table: str) -> str:
    """Prefix bare column names with the Geocache table to avoid ambiguity.

    Only qualifies names that appear as bare identifiers (not already dotted)
    and that match actual Geocache column names.

    Text inside SQL string literals (single-quoted, honouring ``''`` escapes)
    and inside double-quoted identifiers is left untouched — qualifying a
    column-name word that happens to appear inside e.g. ``name LIKE '%county%'``
    would silently change which rows match.  We scan the clause splitting it
    into quoted spans (left verbatim) and unquoted spans (qualified), so the
    identifier regex only ever sees code, never literals.
    """
    import re
    from .models import Geocache
    columns = {f.column for f in Geocache._meta.get_fields()
               if hasattr(f, "column") and f.column}

    _IDENT_RE = re.compile(r'\b([a-z_][a-z0-9_]*)\b', re.IGNORECASE)

    def _qualify_segment(segment: str) -> str:
        def _replace(m):
            word = m.group(0)
            if word in columns:
                start = m.start()
                if start > 0 and segment[start - 1] == ".":
                    return word  # already dotted/qualified
                return f"{table}.{word}"
            return word
        return _IDENT_RE.sub(_replace, segment)

    out: list[str] = []
    buf: list[str] = []  # pending unquoted code
    i, n = 0, len(sql)
    while i < n:
        ch = sql[i]
        if ch in ("'", '"'):
            # Flush accumulated code, qualifying it, then copy the quoted span
            # (including its delimiters) verbatim.
            out.append(_qualify_segment("".join(buf)))
            buf = []
            quote = ch
            j = i + 1
            while j < n:
                if sql[j] == quote:
                    # A doubled quote ('' or "") is an escaped delimiter, not
                    # the end of the span — skip both characters.
                    if j + 1 < n and sql[j + 1] == quote:
                        j += 2
                        continue
                    j += 1  # consume the closing quote
                    break
                j += 1
            out.append(sql[i:j])
            i = j
        else:
            buf.append(ch)
            i += 1
    out.append(_qualify_segment("".join(buf)))
    return "".join(out)


def tree_to_toolbar_state(fx_value: str, f_name: str = "") -> dict:
    """Decode ``fx`` (and resolve ``?f=<name>`` to its tree) into the
    f_type/f_status/etc. variables the toolbar template uses for selected
    states.  Empty for fields not represented in the tree.
    """
    from .filter_expr import (
        Condition,
        FilterExprError,
        Group,
        from_url_param,
    )

    state = {
        "f_type":      "",
        "f_status":    "",
        "f_size":      "",
        "f_found":     "",
        "f_flag":      "",
        "f_country":   "",
        "f_tag":       "",
    }

    tree = None
    if fx_value:
        try:
            tree = from_url_param(fx_value)
        except FilterExprError:
            return state
    elif f_name:
        from .models import SavedFilter
        try:
            sf = SavedFilter.objects.get(name=f_name)
        except SavedFilter.DoesNotExist:
            return state
        if not sf.tree:
            return state
        try:
            tree = Group.from_dict(sf.tree)
        except FilterExprError:
            return state

    if tree is None:
        return state

    field_to_state_key = {
        "cache_type": "f_type",
        "status":     "f_status",
        "size":       "f_size",
        "country":    "f_country",
    }
    flag_field_to_value = {
        "ftf":                       "ftf",
        "dnf":                       "dnf",
        "user_flag":                 "user_flag",
        "is_premium":                "is_premium",
        "has_trackable":             "has_trackable",
        "import_locked":             "import_locked",
        "needs_maintenance":         "needs_maintenance",
        "watch":                     "watch",
        "has_corrected_coordinates": "corrected_coords",
    }

    for child in tree.children:
        if not isinstance(child, Condition):
            continue
        if child.field == "tags" and child.op == "in":
            v = child.value
            if isinstance(v, list) and len(v) == 1:
                state["f_tag"] = v[0]
            continue
        if child.field == "found" and child.op == "is_true":
            state["f_found"] = "1"
            continue
        if child.field == "found" and child.op == "is_false":
            state["f_found"] = "0"
            continue
        if child.field in flag_field_to_value and child.op == "is_true":
            # Toolbar's flag dropdown can show one selected value at a time;
            # last wins if multiple flag conditions are present.
            state["f_flag"] = flag_field_to_value[child.field]
            continue
        if child.field in field_to_state_key and child.op == "in":
            v = child.value
            if isinstance(v, list) and len(v) == 1:
                state[field_to_state_key[child.field]] = v[0]
    return state


def apply_filter_expr(qs: QuerySet, params: dict) -> tuple:
    """Apply v2 filter-expression filters: ``?fx=<encoded>`` and/or ``?f=<name>``.

    Two sources of a tree, ANDed onto the qs:
      * ``?fx=`` — inline encoded tree (zlib+base64 JSON).
      * ``?f=`` — a named ``SavedFilter`` whose ``tree`` field holds the same
        shape.  If the filter has only a legacy ``params`` payload (i.e.
        backfill failed or the entry is a built-in with an unusual shape),
        ``?f=`` is a no-op for the tree path — the legacy chain handles it.

    Returns ``(qs, fx_param, fx_error, fx_count)``:
      * ``fx_param`` is the original encoded ``fx`` verbatim (empty when only
        ``?f=`` was used) so chip rendering can round-trip URLs.
      * ``fx_error`` is set when ``fx`` is present but malformed, or when the
        named ``?f=`` doesn't exist.  The qs is left untouched in either case.
      * ``fx_count`` is the total number of Condition leaves across both
        sources, for the chip label.

    ``?fx=`` is the canonical filter representation.  The residual
    ``apply_filters`` chain (``?q=`` / ``?elevation=`` / ``?flag=`` /
    ``?geo=``) runs first and tree output is ANDed onto its result; those
    residuals stay by design because they can't become pure-Q tree conditions
    (see :mod:`geocaches.filters`), not as a deprecated codepath.
    """
    from .filter_expr import (
        FilterExprError,
        Group,
        compile_tree,
        count_conditions,
        from_url_param,
    )

    fx = (params.get("fx") or "").strip()
    f_name = (params.get("f") or "").strip()

    trees: list = []
    errors: list[str] = []

    if fx:
        try:
            trees.append(from_url_param(fx))
        except FilterExprError as exc:
            errors.append(str(exc))

    if f_name:
        from .models import SavedFilter
        try:
            sf = SavedFilter.objects.get(name=f_name)
        except SavedFilter.DoesNotExist:
            errors.append(f"Saved filter not found: {f_name!r}")
        else:
            if sf.tree:
                try:
                    trees.append(Group.from_dict(sf.tree))
                except FilterExprError as exc:
                    errors.append(f"Saved filter {f_name!r} has malformed tree: {exc}")
            # else: no tree backfill — legacy params chain handles it.

    if not trees:
        return qs, fx, "; ".join(errors), 0

    # Compile each tree, AND them together.  Multiple sources compose with
    # AND because that's how the user reads "apply fx and also apply this
    # saved filter".
    q = None
    leaves = 0
    for tree in trees:
        try:
            child_q = compile_tree(tree)
        except FilterExprError as exc:
            errors.append(str(exc))
            continue
        q = child_q if q is None else (q & child_q)
        leaves += count_conditions(tree)

    if q is not None:
        from django.core.exceptions import FieldError
        try:
            qs = qs.filter(q)
        except FieldError as exc:
            # Annotation-dependent condition without the needed annotation
            # (distance_km, bearing_deg, etc.).  Surface as fx_error rather
            # than 500-ing the page.
            errors.append(f"Filter compile error: {exc}")
    return qs, fx, "; ".join(errors), leaves


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


def apply_action_scope(qs: QuerySet, params) -> QuerySet:
    """Narrow qs by the action ``target`` (filter / viewport / page).

    Called by ``_filtered_qs`` so every action endpoint inherits this. The list
    view itself does NOT apply this — it always shows the full filtered set.

    target=filter (default) — no narrowing.
    target=viewport         — AND a bbox from ?vbox=south,west,north,east.
                              Preserves existing region filters (geo=, country=,
                              radius=) — this is one more AND clause on top.
    target=page             — filter to ?ids=PK1,PK2,... (visible page rows).
                              JS injects ids at click time; if absent here we
                              treat as empty selection (no rows affected).
    """
    target = params.get("target", "filter").strip()
    if target == "viewport":
        vbox = params.get("vbox", "").strip()
        if vbox:
            try:
                south, west, north, east = (float(x) for x in vbox.split(","))
            except (ValueError, TypeError):
                return qs
            qs = qs.filter(
                latitude__gte=south, latitude__lte=north,
                longitude__gte=west, longitude__lte=east,
            )
    elif target == "page":
        ids = params.get("ids", "").strip()
        if not ids:
            return qs.none()
        try:
            pk_list = [int(x) for x in ids.split(",") if x.strip()]
        except ValueError:
            return qs.none()
        qs = qs.filter(pk__in=pk_list)
    return qs


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
                        where_error: str = "", where_name: str = "",
                        fx: str = "", fx_error: str = "", fx_count: int = 0,
                        f_name: str = "") -> dict:
    """Build the fv dict from request params. Same keys as today."""
    return {
        "fx": fx, "fx_error": fx_error, "fx_count": fx_count,
        "f_name": f_name,
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


def _tree_chip_href(current_get, new_fx_encoded: str, drop_f: bool) -> str:
    """Compute the URL to navigate to after removing a tree-leaf chip.

    ``current_get`` is a ``QueryDict`` (request.GET) or a regular dict.  We
    keep every param except ``fx``, ``f`` (when ``drop_f``), and ``page``,
    and set ``fx`` to ``new_fx_encoded`` (when non-empty).
    """
    from urllib.parse import urlencode
    pairs: list[tuple[str, str]] = []
    if hasattr(current_get, "lists"):
        items = current_get.lists()
    elif hasattr(current_get, "items"):
        items = [(k, [v]) for k, v in current_get.items()]
    else:
        items = []
    for k, vals in items:
        if k in ("fx", "page") or (drop_f and k == "f"):
            continue
        for v in vals:
            pairs.append((k, v))
    if new_fx_encoded:
        pairs.append(("fx", new_fx_encoded))
    return "?" + urlencode(pairs) if pairs else "?"


def _tree_chips(fv: dict, request) -> list:
    """Decode the active ``fx`` / ``f`` tree and emit per-leaf chips.

    Returns a list of ``("@<href>", label, css_class)`` tuples; the leading
    ``@`` is the sentinel the chip template uses to switch from
    gcfRemoveFilter() to a direct anchor href.  Each leaf chip's href
    encodes the URL the user lands on if they remove that single leaf —
    same query string but ``fx`` rewritten (or dropped if the result is
    empty).  When the active tree came from ``?f=<name>`` (saved filter),
    removing a leaf "detaches" — the chip URL drops ``?f=`` and writes
    the modified tree as ``?fx=``.
    """
    from .filter_expr import (
        Condition,
        FilterExprError,
        Group,
        condition_to_label,
        from_url_param,
        group_to_summary_label,
        to_url_param,
    )

    tree = None
    drop_f = False
    if fv.get("fx"):
        try:
            tree = from_url_param(fv["fx"])
        except FilterExprError:
            return []
    elif fv.get("f_name"):
        from .models import SavedFilter
        try:
            sf = SavedFilter.objects.get(name=fv["f_name"])
        except SavedFilter.DoesNotExist:
            return []
        if not sf.tree:
            return []
        try:
            tree = Group.from_dict(sf.tree)
        except FilterExprError:
            return []
        drop_f = True

    if tree is None or not tree.children:
        return []

    current_get = request.GET if request is not None else None
    out: list[tuple[str, str, str]] = []
    for i, child in enumerate(tree.children):
        new_children = list(tree.children[:i]) + list(tree.children[i + 1:])
        new_tree = Group(tree.op, new_children) if new_children else None
        new_fx = to_url_param(new_tree) if new_tree else ""
        href = _tree_chip_href(current_get, new_fx, drop_f) if current_get is not None else "#"

        if isinstance(child, Condition):
            label = condition_to_label(child.field, child.op, child.value)
        elif isinstance(child, Group):
            label = group_to_summary_label(child)
        else:
            label = "?"
        out.append(("@" + href, label, "bg-info text-dark"))
    return out


def build_filter_chips(fv: dict, request=None) -> list:
    """Return list of ``(action, label, badge_class)`` for active filters.

    ``action`` is either a CSV of URL params to drop via ``gcfRemoveFilter``
    (legacy chips), or ``"@<href>"`` for a direct-navigation tree chip.
    ``request`` is optional; without it tree chips render with ``href="#"``
    (non-removable). The list view always passes the live request.
    """
    chips = []

    def chip(params, label, cls="bg-warning text-dark"):
        chips.append((params, label, cls))

    _NONE = "None"
    if fv.get("state"):
        chip("state", f"State: {_NONE if fv['state'] == '__none__' else fv['state']}")
    if fv.get("county"):
        chip("county", f"County: {_NONE if fv['county'] == '__none__' else fv['county']}")
    if fv.get("country_exclude"):
        from geocaches.geo.countries import iso_to_name
        names = ", ".join(iso_to_name(c) for c in fv["country_exclude"].split(",") if c.strip())
        chip("country_exclude", f"Not in: {names}", "bg-danger text-white")
    if fv.get("state_exclude"):
        chip("state_exclude", f"Not state: {fv['state_exclude']}")
    if fv.get("county_exclude"):
        chip("county_exclude", f"Not county: {fv['county_exclude']}")
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
    # Tree-based filters (?fx= and/or ?f=).  Each leaf renders as its own
    # removable chip; nested sub-groups render as a single summary chip.
    fx_invalid = fv.get("fx_error") and not fv.get("f_name")
    f_invalid  = fv.get("fx_error") and fv.get("f_name")
    if f_invalid:
        # Surface "saved-filter-name not found / malformed" as a red chip.
        chip("f", f"Saved filter: {fv['f_name']} (invalid)", "bg-danger text-white")
    elif fv.get("f_name"):
        # Header chip identifying the source — clicking removes ?f= entirely.
        chip("f", f"Saved filter: {fv['f_name']}", "bg-info text-dark")
    if fx_invalid:
        chip("fx", "Custom filter (invalid)", "bg-danger text-white")
    else:
        chips.extend(_tree_chips(fv, request))
    return chips


# ---------------------------------------------------------------------------
# Convenience: apply everything in one call
# ---------------------------------------------------------------------------


_UNSET = object()


def apply_all(qs: QuerySet, params: dict, ref=None,
              distance_unit: str = "km", compile_ref=_UNSET) -> tuple:
    """Convenience: apply scope + filters + where + distance/bearing.
    Returns (filtered_qs, fv_dict).
    Used by both list view and map endpoint.

    ``ref`` controls distance/bearing **annotation** (omit for callers that
    don't need it — saves a join).  ``compile_ref`` controls the active
    reference point exposed to fx compile functions like
    ``alc.loggable_from_ref``; defaults to ``ref`` when unset.  The map
    endpoint passes ``ref=None, compile_ref=ref`` so the alc-loggable tree
    condition resolves the active toolbar ref without paying for the
    distance annotation on every marker fetch.
    """
    if compile_ref is _UNSET:
        compile_ref = ref
    qs = apply_scope(qs)
    qs = apply_filters(qs, params)

    # Annotate distance/bearing BEFORE apply_filter_expr so that any fx tree
    # referencing ``distance_km`` / ``bearing_deg`` doesn't trip a FieldError
    # at qs.filter() time.  When no ref is active, annotate the columns as
    # NULL so the conditions compile cleanly but match nothing — the user
    # sees an empty result and the chip flags the active filter; clearer than
    # a 500.
    if ref:
        qs = annotate_distance(qs, ref)
    else:
        from django.db.models import FloatField, Value
        qs = qs.annotate(
            distance_km=Value(None, output_field=FloatField()),
            bearing_deg=Value(None, output_field=FloatField()),
        )

    # Expose the active ref to compile functions that need it (e.g.
    # alc.loggable_from_ref).  Scoped so concurrent requests / nested calls
    # don't leak.
    from . import filter_expr as _fx
    _token = _fx.set_active_ref(compile_ref)
    try:
        qs, fx, fx_error, fx_count = apply_filter_expr(qs, params)
    finally:
        _fx.reset_active_ref(_token)
    qs, where_sql, where_error = apply_where_clause(qs, params)
    where_name = params.get("where_name", "").strip()

    if ref:
        radius_str = params.get("radius", "").strip()
        if radius_str:
            qs = apply_radius_filter(qs, radius_str, distance_unit)
        bearing = params.get("bearing", "")
        if bearing:
            qs = apply_bearing_filter(qs, bearing)

    if params.get("flag") == "alc_loggable_at_center":
        from .filters import apply_alc_loggable_filter
        qs = apply_alc_loggable_filter(qs, ref)

    f_name = (params.get("f") or "").strip()
    fv = build_filter_values(params, where_sql, where_error, where_name,
                             fx=fx, fx_error=fx_error, fx_count=fx_count,
                             f_name=f_name)
    return qs, fv
