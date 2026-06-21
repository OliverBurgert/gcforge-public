from django.core.paginator import Paginator
from django.db.models import BooleanField, Case, Value, When
from django.shortcuts import render
from django.utils.translation import gettext_lazy as _

from ..filter_expr import Condition, Group, OP_AND, OP_OR, to_url_param
from ..models import Attribute, CacheSize, CacheStatus, CacheType, Geocache, LogType, SavedFilter, SavedWhereClause, Tag
from ..query import apply_all, build_filter_chips, mine_q


PAGE_SIZE = 50

# Params stripped before tacking on a fresh ``fx=…`` for the Enrich dropdown's
# "Show missing X" shortcuts.  Keeps sort / ref / target / q / etc., drops any
# previously-applied filter so the shortcut renders the missing-field view
# directly (matching the old links' behaviour, which were also fresh filters).
_FILTER_PARAMS_TO_CLEAR = (
    "fx", "f", "where_name", "where_sql",
    "state", "county", "country", "elevation", "missing", "flag",
)


def _build_enrich_filter_url(request, tree: Group) -> str:
    """URL to the list view with the legacy filter params stripped and the
    given filter-expression tree encoded as ``fx``."""
    params = request.GET.copy()
    for key in _FILTER_PARAMS_TO_CLEAR:
        if key in params:
            del params[key]
    params["fx"] = to_url_param(tree)
    return f"{request.path}?{params.urlencode()}"

SORT_FIELDS = {
    "gc_code":          "gc_code",
    "name":             "name",
    "cache_type":       "cache_type",
    "size":             "size",
    "difficulty":       "difficulty",
    "terrain":          "terrain",
    "status":           "status",
    "country":          "country",
    "state":            "state",
    "county":           "county",
    "elevation":        "elevation",
    "hidden_date":      "hidden_date",
    "last_found_date":  "last_found_date",
    "found_date":       "found_date",
    "fav_points":       "fav_points",
    "updated_at":       "updated_at",
    "owner":            "owner",
    "placed_by":        "placed_by",
    "distance_km":      "distance_km",
    "bearing_deg":      "bearing_deg",
}


_TABBED_TEXT_FIELDS = [
    ("name",        _("Name")),
    ("code",        _("Cache code")),
    ("owner",       _("Owner")),
    ("placed_by",   _("Placed by")),
    ("description", _("Description / hint")),
]

_TABBED_BOOL_FIELDS = [
    ("is_premium",        _("Premium")),
    ("has_trackable",     _("Has trackable")),
    ("needs_maintenance", _("Needs maintenance")),
    ("found",             _("Found")),
    ("ftf",               _("FTF")),
    ("dnf",               _("DNF flag")),
    ("user_flag",         _("User flag")),
    ("watch",             _("Watching")),
    ("has_corrected_coordinates", _("Corrected coords")),
    ("import_locked",     _("Import locked")),
]

_TABBED_DATE_FIELDS = [
    ("hidden_date",     _("Hidden date")),
    ("last_found_date", _("Last found")),
    ("found_date",      _("Found by me")),
    ("dnf_date",        _("DNF date")),
    ("updated_at",      _("Updated")),
    ("last_gpx_date",   _("Last GPX")),
    ("imported_at",     _("Imported")),
]

_TABBED_ALC_BOOL_FIELDS = [
    ("is_adventure",         _("Is an ALC adventure")),
    ("is_stage",             _("Is an ALC stage")),
    ("is_final",             _("Is the final stage")),
    ("in_progress",          _("Adventure in progress")),
    ("loggable_from_ref",    _("Loggable from active ref")),
    ("has_theme_image",      _("Has theme image")),
    ("is_highly_recommended", _("Highly recommended")),
]

_FLAG_CHOICES = [
    ("ftf", "FTF"),
    ("dnf", "DNF"), ("user_flag", "Flagged"),
    ("is_premium", "Premium"), ("has_trackable", "Has trackable"),
    ("my_tb_inside", "Contains my TB"),
    ("import_locked", "Import locked"), ("needs_maintenance", "Needs maintenance"),
    ("watch", "Watching"), ("corrected_coords", "Corrected coords"),
]

_BEARING_DIRS = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]


def _filtered_qs(request, qs=None):
    """Apply all filters (scope + params + where + distance/bearing) to a queryset.

    Resolves the active reference point and distance unit so radius/bearing
    filters are honoured — unlike a bare ``apply_all(qs, request.GET)`` call
    which silently skips distance filters when no *ref* is passed.
    """
    from preferences.models import UserPreference
    from preferences.services import resolve_active_reference_point
    from ..query import apply_all

    if qs is None:
        qs = Geocache.objects.all()

    distance_unit = UserPreference.get("distance_unit", "km")
    rrp = resolve_active_reference_point(request)
    ref = rrp.ref_point

    if ref:
        from ..geo.distance_cache import ensure_cached
        ensure_cached(ref)

    from ..query import apply_action_scope
    qs, fv = apply_all(qs, request.GET, ref=ref, distance_unit=distance_unit)
    qs = apply_action_scope(qs, request.GET)
    return qs, fv


def cache_list(request):
    from preferences.views import get_active_columns, get_active_preset_name
    from preferences.models import ColumnPreset, ReferencePoint, UserPreference
    from preferences.services import resolve_active_reference_point
    from ..query import tree_to_toolbar_state

    qs = Geocache.objects.select_related("adventure", "corrected_coordinates", "al_detail").prefetch_related("tags")

    # --- resolve reference point ---
    distance_unit = UserPreference.get("distance_unit", "km")
    ref_points = list(ReferencePoint.objects.all())
    rrp = resolve_active_reference_point(request, ref_points=ref_points)
    ref = rrp.ref_point

    # Ensure the distance cache is populated for fast distance queries.
    if ref:
        from ..geo.distance_cache import ensure_cached
        ensure_cached(ref)

    # --- apply all filters (scope + explicit + where + distance/bearing) ---
    qs, fv = apply_all(qs, request.GET, ref=ref, distance_unit=distance_unit)

    radius_str = fv.get("radius", "")

    # --- sort ---
    default_sort = UserPreference.get("default_sort", "gc_code")
    default_order = UserPreference.get("default_order", "asc")
    sort = request.GET.get("sort", default_sort)
    order = request.GET.get("order", default_order)
    sort_field = SORT_FIELDS.get(sort, "gc_code")
    if sort_field in ("distance_km", "bearing_deg") and not ref:
        sort_field = "gc_code"
    qs = qs.order_by(f"{'-' if order == 'desc' else ''}{sort_field}")

    # --- annotate is_mine ---
    from accounts.models import UserAccount
    _accounts = list(UserAccount.objects.all())
    _mine_q = mine_q() if _accounts else None
    if _mine_q is not None:
        qs = qs.annotate(
            is_mine=Case(When(_mine_q, then=Value(True)), default=Value(False), output_field=BooleanField())
        )

    # --- paginate ---
    page_size = UserPreference.get("page_size", PAGE_SIZE)
    paginator = Paginator(qs, page_size)
    page_obj = paginator.get_page(request.GET.get("page"))

    # --- action scope: counts and IDs for the scope picker ---
    f_target = request.GET.get("target", "filter").strip()
    if f_target not in ("filter", "viewport", "page"):
        f_target = "filter"
    f_vbox = request.GET.get("vbox", "").strip()
    viewport_count = None
    if f_vbox:
        try:
            south, west, north, east = (float(x) for x in f_vbox.split(","))
            viewport_count = qs.filter(
                latitude__gte=south, latitude__lte=north,
                longitude__gte=west, longitude__lte=east,
            ).count()
        except ValueError:
            viewport_count = None
    page_count = len(page_obj.object_list)
    page_ids_csv = ",".join(str(c.pk) for c in page_obj.object_list)

    # --- map-visibility per-row state (avoid N+1: single session-codes set) ---
    from geocaches.services.map_visibility import MapVisibility, hidden_codes_in_session
    _mv_hidden_session = hidden_codes_in_session(request.session)
    for _c in page_obj.object_list:
        if _c.map_hidden_always:
            _c.map_visibility_state = MapVisibility.ALWAYS
        elif _c.display_code in _mv_hidden_session:
            _c.map_visibility_state = MapVisibility.SESSION
        else:
            _c.map_visibility_state = MapVisibility.VISIBLE

    # --- column preset ---
    active_columns = get_active_columns(request)
    active_preset_name = get_active_preset_name(request)

    # --- filter option lists ---
    tags = Tag.objects.select_related("default_ref_point").order_by("name")
    from geocaches.geo.countries import iso_to_name as _iso_to_name
    iso_codes = (
        Geocache.objects.exclude(iso_country_code="")
        .values_list("iso_country_code", flat=True)
        .distinct()
        .order_by("iso_country_code")
    )
    countries = [{"code": code, "name": _iso_to_name(code)} for code in iso_codes]
    countries.sort(key=lambda c: c["name"])
    has_no_country = Geocache.objects.filter(iso_country_code="").exists()
    has_untagged = Geocache.objects.filter(tags__isnull=True).exists()
    has_accounts = bool(_accounts)

    # --- attributes for dialog ---
    from itertools import groupby
    all_attributes = list(Attribute.objects.order_by("source", "name", "-is_positive"))
    attrs_by_source = {}
    for src, grp in groupby(all_attributes, key=lambda a: a.source):
        attrs_by_source[src] = list(grp)

    # --- saved filters & where clauses ---
    all_filters = list(SavedFilter.objects.all())
    builtin_filters = [f for f in all_filters if f.is_builtin]
    user_filters = [f for f in all_filters if not f.is_builtin]
    # SavedFilters with a non-null tree show up in the "Custom filter" modal's
    # load dropdown — those are the ones that can be applied via ?f=<name>.
    tree_saved_filters = [f for f in all_filters if f.tree]
    named_where_clauses = list(SavedWhereClause.objects.filter(name__gt="").order_by("name"))
    recent_where_clauses = list(SavedWhereClause.objects.filter(name="").order_by("-updated_at")[:10])

    # Pretty-printed JSON of the currently-active tree, to pre-populate the
    # textarea in the Custom-filter modal.  Empty AND group when nothing is
    # active.
    import json as _json
    current_tree_dict = {"g": "and", "c": []}
    if fv.get("fx"):
        try:
            from ..filter_expr import from_url_param
            current_tree_dict = from_url_param(fv["fx"]).to_dict()
        except Exception:
            current_tree_dict = {"g": "and", "c": []}
    current_tree_json = _json.dumps(current_tree_dict, indent=2)

    # 4d-ii-B: toolbar dropdowns must stay in sync with the canonical fx tree.
    # After normalisation, the URL has no legacy ?type=, so fv["cache_type"]
    # is empty — instead, derive each toolbar field from the decoded tree.
    toolbar_state = tree_to_toolbar_state(fv.get("fx", ""), fv.get("f_name", ""))

    context = {
        "page_obj": page_obj,
        "total": paginator.count,
        "cache_types": CacheType.choices,
        "cache_statuses": CacheStatus.choices,
        "cache_sizes": CacheSize.choices,
        "tags": tags,
        "countries": countries,
        "has_no_country": has_no_country,
        "has_untagged": has_untagged,
        # quick filter values
        # action scope picker
        "f_target": f_target,
        "f_vbox": f_vbox,
        "viewport_count": viewport_count,
        "page_count": page_count,
        "page_ids_csv": page_ids_csv,
        "f_q": fv["q"],
        # Toolbar dropdowns — derived from the fx tree so they stay in sync
        # with the canonical filter state.  Legacy ?type= etc. is carried by
        # the ?fx= tree; tree_to_toolbar_state() (above) decodes the tree back
        # into these per-field selected values.
        "f_type":      toolbar_state["f_type"]    or fv["cache_type"],
        "f_status":    toolbar_state["f_status"]  or fv["status"],
        "f_size":      toolbar_state["f_size"]    or fv["size"],
        "f_found":     toolbar_state["f_found"]   or fv["found"],
        "f_flag":      toolbar_state["f_flag"]    or fv["flag"],
        "f_elevation": fv["elevation"],
        "f_tag":       toolbar_state["f_tag"]     or fv["tag"],
        "f_country":   toolbar_state["f_country"] or fv["country"],
        # advanced filter values (passed to template for hidden inputs + dialog pre-pop)
        "fv": fv,
        # chip badges for active advanced/hidden filters
        "active_filter_chips": build_filter_chips(fv, request=request),
        "f_sort": sort,
        "f_order": order,
        "f_radius": radius_str,
        "f_ref": str(ref.pk) if ref else "",
        "ref_point": ref,
        "ref_points": ref_points,
        "distance_unit": distance_unit,
        "active_columns": active_columns,
        "active_preset_name": active_preset_name,
        "column_presets": ColumnPreset.objects.all(),
        "cache_type_display": UserPreference.get("cache_type_display", "icon"),
        "has_accounts": has_accounts,
        # dialog data
        "attrs_by_source": attrs_by_source,
        "builtin_filters": builtin_filters,
        "user_filters": user_filters,
        "tree_saved_filters": tree_saved_filters,
        "current_tree_json": current_tree_json,
        "current_tree_dict": current_tree_dict,
        "saved_filter_trees": {sf.name: sf.tree for sf in tree_saved_filters},
        # ── 4d-i: tabbed dialog config tables ──────────────────────────────
        "tabbed_text_fields": _TABBED_TEXT_FIELDS,
        "tabbed_bool_fields": _TABBED_BOOL_FIELDS,
        "tabbed_date_fields": _TABBED_DATE_FIELDS,
        "tabbed_alc_bool_fields": _TABBED_ALC_BOOL_FIELDS,
        "log_types": LogType.choices,
        # ───────────────────────────────────────────────────────────────────
        "named_where_clauses": named_where_clauses,
        "recent_where_clauses": recent_where_clauses,
        "bearing_dirs": _BEARING_DIRS,
        "flag_choices": _FLAG_CHOICES,
        # Enrich dropdown shortcuts — replace legacy ``&state=__none__`` style
        # links with proper fx-encoded filters.
        "fx_url_missing_any": _build_enrich_filter_url(request, Group(OP_OR, [
            Condition("country",   "is_none"),
            Condition("state",     "is_none"),
            Condition("county",    "is_none"),
            Condition("elevation", "is_null", True),
        ])),
        "fx_url_missing_elevation": _build_enrich_filter_url(request, Group(OP_AND, [
            Condition("elevation", "is_null", True),
        ])),
        "fx_url_missing_state": _build_enrich_filter_url(request, Group(OP_AND, [
            Condition("state", "is_none"),
        ])),
        "fx_url_missing_county": _build_enrich_filter_url(request, Group(OP_AND, [
            Condition("county", "is_none"),
        ])),
    }

    if request.headers.get("HX-Request"):
        return render(request, "geocaches/partials/_table_with_oob.html", context)
    return render(request, "geocaches/list.html", context)
