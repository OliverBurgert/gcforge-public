import json
import logging
from datetime import datetime, timezone

_import_logger = logging.getLogger("geocaches.import")

from django.core.paginator import Paginator
from django.db.models import BooleanField, Case, Q, Value, When
from django.http import Http404, HttpResponse, HttpResponseBadRequest, HttpResponseNotAllowed, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render

from .models import (
    Attribute, CacheMapState, CacheSize, CacheStatus, CacheType,
    CorrectedCoordinates, Geocache, Log, Note, SavedFilter, SavedWhereClause, Tag,
)


PAGE_SIZE = 50


def _filtered_qs(request, qs=None):
    """Apply all filters (scope + params + where + distance/bearing) to a queryset.

    Resolves the active reference point and distance unit so radius/bearing
    filters are honoured — unlike a bare ``apply_all(qs, request.GET)`` call
    which silently skips distance filters when no *ref* is passed.
    """
    from preferences.models import ReferencePoint, UserPreference
    from .query import apply_all

    if qs is None:
        qs = Geocache.objects.all()

    distance_unit = UserPreference.get("distance_unit", "km")
    ref_points = list(ReferencePoint.objects.all())
    ref_id = request.GET.get("ref", "")
    if ref_id:
        ref = next((r for r in ref_points if str(r.pk) == ref_id), None)
    else:
        ref = next((r for r in ref_points if r.is_default), None) or (ref_points[0] if ref_points else None)

    if ref:
        from .distance_cache import ensure_cached
        ensure_cached(ref)

    qs, fv = apply_all(qs, request.GET, ref=ref, distance_unit=distance_unit)
    return qs, fv


def _get_cache(code, qs=None):
    """Look up a Geocache by gc_code or oc_code. Raises Http404 if not found."""
    if qs is None:
        qs = Geocache.objects.all()
    cache = qs.filter(gc_code=code).first() or qs.filter(al_code=code).first() or qs.filter(oc_code=code).first()
    if cache is None:
        raise Http404(f"No cache with code {code!r}")
    return cache

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

from .filters import FLAG_FIELDS
from .query import apply_all, build_filter_chips, mine_q


def cache_list(request):
    from preferences.views import get_active_columns, get_active_preset_name
    from preferences.models import ColumnPreset, ReferencePoint, UserPreference

    qs = Geocache.objects.select_related("adventure", "corrected_coordinates")

    # --- resolve reference point ---
    distance_unit = UserPreference.get("distance_unit", "km")
    ref_points = list(ReferencePoint.objects.all())
    ref_id = request.GET.get("ref", "")
    if ref_id:
        ref = next((r for r in ref_points if str(r.pk) == ref_id), None)
    else:
        ref = next((r for r in ref_points if r.is_default), None) or (ref_points[0] if ref_points else None)

    # Ensure the distance cache is populated for fast distance queries.
    if ref:
        from .distance_cache import ensure_cached
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

    # --- column preset ---
    active_columns = get_active_columns(request)
    active_preset_name = get_active_preset_name(request)

    # --- filter option lists ---
    tags = Tag.objects.select_related("default_ref_point").order_by("name")
    from geocaches.countries import iso_to_name as _iso_to_name
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
    named_where_clauses = list(SavedWhereClause.objects.filter(name__gt="").order_by("name"))
    recent_where_clauses = list(SavedWhereClause.objects.filter(name="").order_by("-updated_at")[:10])

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
        "f_q": fv["q"],
        "f_type": fv["cache_type"],
        "f_status": fv["status"],
        "f_size": fv["size"],
        "f_found": fv["found"],
        "f_flag": fv["flag"],
        "f_elevation": fv["elevation"],
        "f_tag": fv["tag"],
        "f_country": fv["country"],
        # advanced filter values (passed to template for hidden inputs + dialog pre-pop)
        "fv": fv,
        # chip badges for active advanced/hidden filters
        "active_filter_chips": build_filter_chips(fv),
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
        "has_accounts": has_accounts,
        # dialog data
        "attrs_by_source": attrs_by_source,
        "builtin_filters": builtin_filters,
        "user_filters": user_filters,
        "named_where_clauses": named_where_clauses,
        "recent_where_clauses": recent_where_clauses,
        "bearing_dirs": ["N", "NE", "E", "SE", "S", "SW", "W", "NW"],
        "flag_choices": [
            ("ftf", "FTF"),
            ("dnf", "DNF"), ("user_flag", "Flagged"),
            ("is_premium", "Premium"), ("has_trackable", "Has trackable"),
            ("import_locked", "Import locked"), ("needs_maintenance", "Needs maintenance"),
            ("watch", "Watching"), ("corrected_coords", "Corrected coords"),
        ],
    }

    if request.headers.get("HX-Request"):
        return render(request, "geocaches/_table.html", context)
    return render(request, "geocaches/list.html", context)


# ---------------------------------------------------------------------------
# Saved filter CRUD
# ---------------------------------------------------------------------------


def saved_filter_save(request):
    """POST: create or overwrite a saved filter by name."""
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    name = request.POST.get("name", "").strip()
    params_json = request.POST.get("params", "{}")
    if not name:
        return HttpResponseBadRequest("name required")
    try:
        params = json.loads(params_json)
    except ValueError:
        params = {}
    SavedFilter.objects.update_or_create(name=name, defaults={"params": params})
    next_url = request.POST.get("next", "").strip()
    if next_url and next_url.startswith("/"):
        return redirect(next_url)
    return redirect("geocaches:list")


def saved_filter_delete(request, pk):
    """POST: delete a saved filter (built-in filters are protected)."""
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    SavedFilter.objects.filter(pk=pk, is_builtin=False).delete()
    if request.headers.get("HX-Request"):
        all_filters = list(SavedFilter.objects.all())
        ctx = {
            "builtin_filters": [f for f in all_filters if f.is_builtin],
            "user_filters": [f for f in all_filters if not f.is_builtin],
        }
        return render(request, "geocaches/_saved_filters_options.html", ctx)
    return redirect("geocaches:list")


def where_clause_save(request):
    """POST: save a named where clause."""
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    name = request.POST.get("name", "").strip()
    sql = request.POST.get("sql", "").strip()
    if not name or not sql:
        return HttpResponseBadRequest("name and sql required")
    SavedWhereClause.objects.update_or_create(name=name, defaults={"sql": sql})
    if request.headers.get("HX-Request"):
        from django.http import JsonResponse
        named = list(SavedWhereClause.objects.filter(name__gt="").order_by("name").values("id", "name", "sql"))
        return HttpResponse(json.dumps(named), content_type="application/json")
    return redirect("geocaches:list")


def where_clause_delete(request, pk):
    """POST: delete a where clause (named or recent)."""
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    SavedWhereClause.objects.filter(pk=pk).delete()
    if request.headers.get("HX-Request"):
        from django.http import JsonResponse
        named = list(SavedWhereClause.objects.filter(name__gt="").order_by("name").values("id", "name", "sql"))
        recent = list(SavedWhereClause.objects.filter(name="").order_by("-updated_at")[:10].values("id", "sql", "updated_at"))
        return HttpResponse(json.dumps({"named": named, "recent": recent}), content_type="application/json")
    return redirect("geocaches:list")


_LOG_SUBMIT_EVENT_TYPES = frozenset({
    "Event", "CITO", "Mega-Event", "Giga-Event",
    "Community Celebration Event", "Geocaching HQ",
    "Geocaching HQ Celebration", "Geocaching HQ Block Party",
})


def _build_log_submit_context(cache, *, selected_log_type="", logged_at_value=None,
                               sequence_number_value=None, log_text_value=""):
    """Build log submission form context. Shared by cache_detail and bulk_logging."""
    from geocaches.sync.log_submit import cache_timezone
    from datetime import datetime as _dt, timezone as _tz
    from accounts.gc_client import has_api_tokens
    from accounts.keyring_util import get_oauth_token
    from accounts.models import UserAccount

    cache_tz = cache_timezone(cache.latitude, cache.longitude)

    if cache.cache_type in _LOG_SUBMIT_EVENT_TYPES:
        log_type_choices = [
            ("Will Attend", "Will Attend"), ("Attended", "Attended"), ("Write note", "Write note"),
        ]
    elif cache.cache_type == "Webcam":
        log_type_choices = [
            ("Webcam Photo Taken", "Webcam Photo Taken"),
            ("Didn't find it", "Didn't find it"), ("Write note", "Write note"),
        ]
    else:
        log_type_choices = [
            ("Found it", "Found it"), ("Didn't find it", "Didn't find it"),
            ("Write note", "Write note"), ("Needs Maintenance", "Needs Maintenance"),
        ]

    submit_platforms = []
    if cache.gc_code and not cache.al_code:
        submit_platforms.append({
            "id": "gc", "label": "geocaching.com",
            "enabled": has_api_tokens(),
            "checked": True,
        })
    if cache.oc_code:
        plat = getattr(cache, "oc_platform", "oc_de") or "oc_de"
        oc_acc = UserAccount.objects.filter(platform=plat).first()
        has_level3 = bool(get_oauth_token(plat, oc_acc.user_id)) if oc_acc else False
        submit_platforms.append({
            "id": plat, "label": f"opencaching ({plat.replace('oc_', '')})",
            "enabled": has_level3,
            "checked": True,
        })

    max_seq = (
        Log.objects.filter(is_local=True, sequence_number__isnull=False)
        .order_by("-sequence_number")
        .values_list("sequence_number", flat=True)
        .first()
    )

    # Insert buttons: unsubmitted field notes + all other notes with text
    from django.db.models import Q as _Q, F as _F
    from django.db.models.functions import Coalesce as _Coalesce
    insert_notes_qs = (
        cache.notes
        .filter(body__gt="")
        .filter(_Q(note_type="field_note", submitted_at__isnull=True) | ~_Q(note_type="field_note"))
        .annotate(_ref=_Coalesce("logged_at", "updated_at", "created_at"))
        .order_by(_F("_ref").desc(nulls_last=True))[:8]
    )
    pending_field_notes = []
    for fn in insert_notes_qs:
        is_field_note = fn.note_type == "field_note"
        ref_dt = fn.logged_at or fn.updated_at or fn.created_at
        local_dt = ref_dt.astimezone(cache_tz).strftime("%Y-%m-%dT%H:%M") if ref_dt and is_field_note else ""
        date_label = ref_dt.strftime("%Y-%m-%d") if ref_dt else ""
        pending_field_notes.append({
            "body": fn.body or "",
            "log_type": fn.log_type or "" if is_field_note else "",
            "local_dt": local_dt,
            "date_label": date_label,
            "label": "Insert field note" if is_field_note else "Insert note",
        })

    if logged_at_value is None:
        logged_at_value = _dt.now(_tz.utc).astimezone(cache_tz).strftime("%Y-%m-%dT%H:%M")
    if sequence_number_value is None:
        sequence_number_value = (max_seq + 1) if max_seq else None
    if not selected_log_type and log_type_choices:
        selected_log_type = log_type_choices[0][0]

    oc_ext = getattr(cache, "oc_extension", None)
    requires_passphrase = bool(getattr(oc_ext, "req_passwd", False))
    stored_passphrase = getattr(oc_ext, "passphrase", "") or ""

    # Favourite / recommendation eligibility
    gc_platform = next((p for p in submit_platforms if p["id"] == "gc"), None)
    oc_platform = next((p for p in submit_platforms if p["id"].startswith("oc_")), None)
    can_give_fav = bool(gc_platform and gc_platform.get("enabled"))
    can_recommend = bool(oc_platform and oc_platform.get("enabled"))
    user_favorited = cache.user_favorited
    user_recommended = getattr(oc_ext, "user_recommended", None)

    from preferences.models import UserPreference as _UP
    return {
        "log_type_choices": log_type_choices,
        "selected_log_type": selected_log_type,
        "cache_tz_name": str(cache_tz),
        "logged_at_value": logged_at_value,
        "sequence_number_value": sequence_number_value,
        "log_text_value": log_text_value,
        "submit_platforms": submit_platforms,
        "pending_field_notes": pending_field_notes,
        "requires_passphrase": requires_passphrase,
        "stored_passphrase": stored_passphrase,
        "log_image_strip_exif": _UP.get("log_image_strip_exif", True),
        "log_image_max_px": _UP.get("log_image_max_px", 1024),
        "can_give_fav": can_give_fav,
        "can_recommend": can_recommend,
        "user_favorited": user_favorited,
        "user_recommended": user_recommended,
    }


def cache_detail(request, gc_code):
    from preferences.models import UserPreference
    from geocaches.coords import format_coords

    cache = _get_cache(gc_code, Geocache.objects.select_related(
        "adventure", "oc_extension",
    ).prefetch_related(
        "waypoints", "notes", "custom_fields",
        "tags", "attributes", "images",
    ))
    hint_display = UserPreference.get("hint_display", "hidden")
    coord_format = UserPreference.get("coord_format", "dd")

    lat_str, lon_str = format_coords(cache.latitude, cache.longitude, coord_format)
    corr_lat_str = corr_lon_str = None
    if hasattr(cache, "corrected_coordinates") and cache.corrected_coordinates:
        corr_lat_str, corr_lon_str = format_coords(
            cache.corrected_coordinates.latitude,
            cache.corrected_coordinates.longitude,
            coord_format,
        )

    # For parent ALC (LC{base}): pass stages so they appear on map + in table.
    # Query Geocache.objects directly (not via reverse relation) to ensure no
    # scope/list filter from the list view can bleed into this independent query.
    stages = None
    if cache.adventure_id is not None and cache.stage_number is None:
        stages = list(
            Geocache.objects
            .filter(adventure_id=cache.adventure_id, stage_number__isnull=False)
            .order_by("stage_number")
        )

    from django.db.models import F
    from django.db.models.functions import Coalesce
    notes = list(
        cache.notes
        .annotate(ref_date=Coalesce("logged_at", "updated_at", "created_at"))
        .order_by(F("ref_date").desc(nulls_last=True))
    )

    gc_username = UserPreference.get("gc_username", "")
    log_truncate = UserPreference.get("log_truncate", True)
    log_truncate_length = UserPreference.get("log_truncate_length", 300)

    _OWNER_LOG_TYPES = {
        "Owner Maintenance", "Temporarily Disable Listing", "Enable Listing",
        "Update Coordinates", "Archive", "Permanently Archived", "Needs Archived", "Unarchive",
    }
    _REVIEWER_LOG_TYPES = {
        "Post Reviewer Note", "Publish Listing", "Retract Listing",
        "Submit For Review", "OC Team comment",
    }

    log_filter = request.GET.get("log_filter", "all")
    logs_qs = cache.logs.order_by("-logged_date")

    if log_filter == "my":
        from accounts.models import UserAccount
        from functools import reduce
        import operator

        # Build per-platform identity map from UserAccount records
        platform_map: dict[str, tuple[set, set]] = {}
        for acc in UserAccount.objects.all():
            if acc.platform not in platform_map:
                platform_map[acc.platform] = (set(), set())
            if acc.user_id:
                platform_map[acc.platform][0].add(acc.user_id)
            if acc.username:
                platform_map[acc.platform][1].add(acc.username)

        # Legacy gc_username preference as fallback
        if gc_username:
            if "gc" not in platform_map:
                platform_map["gc"] = (set(), set())
            platform_map["gc"][1].add(gc_username)

        # All known identities for blank-source logs (GSAK / legacy GC imports)
        all_ids = {uid for ids, _ in platform_map.values() for uid in ids}
        all_names = {n for _, names in platform_map.values() for n in names}

        my_q_parts = []
        for platform, (user_ids, usernames) in platform_map.items():
            sub_q = Q()
            if user_ids:
                sub_q |= Q(user_id__in=user_ids)
            for name in usernames:
                sub_q |= Q(user_name__iexact=name)
            if sub_q:
                my_q_parts.append(Q(source=platform) & sub_q)

        # Blank source: GSAK imports and legacy GC logs
        blank_sub_q = Q()
        if all_ids:
            blank_sub_q |= Q(user_id__in=all_ids)
        for name in all_names:
            blank_sub_q |= Q(user_name__iexact=name)
        if blank_sub_q:
            my_q_parts.append(Q(source="") & blank_sub_q)

        # Locally submitted logs are always mine regardless of username matching
        my_q_parts.append(Q(is_local=True))

        if my_q_parts:
            logs_qs = logs_qs.filter(reduce(operator.or_, my_q_parts))
        else:
            logs_qs = logs_qs.none()

    elif log_filter == "owner":
        # Base: owner-action log types — catches unambiguous owner actions on any platform
        owner_q = Q(log_type__in=_OWNER_LOG_TYPES)

        # Also match all logs by the GC owner identity (catches write notes etc.)
        gc_identity_q = Q()
        if cache.owner_gc_id:
            gc_identity_q |= Q(user_id=str(cache.owner_gc_id))
        if cache.owner:
            gc_identity_q |= Q(user_name=cache.owner)
        if gc_identity_q:
            owner_q |= Q(source__in=["gc", ""]) & gc_identity_q
            # OC-only cache: cache.owner is reliably the OC owner name
            if not cache.gc_code:
                _OC_SOURCES = ["oc_de", "oc_pl", "oc_uk", "oc_nl", "oc_us"]
                owner_q |= Q(source__in=_OC_SOURCES) & gc_identity_q

        logs_qs = logs_qs.filter(owner_q)

    elif log_filter == "reviewer":
        logs_qs = logs_qs.filter(log_type__in=_REVIEWER_LOG_TYPES)
    # "all" and "friends" (placeholder): no additional filter

    log_paginator = Paginator(logs_qs, 20)
    log_page_obj = log_paginator.get_page(request.GET.get("log_page", 1))

    # Determine if logs come from more than one source (exclude blank sources)
    log_sources = (
        cache.logs
        .exclude(source="")
        .exclude(source__isnull=True)
        .values_list("source", flat=True)
        .distinct()
    )
    multi_source_logs = log_sources.count() > 1

    # Session-based log fetch state for "fetch more" buttons
    log_skip = request.session.get(f"log_skip_{cache.pk}", 0)
    log_has_more = request.session.get(f"log_has_more_{cache.pk}", False)

    # --- Log submission context ---
    log_submit_ctx = _build_log_submit_context(cache)

    # De-fuse context (only for fused caches with both codes)
    defuse_available = bool(
        cache.gc_code and not cache.al_code and cache.oc_code
    )
    defuse_gc_ok = defuse_oc_ok = False
    defuse_has_corrected = defuse_has_notes = False
    if defuse_available:
        from accounts.gc_client import has_api_tokens as _has_api_tokens
        from accounts.keyring_util import get_oauth_token as _get_oauth_token
        from accounts.models import UserAccount as _UA
        defuse_gc_ok = _has_api_tokens()
        _oc_plat = cache.oc_platform or "oc_de"
        _oc_acc = _UA.objects.filter(platform=_oc_plat).first()
        defuse_oc_ok = bool(_get_oauth_token(_oc_plat, _oc_acc.user_id) if _oc_acc else None)
        defuse_has_corrected = hasattr(cache, "corrected_coordinates") and bool(
            cache.corrected_coordinates
        )
        defuse_has_notes = cache.notes.exists()

    hidden_waypoint_count = cache.waypoints.filter(is_hidden=True).count()
    _visible_coord_wps = list(cache.waypoints.filter(is_hidden=False, latitude__isnull=False))
    coord_waypoints = [
        {
            "id": wp.pk,
            "label": f"{wp.waypoint_type}: {wp.name or wp.lookup or '—'}",
            "lat": wp.latitude,
            "lon": wp.longitude,
        }
        for wp in _visible_coord_wps
    ]
    map_waypoints = [
        {"lat": wp.latitude, "lon": wp.longitude, "type": wp.waypoint_type, "name": wp.name or wp.lookup}
        for wp in _visible_coord_wps
    ]

    context = {
        "cache": cache,
        "log_page_obj": log_page_obj,
        "log_filter": log_filter,
        "multi_source_logs": multi_source_logs,
        "log_truncate": log_truncate,
        "log_truncate_length": log_truncate_length,
        "hint_display": hint_display,
        "coord_format": coord_format,
        "lat_str": lat_str,
        "lon_str": lon_str,
        "corr_lat_str": corr_lat_str,
        "corr_lon_str": corr_lon_str,
        "all_tags": Tag.objects.order_by("name"),
        "notes": notes,
        "stages": stages,
        "map_state": getattr(cache, "map_state", None),
        "log_skip": log_skip,
        "log_has_more": log_has_more,
        "embed": request.GET.get("embed") == "1",
        **log_submit_ctx,
        "hidden_waypoint_count": hidden_waypoint_count,
        "coord_waypoints": coord_waypoints,
        "map_waypoints": map_waypoints,
        # De-fuse
        "defuse_available": defuse_available,
        "defuse_gc_ok": defuse_gc_ok,
        "defuse_oc_ok": defuse_oc_ok,
        "defuse_has_corrected": defuse_has_corrected,
        "defuse_has_notes": defuse_has_notes,
    }
    response = render(request, "geocaches/detail.html", context)
    response["X-Frame-Options"] = "SAMEORIGIN"
    return response


def _parse_image_attachments(request, *, strip_exif_default: bool = True, max_px_default: int = 1024):
    """Extract image attachments from a multipart POST request.

    Looks for fields: image_file_N, image_title_N, image_desc_N,
    image_spoiler_N, image_rotate_N, image_max_px_N, image_strip_exif_N
    where N = 0, 1, 2, …
    """
    from geocaches.image_upload import ImageAttachment
    attachments = []
    i = 0
    while True:
        f = request.FILES.get(f"image_file_{i}")
        if f is None:
            break
        try:
            file_bytes = f.read()
            rotate = int(request.POST.get(f"image_rotate_{i}", "0") or "0")
            if rotate not in (0, 90, 180, 270):
                rotate = 0
            max_px_str = request.POST.get(f"image_max_px_{i}", "")
            max_px = int(max_px_str) if max_px_str.isdigit() else max_px_default
            strip_exif_val = request.POST.get(f"image_strip_exif_{i}", "")
            strip_exif = (strip_exif_val == "1") if strip_exif_val else strip_exif_default
            attachments.append(ImageAttachment(
                file_bytes=file_bytes,
                filename=f.name,
                title=request.POST.get(f"image_title_{i}", "").strip()[:100],
                description=request.POST.get(f"image_desc_{i}", "").strip()[:500],
                is_spoiler=request.POST.get(f"image_spoiler_{i}") == "1",
                rotate=rotate,
                max_dimension=max_px,
                strip_exif=strip_exif,
            ))
        except Exception:
            pass
        i += 1
    return attachments


def _parse_logged_at(s: str) -> "datetime | None":
    for fmt in ("%Y-%m-%dT%H:%M", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(s.strip(), fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def log_submit(request, gc_code):
    """Submit a new log for this cache to platform(s) and store locally."""
    from django.contrib import messages

    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    cache = _get_cache(gc_code)

    log_type = request.POST.get("log_type", "Found it")
    logged_at_str = request.POST.get("logged_at", "")
    text = request.POST.get("text", "")
    platforms = request.POST.getlist("platforms")
    seq = request.POST.get("sequence_number", "").strip()
    sequence_number = int(seq) if seq else None
    passphrase = request.POST.get("passphrase", "").strip()
    give_favourite = bool(request.POST.get("give_favourite"))
    recommend = bool(request.POST.get("recommend"))

    # Parse as naive datetime in cache timezone, convert to UTC
    from geocaches.sync.log_submit import submit_log, cache_timezone
    from zoneinfo import ZoneInfo

    cache_tz = cache_timezone(cache.latitude, cache.longitude)
    try:
        naive = datetime.strptime(logged_at_str, "%Y-%m-%dT%H:%M")
    except ValueError:
        messages.error(request, "Invalid date/time format.")
        return redirect("geocaches:detail", gc_code=cache.display_code)

    logged_at_utc = naive.replace(tzinfo=cache_tz).astimezone(timezone.utc)

    from preferences.models import UserPreference as _UP
    strip_exif_default = _UP.get("log_image_strip_exif", True)
    max_px_default = _UP.get("log_image_max_px", 1024)
    image_attachments = _parse_image_attachments(
        request, strip_exif_default=strip_exif_default, max_px_default=max_px_default
    )

    result = submit_log(cache, log_type, logged_at_utc, text, platforms,
                        sequence_number=sequence_number, passphrase=passphrase,
                        images=image_attachments,
                        give_favourite=give_favourite, recommend=recommend)

    if result.gc_success:
        messages.success(request, f"GC log submitted ({result.gc_ref_code})")
    elif result.gc_success is False:
        messages.error(request, f"GC log failed: {result.gc_error}")

    if result.oc_success:
        messages.success(request, f"OC log submitted ({result.oc_ref_code})")
    elif result.oc_success is False:
        messages.error(request, f"OC log failed: {result.oc_error}")

    for msg in result.messages:
        messages.info(request, msg)
    for err in result.image_errors:
        messages.warning(request, f"Image upload: {err}")

    return redirect("geocaches:detail", gc_code=cache.display_code)



def oc_passphrase_save(request, gc_code):
    """Save the passphrase for an OC cache that requires one."""
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    cache = _get_cache(gc_code)
    passphrase = request.POST.get("passphrase", "").strip()
    oc_ext = getattr(cache, "oc_extension", None)
    if oc_ext:
        oc_ext.passphrase = passphrase
        oc_ext.save(update_fields=["passphrase"])
    return redirect("geocaches:detail", gc_code=cache.display_code)


def note_add(request, gc_code):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    cache = _get_cache(gc_code)
    body = request.POST.get("body", "").strip()
    note_type = request.POST.get("note_type", "note")
    note_format = request.POST.get("format", "plain")
    logged_at = _parse_logged_at(request.POST.get("logged_at", ""))
    now = datetime.now(timezone.utc)
    # Don't create empty notes, unless it's a field note with a date
    if body or (note_type == "field_note" and logged_at):
        Note.objects.create(
            geocache=cache,
            note_type=note_type,
            format=note_format,
            body=body,
            logged_at=logged_at,
            created_at=now,
            updated_at=now,
        )
    return redirect("geocaches:detail", gc_code=gc_code)


def log_delete(request, log_id):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    log = get_object_or_404(Log, pk=log_id)
    gc_code = log.geocache.display_code
    log.delete()
    return redirect("geocaches:detail", gc_code=gc_code)


def note_update(request, note_id):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    note = get_object_or_404(Note, pk=note_id)
    note.body = request.POST.get("body", "").strip()
    note.note_type = request.POST.get("note_type", note.note_type)
    note.format = request.POST.get("format", note.format)
    note.logged_at = _parse_logged_at(request.POST.get("logged_at", ""))
    note.updated_at = datetime.now(timezone.utc)
    note.save(update_fields=["body", "note_type", "format", "logged_at", "updated_at"])
    return redirect("geocaches:detail", gc_code=note.geocache.display_code)


def note_delete(request, note_id):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    note = get_object_or_404(Note, pk=note_id)
    display_code = note.geocache.display_code
    note.delete()
    return redirect("geocaches:detail", gc_code=display_code)


def corrected_coords_save(request, gc_code):
    from geocaches.coords import parse_lat_lon

    cache = _get_cache(gc_code)

    if request.method == "POST":
        lat_str = request.POST.get("latitude", "").strip()
        lon_str = request.POST.get("longitude", "").strip()
        note = request.POST.get("note", "").strip()
        clear = request.POST.get("clear", "")

        if clear or (not lat_str and not lon_str):
            CorrectedCoordinates.objects.filter(geocache=cache).delete()
            if cache.has_corrected_coordinates:
                cache.has_corrected_coordinates = False
                cache.save(update_fields=["has_corrected_coordinates"])
        else:
            result = parse_lat_lon(lat_str, lon_str)
            if result:
                lat, lon = result
                CorrectedCoordinates.objects.update_or_create(
                    geocache=cache,
                    defaults={"latitude": lat, "longitude": lon, "note": note},
                )
                if not cache.has_corrected_coordinates:
                    cache.has_corrected_coordinates = True
                    cache.save(update_fields=["has_corrected_coordinates"])

    return redirect("geocaches:detail", gc_code=gc_code)


def save_map_state(request, gc_code):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    cache = _get_cache(gc_code)
    try:
        zoom = int(request.POST["zoom"])
        lat = float(request.POST["lat"])
        lon = float(request.POST["lon"])
    except (KeyError, ValueError):
        return HttpResponseBadRequest()
    CacheMapState.objects.update_or_create(
        geocache=cache,
        defaults={"zoom": zoom, "lat": lat, "lon": lon},
    )
    return HttpResponse(status=204)


def reset_map_state(request, gc_code):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    cache = _get_cache(gc_code)
    CacheMapState.objects.filter(geocache=cache).delete()
    return HttpResponse(status=204)


def set_as_reference_point(request, gc_code):
    """Create a ReferencePoint from this cache's (corrected) coordinates."""
    from preferences.models import ReferencePoint

    cache = _get_cache(gc_code)
    if request.method == "POST":
        name = request.POST.get("name", "").strip() or gc_code
        use_corrected = request.POST.get("use_corrected") == "1"

        if use_corrected and hasattr(cache, "corrected_coordinates") and cache.corrected_coordinates:
            lat = cache.corrected_coordinates.latitude
            lon = cache.corrected_coordinates.longitude
        else:
            lat = cache.latitude
            lon = cache.longitude

        ReferencePoint.objects.create(
            name=name,
            latitude=lat,
            longitude=lon,
            note=f"From cache {gc_code}",
        )
    return redirect("geocaches:detail", gc_code=gc_code)


def _import_tag_names(request):
    raw = request.POST.get("tags", "")
    return [t.strip() for t in raw.split(",") if t.strip()] or None


def _derive_wpts_path(gpx_path):
    """Derive the companion -wpts.gpx path from a main .gpx path, or None."""
    from pathlib import Path
    p = Path(gpx_path)
    if p.suffix.lower() != ".gpx":
        return None
    candidate = p.with_name(p.stem + "-wpts.gpx")
    return str(candidate) if candidate.exists() else None


def _is_wpts_file(filename):
    """Return True if the filename looks like a companion -wpts.gpx file."""
    return filename.lower().endswith("-wpts.gpx")


def _save_recent_import(pref_key, path_str, result):
    """Append a successful import to the recent-files list (max 10)."""
    from preferences.models import UserPreference
    recent = UserPreference.get(pref_key, [])
    summary = f"{result.created}+ {result.updated}~"
    entry = {
        "path": path_str,
        "date": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M"),
        "summary": summary,
    }
    # Remove duplicates of the same path
    recent = [r for r in recent if r.get("path") != path_str]
    recent.insert(0, entry)
    recent = recent[:10]
    UserPreference.set(pref_key, recent)


def _get_recent_imports(pref_key):
    from preferences.models import UserPreference
    return UserPreference.get(pref_key, [])


def _resolve_gpx_paths(path_str: str) -> list:
    """Resolve a path string into a list of GPX file paths.

    Handles: single file, semicolon-separated list, or folder path.
    Excludes -wpts.gpx companion files when expanding folders.
    """
    from pathlib import Path
    paths = []
    for part in path_str.split(";"):
        part = part.strip()
        if not part:
            continue
        p = Path(part)
        if p.is_dir():
            for f in sorted(p.glob("*.gpx")):
                if not _is_wpts_file(f.name):
                    paths.append(str(f))
            for f in sorted(p.glob("*.zip")):
                paths.append(str(f))
        elif p.exists():
            paths.append(str(p))
        else:
            paths.append(str(p))  # let import_and_enrich report the error
    return paths


def import_gpx(request):
    from geocaches.services import import_and_enrich
    from preferences.models import UserPreference

    results = []
    errors = []
    delete_after_import = UserPreference.get("delete_after_import", False)

    if request.method == "POST":
        tag_names = _import_tag_names(request)
        path_str = request.POST.get("gpx_path", "").strip()
        include_wpts = request.POST.get("include_wpts", "include") == "include"
        delete_after = "delete_after_import" in request.POST

        # Persist the delete preference
        if delete_after != delete_after_import:
            UserPreference.set("delete_after_import", delete_after)
            delete_after_import = delete_after

        if not path_str:
            errors.append("Please enter a file path.")
        else:
            file_paths = _resolve_gpx_paths(path_str)
            if not file_paths:
                errors.append("No GPX/ZIP files found.")

            for fp in file_paths:
                if _is_wpts_file(fp):
                    continue
                try:
                    _import_logger.info("--- GPX import start: %s", fp)
                    wpts_path = _derive_wpts_path(fp) if include_wpts else ""
                    result = import_and_enrich(
                        "unified_gpx", fp, tag_names, wpts_path=wpts_path,
                    )
                    if result:
                        _import_logger.info("--- GPX import done: %s", result)
                        for e in result.errors:
                            _import_logger.warning("GPX import error: %s", e)
                        _save_recent_import("recent_imports_gpx", fp, result)
                        results.append(result)

                        # Delete source file after successful import
                        if delete_after:
                            _delete_imported_file(fp, include_wpts)
                except Exception as exc:
                    errors.append(f"{fp}: {exc}")
                    _import_logger.error("GPX import failed: %s: %s", fp, exc)

    # Merge results for template display
    merged_result = _merge_import_results(results) if results else None

    if results:
        _check_duplicates_after_import(request)

    recent_files = _get_recent_imports("recent_imports_gpx")
    return render(request, "geocaches/import_gpx.html", {
        "result": merged_result,
        "errors": errors,
        "recent_files": recent_files,
        "delete_after_import": delete_after_import,
    })


def _delete_imported_file(file_path: str, include_wpts: bool = True):
    """Delete a GPX file and its companion -wpts.gpx after import."""
    from pathlib import Path
    p = Path(file_path)
    try:
        if p.exists():
            p.unlink()
            _import_logger.info("Deleted imported file: %s", file_path)
        if include_wpts:
            wpts = _derive_wpts_path(file_path)
            if wpts:
                wp = Path(wpts)
                if wp.exists():
                    wp.unlink()
                    _import_logger.info("Deleted companion wpts file: %s", wpts)
    except OSError as exc:
        _import_logger.warning("Failed to delete %s: %s", file_path, exc)


def _merge_import_results(results):
    """Merge multiple ImportResult objects into a summary."""
    if len(results) == 1:
        return results[0]

    # Create a simple summary object that looks like ImportResult
    class MergedResult:
        def __init__(self):
            self.created = 0
            self.updated = 0
            self.locked = 0
            self.skipped = 0
            self.errors = []
            self.file_count = 0

    merged = MergedResult()
    for r in results:
        merged.created += getattr(r, "created", 0)
        merged.updated += getattr(r, "updated", 0)
        merged.locked += getattr(r, "locked", 0)
        merged.skipped += getattr(r, "skipped", 0)
        merged.errors.extend(getattr(r, "errors", []))
        merged.file_count += 1
    return merged


def _check_duplicates_after_import(request):
    """Quick scan for potential GC/OC duplicates; adds a Django message if found."""
    from django.contrib import messages
    from geocaches.services import find_potential_duplicates
    try:
        dupes = find_potential_duplicates()
        if dupes:
            from django.utils.safestring import mark_safe
            url = '/tools/duplicate-caches/'
            messages.info(request, mark_safe(
                f'{len(dupes)} potential duplicate GC/OC cache(es) detected. '
                f'<a href="{url}">Review and merge in Tools</a>.'
            ))
    except Exception:
        pass  # never fail the import over a dedup scan


def detect_gpx_format_ajax(request):
    """AJAX endpoint to detect GPX file format from a path."""
    from geocaches.importers import detect_gpx_format
    path_str = request.GET.get("path", "").strip()
    if not path_str:
        return HttpResponseBadRequest(json.dumps({"error": "No path"}), content_type="application/json")
    fmt = detect_gpx_format(path_str)
    return HttpResponse(json.dumps({"format": fmt}), content_type="application/json")


def import_gsak(request):
    from pathlib import Path
    from geocaches.services import import_and_enrich

    GSAK_DATA_DIR = Path.home() / "AppData/Roaming/gsak/data"
    gsak_dbs = []
    if GSAK_DATA_DIR.exists():
        gsak_dbs = sorted(
            p for p in GSAK_DATA_DIR.iterdir()
            if p.is_dir() and (p / "sqlite.db3").exists()
        )

    result = None
    errors = []
    db_path = ""

    if request.method == "POST":
        tag_names = _import_tag_names(request)
        db_path = request.POST.get("gsak_path", "").strip()
        if not db_path:
            db_path = request.POST.get("gsak_custom_path", "").strip()
        try:
            if db_path:
                _import_logger.info("--- GSAK import start: %s", db_path)
                result = import_and_enrich("gsak", db_path, tag_names)
            else:
                errors.append("Please select or enter a database path.")
        except Exception as exc:
            errors.append(str(exc))
        if result:
            _import_logger.info("--- GSAK import done: %s", result)
            for e in result.errors:
                _import_logger.warning("GSAK import error: %s", e)
        for e in errors:
            _import_logger.error("GSAK import failed: %s", e)

    if result:
        _check_duplicates_after_import(request)

    db_name = Path(db_path).parent.name if db_path else None
    return render(request, "geocaches/import_gsak.html", {
        "gsak_dbs": gsak_dbs,
        "result": result,
        "errors": errors,
        "db_name": db_name,
    })


def import_lab2gpx(request):
    from geocaches.services import import_and_enrich

    result = None
    errors = []

    if request.method == "POST":
        tag_names = _import_tag_names(request)
        path_str = request.POST.get("lab2gpx_path", "").strip()
        try:
            if not path_str:
                errors.append("Please enter a file path.")
            else:
                _import_logger.info("--- lab2gpx import start: %s", path_str)
                result = import_and_enrich("lab2gpx", path_str, tag_names)
        except Exception as exc:
            errors.append(str(exc))
        if result:
            _import_logger.info("--- lab2gpx import done: %s", result)
            for e in result.errors:
                _import_logger.warning("lab2gpx import error: %s", e)
            if path_str:
                _save_recent_import("recent_imports_lab2gpx", path_str, result)
        for e in errors:
            _import_logger.error("lab2gpx import failed: %s", e)

    if result:
        _check_duplicates_after_import(request)

    recent_files = _get_recent_imports("recent_imports_lab2gpx")
    return render(request, "geocaches/import_lab2gpx.html", {
        "result": result, "errors": errors, "recent_files": recent_files,
    })


_fieldnote_logger = logging.getLogger("geocaches.fieldnote")


def import_fieldnotes(request):
    """Import field notes from a file or download from GC.com."""
    import json as _json
    from django.contrib import messages
    from geocaches.importers.fieldnote import (
        import_fieldnote_file, analyze_fieldnote_file,
        download_gc_fieldnotes, _fieldnotes_dir,
    )
    from pathlib import Path

    result = None
    errors = []
    pending_file_path = ""   # file waiting for user decision (not yet imported)
    action = request.POST.get("action", "import") if request.method == "POST" else ""

    if request.method == "POST":
        if action == "download_gc":
            try:
                saved_path = download_gc_fieldnotes()
                messages.success(request, f"Downloaded GC field notes → {saved_path.name}")
                result = analyze_fieldnote_file(saved_path)
                if result.not_found_entries:
                    pending_file_path = str(saved_path)
                else:
                    result = import_fieldnote_file(saved_path)
            except Exception as exc:
                errors.append(f"GC download failed: {exc}")
                _fieldnote_logger.error("GC field note download failed: %s", exc)

        elif action in ("import", "reimport"):
            path_str = request.POST.get("fieldnote_path", "").strip()
            if not path_str:
                errors.append("Please enter a file path.")
            else:
                fp = Path(path_str)
                if not fp.is_file():
                    errors.append(f"File not found: {path_str}")
                else:
                    # If the file is not already in the fieldnotes folder, copy it there first
                    fn_dir = _fieldnotes_dir()
                    fn_dir.mkdir(parents=True, exist_ok=True)
                    if fp.parent.resolve() != fn_dir.resolve():
                        import shutil as _shutil
                        dest = fn_dir / fp.name
                        if dest.exists():
                            from datetime import datetime as _dt
                            dest = fn_dir / f"{fp.stem}_{_dt.now().strftime('%Y%m%d_%H%M%S')}{fp.suffix}"
                        _shutil.copy2(str(fp), str(dest))
                        fp = dest
                    result = analyze_fieldnote_file(fp)
                    if result.not_found_entries:
                        pending_file_path = str(fp)
                    else:
                        result = import_fieldnote_file(fp)

        elif action == "import_skip_missing":
            path_str = request.POST.get("fieldnote_path", "").strip()
            fp = Path(path_str)
            if fp.is_file():
                result = import_fieldnote_file(fp, mode="skip_missing")
            else:
                errors.append(f"File not found: {path_str}")

        elif action == "import_with_placeholders":
            path_str = request.POST.get("fieldnote_path", "").strip()
            fp = Path(path_str)
            if fp.is_file():
                result = import_fieldnote_file(fp, mode="import_all")
            else:
                errors.append(f"File not found: {path_str}")

    # List unprocessed files for the "recent" panel
    fn_dir = _fieldnotes_dir()
    pending_files = sorted(fn_dir.glob("*.txt"), reverse=True)[:10]

    # Build per-platform code grouping for the "Fetch Caches" button
    not_found_by_platform: dict = {}
    if result and result.not_found_entries:
        for entry in result.not_found_entries:
            plat = entry.platform
            not_found_by_platform.setdefault(plat, []).append(entry.cache_code)

    redirect_to_bulk = (
        result is not None
        and not errors
        and not result.not_found_entries
        and not pending_file_path
    )

    return render(request, "geocaches/import_fieldnotes.html", {
        "result": result,
        "errors": errors,
        "pending_files": [str(p) for p in pending_files],
        "redirect_to_bulk": redirect_to_bulk,
        "pending_file_path": pending_file_path,
        "not_found_by_platform_json": _json.dumps(not_found_by_platform),
    })


_bulk_logging_logger = logging.getLogger("geocaches.bulk_logging")


def bulk_logging(request):
    """Bulk logging UI — review and submit pending field notes as logs."""
    from django.contrib import messages
    from django.db.models import Max
    from datetime import datetime as _dt, timezone as _tz
    from geocaches.sync.log_submit import submit_log, cache_timezone

    def _note_status(note):
        if note.submitted_at:
            return "logged"
        if note.submit_error:
            return "error"
        if note.bulk_draft:
            return "draft"
        return "new"

    # Only show field notes that were imported via the field note importer
    # (they always have log_type set); GSAK-imported notes have empty log_type
    pending_notes_qs = (
        Note.objects.filter(
            note_type="field_note", submitted_at__isnull=True, log_type__gt="", bulk_dismissed=False
        )
        .select_related("geocache", "geocache__oc_extension")
        .order_by("logged_at")
    )
    done_notes_qs = (
        Note.objects.filter(note_type="field_note", submitted_at__isnull=False, log_type__gt="")
        .select_related("geocache", "geocache__oc_extension")
        .order_by("-submitted_at")[:50]
    )
    pending_notes = list(pending_notes_qs)
    done_notes = list(done_notes_qs)

    # Assign sequence numbers: steps of 5 from highest existing, preserve stored overrides
    max_seq_log = (
        Log.objects.filter(is_local=True, sequence_number__isnull=False)
        .aggregate(Max("sequence_number"))["sequence_number__max"] or 0
    )
    max_seq_note = max((n.sequence_number for n in pending_notes if n.sequence_number), default=0)
    base_seq = max(max_seq_log, max_seq_note)
    auto_i = 1
    for note in pending_notes:
        if not note.sequence_number:
            note.auto_seq = base_seq + auto_i * 5
            auto_i += 1
        else:
            note.auto_seq = note.sequence_number

    for note in pending_notes:
        note.status = _note_status(note)
    for note in done_notes:
        note.status = "logged"

    # Handle POST
    if request.method == "POST":
        action = request.POST.get("action", "")
        note_id = request.POST.get("note_id", "")
        note = Note.objects.filter(pk=note_id, note_type="field_note").select_related("geocache").first()
        if note is None:
            messages.error(request, "Field note not found.")
            return redirect("geocaches:bulk_logging")

        def _next_url():
            ids = [n.pk for n in pending_notes]
            try:
                cur_idx = ids.index(note.pk)
            except ValueError:
                cur_idx = -1
            # Prefer the note immediately after current; fall back to the one before
            for n in pending_notes[cur_idx + 1:]:
                if n.pk != note.pk:
                    return f"{request.path}?note={n.pk}"
            for n in reversed(pending_notes[:cur_idx]):
                if n.pk != note.pk:
                    return f"{request.path}?note={n.pk}"
            return request.path

        if action == "delete":
            note.bulk_dismissed = True
            note.save(update_fields=["bulk_dismissed"])
            return redirect(_next_url())

        log_type = request.POST.get("log_type", note.log_type or "Found it")
        text = request.POST.get("text", "")
        logged_at_str = request.POST.get("logged_at", "")
        seq_str = request.POST.get("sequence_number", "").strip()
        sequence_number = int(seq_str) if seq_str else None
        platforms = request.POST.getlist("platforms")
        passphrase = request.POST.get("passphrase", "").strip()
        give_favourite = bool(request.POST.get("give_favourite"))
        recommend = bool(request.POST.get("recommend"))

        cache_tz_obj = cache_timezone(note.geocache.latitude, note.geocache.longitude)
        try:
            naive = _dt.strptime(logged_at_str, "%Y-%m-%dT%H:%M")
            logged_at_utc = naive.replace(tzinfo=cache_tz_obj).astimezone(_tz.utc)
        except ValueError:
            logged_at_utc = note.logged_at

        if action == "save_draft":
            note.log_type = log_type
            note.draft_body = text
            note.logged_at = logged_at_utc
            note.sequence_number = sequence_number
            note.bulk_draft = True
            note.submit_error = ""
            note.save(update_fields=["log_type", "draft_body", "logged_at", "sequence_number", "bulk_draft", "submit_error"])
            return redirect(_next_url())

        if action == "submit_now":
            from preferences.models import UserPreference as _UP
            _strip_exif = _UP.get("log_image_strip_exif", True)
            _max_px = _UP.get("log_image_max_px", 1024)
            image_attachments = _parse_image_attachments(
                request, strip_exif_default=_strip_exif, max_px_default=_max_px
            )
            result = submit_log(
                note.geocache, log_type, logged_at_utc, text, platforms,
                sequence_number=sequence_number, passphrase=passphrase,
                images=image_attachments,
                give_favourite=give_favourite, recommend=recommend,
            )
            errors = []
            if result.gc_success is False:
                errors.append(f"GC: {result.gc_error}")
            if result.oc_success is False:
                errors.append(f"OC: {result.oc_error}")
            errors.extend(result.image_errors)
            if errors and (result.gc_success is False or result.oc_success is False):
                note.submit_error = "; ".join(e for e in errors if not e.startswith("GC image") and not e.startswith("OC image"))
                if result.image_errors:
                    note.submit_error = (note.submit_error + " | Images: " + "; ".join(result.image_errors)).strip(" |")
                note.save(update_fields=["submit_error"])
                _bulk_logging_logger.warning("Bulk log submit error for note %s: %s", note.pk, note.submit_error)
            else:
                note.submitted_at = _dt.now(_tz.utc)
                note.bulk_draft = False
                note.submit_error = " | ".join(result.image_errors) if result.image_errors else ""
                note.log_type = log_type
                note.logged_at = logged_at_utc
                note.sequence_number = sequence_number
                note.draft_body = ""
                note.save(update_fields=["submitted_at", "bulk_draft", "submit_error", "log_type", "logged_at", "sequence_number", "draft_body"])
            return redirect(_next_url())

        return redirect(f"{request.path}?note={note_id}")

    # Build editor context for selected note
    selected_id = request.GET.get("note") or (str(pending_notes[0].pk) if pending_notes else None)
    selected_note = None
    if selected_id:
        selected_note = next((n for n in pending_notes if str(n.pk) == selected_id), None)
        if selected_note is None:
            selected_note = next((n for n in done_notes if str(n.pk) == selected_id), None)

    editor_ctx: dict = {}
    if selected_note and selected_note.geocache:
        cache_tz_obj = cache_timezone(selected_note.geocache.latitude, selected_note.geocache.longitude)
        logged_at_value = (
            selected_note.logged_at.astimezone(cache_tz_obj).strftime("%Y-%m-%dT%H:%M")
            if selected_note.logged_at else ""
        )
        seq_val = getattr(selected_note, "auto_seq", None) or selected_note.sequence_number
        editor_ctx = _build_log_submit_context(
            selected_note.geocache,
            selected_log_type=selected_note.log_type or "",
            logged_at_value=logged_at_value,
            sequence_number_value=seq_val,
            log_text_value=selected_note.draft_body or selected_note.body or "",
        )

    active_tab = request.GET.get("tab", "pending")

    return render(request, "geocaches/bulk_logging.html", {
        "pending_notes": pending_notes,
        "done_notes": done_notes,
        "selected_note": selected_note,
        "active_tab": active_tab,
        **editor_ctx,
    })


def tools_remove_zero_waypoints(request):
    """Delete all waypoints where lat=0 and lon=0."""
    from geocaches.models import Waypoint
    qs = Waypoint.objects.filter(latitude=0.0, longitude=0.0)
    count = qs.count()
    if request.method == "POST":
        qs.delete()
        return render(request, "geocaches/tools_result.html", {
            "title": "Remove 0,0 waypoints",
            "message": f"Deleted {count} waypoint{'s' if count != 1 else ''} with coordinates 0°/0°.",
        })
    return render(request, "geocaches/tools_confirm.html", {
        "title": "Remove 0,0 waypoints",
        "description": f"This will permanently delete {count} waypoint{'s' if count != 1 else ''} "
                       f"where latitude and longitude are both 0°. These are placeholder waypoints "
                       f"that break map display and are exported to external devices without need.",
        "action_url": request.path,
        "submit_label": "Delete waypoints",
    })


_OC_PREFIX_TO_PLATFORM = {
    "OC": "oc_de",
    "OP": "oc_pl",
    "OU": "oc_us",
    "OB": "oc_nl",
    "OK": "oc_uk",
    "OR": "oc_ro",
}


def cache_toggle_lock(request, gc_code):
    """Toggle import_locked on a single cache. POST only."""
    from django.contrib import messages

    if request.method != "POST":
        return redirect("geocaches:detail", gc_code=gc_code)

    cache = _get_cache(gc_code)
    cache.import_locked = not cache.import_locked
    cache.save(update_fields=["import_locked"])
    if cache.import_locked:
        messages.success(request, f"{cache.display_code} is now import-locked.")
    else:
        messages.success(request, f"{cache.display_code} is now unlocked.")
    return redirect("geocaches:detail", gc_code=cache.display_code)


def cache_fetch_logs(request, gc_code):
    """Fetch logs for a single cache from GC or OC. POST only."""
    from django.contrib import messages

    if request.method != "POST":
        return redirect("geocaches:detail", gc_code=gc_code)

    cache = _get_cache(gc_code)

    if cache.import_locked:
        messages.warning(request, f"{cache.display_code} is import-locked. Unlock it first before fetching logs.")
        return redirect("geocaches:detail", gc_code=cache.display_code)

    source = request.POST.get("source", "")
    action = request.POST.get("action", "fetch_recent")
    saved = 0
    api_count = 0
    skip_used = 0
    batch_size = 50

    try:
        if source == "gc" and cache.gc_code:
            from geocaches.sync.gc_client import GCClient
            from geocaches.sync.log_fetch import (
                fetch_recent_gc_logs, fetch_more_gc_logs, fetch_all_gc_logs,
            )
            client = GCClient()
            if action == "fetch_recent":
                saved, api_count = fetch_recent_gc_logs(client, cache.gc_code, count=batch_size)
                skip_used = batch_size
            elif action == "fetch_more":
                skip_used = int(request.POST.get("skip", 0))
                saved, api_count = fetch_more_gc_logs(client, cache.gc_code, skip=skip_used, count=batch_size)
                skip_used += batch_size
            elif action == "fetch_all":
                saved = fetch_all_gc_logs(client, cache.gc_code)
                api_count = 0  # exhausted
        elif source.startswith("oc") and cache.oc_code:
            from geocaches.sync.oc_client import OCClient
            from geocaches.sync.log_fetch import fetch_oc_logs
            from accounts.models import UserAccount
            count = int(request.POST.get("count", 50))
            acct = UserAccount.objects.filter(platform=source).first()
            user_id = acct.user_id if acct else ""
            client = OCClient(platform=source, user_id=user_id)
            saved = fetch_oc_logs(client, cache.oc_code, count=count)
        else:
            messages.error(request, f"Unknown source: {source}")
            return redirect("geocaches:detail", gc_code=cache.display_code)
    except Exception as exc:
        messages.error(request, f"Log fetch failed: {exc}")
        return redirect("geocaches:detail", gc_code=cache.display_code)

    # Update local log count after fetching
    if saved:
        actual_count = cache.logs.count()
        if actual_count != cache.platform_log_count:
            cache.platform_log_count = actual_count
            cache.save(update_fields=["platform_log_count"])
        messages.success(request, f"Fetched {saved} new log(s) from {source.upper()}")
    else:
        messages.info(request, "No new logs found.")

    # Build redirect URL with skip state for "fetch more" flow
    redir_url = f"{cache.display_code}#logs"
    if source == "gc" and action in ("fetch_recent", "fetch_more") and api_count >= batch_size:
        request.session[f"log_skip_{cache.pk}"] = skip_used
        request.session[f"log_has_more_{cache.pk}"] = True
    elif source == "gc" and action in ("fetch_recent", "fetch_more", "fetch_all"):
        request.session.pop(f"log_skip_{cache.pk}", None)
        request.session[f"log_has_more_{cache.pk}"] = False

    return redirect("geocaches:detail", gc_code=cache.display_code)


def cache_refresh(request, gc_code):
    """Re-fetch a single cache from its API source (GC or OC). POST only."""
    from django.contrib import messages
    from geocaches.services import save_geocache

    if request.method != "POST":
        return redirect("geocaches:detail", gc_code=gc_code)

    cache = _get_cache(gc_code)

    if cache.import_locked:
        messages.warning(request, f"{cache.display_code} is import-locked. Unlock it first before refreshing.")
        return redirect("geocaches:detail", gc_code=cache.display_code)

    source = request.POST.get("source", "")  # "gc" or "oc_de", etc.
    errors = []

    if source == "gc" and cache.gc_code and not cache.al_code:
        try:
            from geocaches.sync.gc_client import GCClient
            from geocaches.sync.base import SyncMode
            client = GCClient()
            data = client.get_cache(cache.gc_code, SyncMode.FULL, log_count=5)
            kwargs = dict(data)
            kwargs["fields"] = dict(data["fields"])
            save_geocache(**kwargs)
            # Ensure user's own logs are present (pages through if needed)
            from geocaches.sync.log_fetch import ensure_my_gc_logs
            ensure_my_gc_logs(client, cache.gc_code)
        except Exception as exc:
            errors.append(f"GC refresh failed: {exc}")
    elif source.startswith("oc") and cache.oc_code:
        try:
            from geocaches.sync.oc_client import OCClient
            from geocaches.sync.base import SyncMode
            from accounts.models import UserAccount
            acct = UserAccount.objects.filter(platform=source).first()
            user_id = acct.user_id if acct else ""
            client = OCClient(platform=source, user_id=user_id)
            data = client.get_cache(cache.oc_code, SyncMode.FULL)
            kwargs = dict(data)
            kwargs["fields"] = dict(data["fields"])
            save_geocache(**kwargs)
        except Exception as exc:
            errors.append(f"OC refresh failed: {exc}")
    else:
        errors.append(f"Unknown source: {source}")

    if errors:
        messages.error(request, errors[0])
    else:
        messages.success(request, f"Refreshed {cache.display_code} from {source.upper()}")

    return redirect("geocaches:detail", gc_code=cache.display_code)


_defuse_logger = logging.getLogger("geocaches.defuse")


def cache_defuse(request, gc_code):
    """De-fuse a fused GC+OC cache back into two independent records. POST only."""
    from django.contrib import messages

    if request.method != "POST":
        return redirect("geocaches:detail", gc_code=gc_code)

    cache = _get_cache(gc_code)

    if not cache.gc_code or not cache.oc_code:
        messages.error(request, "Cache is not fused (needs both GC and OC codes).")
        return redirect("geocaches:detail", gc_code=gc_code)

    oc_code = cache.oc_code
    oc_platform = cache.oc_platform or "oc_de"

    # 1. Check API access for both platforms
    from accounts.gc_client import has_api_tokens
    from accounts.keyring_util import get_oauth_token
    from accounts.models import UserAccount

    if not has_api_tokens():
        messages.error(request, "No GC API tokens — cannot de-fuse without GC API access.")
        return redirect("geocaches:detail", gc_code=gc_code)

    oc_acc = UserAccount.objects.filter(platform=oc_platform).first()
    oc_tokens = get_oauth_token(oc_platform, oc_acc.user_id) if oc_acc else None
    if not oc_tokens:
        messages.error(request, f"No OC OAuth tokens for {oc_platform} — cannot de-fuse without OC API access.")
        return redirect("geocaches:detail", gc_code=gc_code)

    # 2. Strip OC data from the GC cache record
    oc_logs_deleted, _ = cache.logs.filter(source__startswith="oc_").delete()

    try:
        cache.oc_extension.delete()
    except Exception:
        pass

    cache.attributes.clear()  # re-fetched from both APIs below
    cache.oc_code = ""
    cache.save(update_fields=["oc_code"])

    _defuse_logger.info(
        "De-fused %s: removed OC code %s, deleted %d OC logs",
        cache.gc_code, oc_code, oc_logs_deleted,
    )

    # 3. Re-import OC cache as a fresh standalone record
    oc_import_error = None
    try:
        from geocaches.sync.oc_client import OCClient
        from geocaches.sync.base import SyncMode
        from geocaches.services import save_geocache

        oc_client = OCClient(platform=oc_platform, user_id=oc_acc.user_id if oc_acc else "")
        oc_data = oc_client.get_cache(oc_code, SyncMode.FULL)
        oc_kwargs = dict(oc_data)
        oc_kwargs["fields"] = dict(oc_data["fields"])
        save_geocache(**oc_kwargs)
    except Exception as exc:
        oc_import_error = str(exc)
        _defuse_logger.error("De-fuse: OC re-import failed for %s: %s", oc_code, exc)

    # 4. Full GC refresh to restore GC-side attributes and data
    gc_refresh_error = None
    try:
        from geocaches.sync.gc_client import GCClient
        from geocaches.sync.base import SyncMode
        from geocaches.sync.log_fetch import ensure_my_gc_logs
        from geocaches.services import save_geocache

        gc_client = GCClient()
        gc_data = gc_client.get_cache(cache.gc_code, SyncMode.FULL, log_count=5)
        gc_kwargs = dict(gc_data)
        gc_kwargs["fields"] = dict(gc_data["fields"])
        save_geocache(**gc_kwargs)
        ensure_my_gc_logs(gc_client, cache.gc_code)
    except Exception as exc:
        gc_refresh_error = str(exc)
        _defuse_logger.error("De-fuse: GC refresh failed for %s: %s", cache.gc_code, exc)

    # 5. Write a note so the user can evaluate both records
    note_parts = [f"De-fused: {cache.gc_code} and {oc_code} separated into independent records."]
    if oc_import_error:
        note_parts.append(f"OC re-import failed: {oc_import_error}")
    if gc_refresh_error:
        note_parts.append(f"GC refresh failed: {gc_refresh_error}")

    Note.objects.create(
        geocache=cache,
        note_type="note",
        format="plain",
        body="\n".join(note_parts),
    )
    _defuse_logger.info("De-fuse complete: %s / %s", cache.gc_code, oc_code)

    # Record the user's decision so the pair won't be auto-suggested for fusion again
    from geocaches.services import set_fusion_decision
    set_fusion_decision(cache.gc_code, oc_code, "dont_fuse")

    if oc_import_error or gc_refresh_error:
        messages.warning(request, f"De-fused {cache.gc_code} / {oc_code} with errors — check notes.")
    else:
        messages.success(request, f"De-fused: {cache.gc_code} and {oc_code} are now separate records.")

    return redirect("geocaches:detail", gc_code=cache.display_code)


def cache_delete(request, gc_code):
    """Delete a single cache — POST only, redirects to list on success."""
    cache = _get_cache(gc_code)
    if request.method == "POST":
        cache.delete()
        next_url = request.POST.get("next", "")
        return redirect(next_url if next_url.startswith("/") else "geocaches:list")
    return redirect("geocaches:detail", gc_code=gc_code)


def cache_delete_filtered(request):
    qs, _ = _filtered_qs(request)
    count = qs.count()

    if request.method == "POST":
        from geocaches.delete_task import start_deletion
        pk_list = list(qs.values_list("pk", flat=True))
        start_deletion(pk_list)
        return redirect("geocaches:delete_progress")

    return render(request, "geocaches/delete_filtered.html", {
        "count": count,
        "query_string": request.GET.urlencode(),
    })


def cache_delete_progress(request):
    from geocaches.delete_task import get_status
    status = get_status()
    auto_refresh = status["running"]
    return render(request, "geocaches/delete_progress.html", {
        "status": status,
        "auto_refresh": auto_refresh,
    })


def cache_enrich(request):
    """Start background enrichment for the filtered cache set and redirect back."""
    from urllib.parse import parse_qs, urlencode as _urlencode
    from django.urls import reverse
    from geocaches.enrich_task import start_enrichment

    qs, _ = _filtered_qs(request)

    fields_param = request.GET.get("fields", "all")
    if fields_param == "elevation":
        fields, overwrite = {"elevation"}, set()
    elif fields_param == "elevation_update":
        fields, overwrite = {"elevation"}, {"elevation"}
    elif fields_param == "elevation_hires":
        fields, overwrite = {"elevation"}, {"elevation_hires"}
    elif fields_param == "location":
        fields, overwrite = {"location"}, set()
    elif fields_param == "location_update":
        fields, overwrite = {"location"}, {"location"}
    else:
        fields, overwrite = {"elevation", "location"}, set()

    started = start_enrichment(qs, fields, overwrite)

    # Redirect back to list, stripping the 'fields' trigger param
    params = parse_qs(request.GET.urlencode(), keep_blank_values=True)
    params.pop("fields", None)
    qs_str = _urlencode(params, doseq=True)
    list_url = reverse("geocaches:list")
    list_target = f"{list_url}?{qs_str}" if qs_str else list_url

    if not started:
        from django.contrib import messages
        messages.warning(request, "Enrichment is already running.")
    return redirect(list_target)


def enrich_status(request):
    """Return current enrichment status — HTML partial for HTMX, JSON otherwise."""
    from django.http import JsonResponse
    from geocaches.enrich_task import get_status
    status = get_status()
    if request.headers.get("HX-Request"):
        return render(request, "geocaches/_enrich_progress.html", status)
    return JsonResponse(status)


def enrich_cancel(request):
    """Cancel a running enrichment and redirect back."""
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    from geocaches.enrich_task import cancel_enrichment
    cancel_enrichment()
    return redirect(request.META.get("HTTP_REFERER", "/"))


def cache_update(request):
    """Start a bulk API update for the filtered cache set and redirect back."""
    from urllib.parse import parse_qs, urlencode as _urlencode
    from django.urls import reverse
    from django.contrib import messages
    from geocaches.update_task import start_update

    qs, _ = _filtered_qs(request)

    action = request.GET.get("action", "")
    kwargs = {}
    if action == "oc_logs":
        kwargs["count"] = int(request.GET.get("count", 50))

    started = start_update(qs, action, **kwargs)

    params = parse_qs(request.GET.urlencode(), keep_blank_values=True)
    params.pop("action", None)
    params.pop("count", None)
    qs_str = _urlencode(params, doseq=True)
    list_url = reverse("geocaches:list")
    list_target = f"{list_url}?{qs_str}" if qs_str else list_url

    if not started:
        messages.warning(request, "An update task is already running.")
    return redirect(list_target)


def update_status(request):
    """Return current update task status — HTML partial for HTMX, JSON otherwise."""
    from django.http import JsonResponse
    from geocaches.update_task import get_status
    status = get_status()
    if request.headers.get("HX-Request"):
        return render(request, "geocaches/_update_progress.html", status)
    return JsonResponse(status)


def update_cancel(request):
    """Cancel a running update task and redirect back."""
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    from geocaches.update_task import cancel_update
    cancel_update()
    return redirect(request.META.get("HTTP_REFERER", "/"))


def tag_management(request):
    from geocaches.models import Tag
    from geocaches.services import manage_tags
    from django.db.models import Count

    if request.method == "POST":
        action = request.POST.get("_action")
        tag_id = request.POST.get("tag_id")
        new_name = request.POST.get("new_name", "").strip()
        rp_id = request.POST.get("rp_id", "").strip()
        manage_tags(action, tag_id=tag_id, new_name=new_name, rp_id=rp_id)
        return redirect("geocaches:tags")

    from preferences.models import ReferencePoint
    tags = Tag.objects.annotate(cache_count=Count("geocaches")).select_related("default_ref_point").order_by("name")
    return render(request, "geocaches/tags.html", {
        "tags": tags,
        "ref_points": ReferencePoint.objects.order_by("name"),
    })


def tags_json(request):
    from geocaches.models import Tag
    names = list(Tag.objects.order_by("name").values_list("name", flat=True))
    return JsonResponse(names, safe=False)


def bulk_tag_add(request):
    from geocaches.services import manage_tags

    qs, _ = _filtered_qs(request)
    query_string = request.GET.urlencode()

    if request.method == "POST":
        name = request.POST.get("tag_name", "").strip()
        if name:
            manage_tags("bulk_add", tag_name=name, queryset=qs)
        from django.urls import reverse
        from urllib.parse import parse_qs, urlencode as _urlencode
        params = parse_qs(query_string, keep_blank_values=True)
        params.pop("tag", None)
        qs_str = _urlencode(params, doseq=True)
        return redirect(f"{reverse('geocaches:list')}?{qs_str}")

    count = qs.count()
    from geocaches.models import Tag
    existing_tags = Tag.objects.order_by("name")
    return render(request, "geocaches/bulk_tag_add.html", {
        "count": count,
        "existing_tags": existing_tags,
        "query_string": query_string,
    })


def bulk_tag_remove(request):
    from geocaches.models import Tag
    from geocaches.services import manage_tags

    qs, _ = _filtered_qs(request)
    query_string = request.GET.urlencode()

    if request.method == "POST":
        tag_id = request.POST.get("tag_id", "").strip()
        if tag_id:
            manage_tags("bulk_remove", tag_id=tag_id, queryset=qs)
        from django.urls import reverse
        from urllib.parse import parse_qs, urlencode as _urlencode
        params = parse_qs(query_string, keep_blank_values=True)
        params.pop("tag", None)
        qs_str = _urlencode(params, doseq=True)
        return redirect(f"{reverse('geocaches:list')}?{qs_str}")

    count = qs.count()
    # Only show tags that actually appear on caches in the current filter
    tags_on_filtered = (
        Tag.objects.filter(geocaches__in=qs).distinct().order_by("name")
    )
    return render(request, "geocaches/bulk_tag_remove.html", {
        "count": count,
        "tags": tags_on_filtered,
        "query_string": query_string,
    })


def cache_tag_edit(request, gc_code):
    cache = _get_cache(gc_code)
    if request.method == "POST":
        action = request.POST.get("_action")
        if action == "add":
            name = request.POST.get("tag_name", "").strip()
            if name:
                from geocaches.models import Tag
                tag, _ = Tag.objects.get_or_create(name=name)
                cache.tags.add(tag)
        elif action == "remove":
            tag_id = request.POST.get("tag_id")
            if tag_id:
                cache.tags.remove(tag_id)
    return redirect("geocaches:detail", gc_code=gc_code)


def import_gsak_locations(request):
    """Import reference points from GSAK Options > Locations and per-DB centre points."""
    from pathlib import Path
    from django.http import HttpResponseRedirect
    from django.urls import reverse
    from geocaches.services import import_gsak_location_candidates, parse_and_import_gsak_locations

    GSAK_DIR = Path.home() / "AppData/Roaming/gsak"
    unique_candidates, errors, existing = parse_and_import_gsak_locations(GSAK_DIR)

    if request.method == "POST":
        selected_indices = request.POST.getlist("loc_idx")
        selected = []
        for idx_str in selected_indices:
            try:
                selected.append(unique_candidates[int(idx_str)])
            except (ValueError, IndexError):
                continue
        imported = import_gsak_location_candidates(selected)
        request.session["gsak_locations_imported"] = imported
        return HttpResponseRedirect(reverse("geocaches:import_gsak_locations"))

    imported = request.session.pop("gsak_locations_imported", [])

    return render(request, "geocaches/import_gsak_locations.html", {
        "candidates": unique_candidates,
        "errors": errors,
        "imported": imported,
        "existing": existing,
    })


def cache_export_gpx(request):
    from datetime import date as _date
    from datetime import datetime, timezone
    from geocaches.services import export_caches

    qs, _ = _filtered_qs(request)

    from preferences.models import UserPreference, GPX_EXPORT_DEFAULTS
    gc_username = UserPreference.get("gc_username", "")
    saved_opts = UserPreference.get("gpx_export_settings", {}) or {}
    opts = {**GPX_EXPORT_DEFAULTS, **saved_opts}
    data = export_caches(qs, username=gc_username, opts=opts)
    filename = f"gcforge-export-{_date.today().isoformat()}.gpx"

    dest = request.GET.get("dest", "").strip()
    if dest:
        from pathlib import Path
        dest_path = Path(dest) / filename
        try:
            dest_path.write_bytes(data)
        except Exception as exc:
            return JsonResponse({"error": str(exc)}, status=400)
        # Save to recent export folders
        recent = UserPreference.get("recent_export_folders", [])
        recent = [r for r in recent if r["path"] != dest]
        recent.insert(0, {
            "path": dest,
            "date": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M"),
        })
        UserPreference.set("recent_export_folders", recent[:5])
        return JsonResponse({"ok": True, "file": str(dest_path)})

    response = HttpResponse(data, content_type="application/gpx+xml")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


def export_recent_folders(request):
    from preferences.models import UserPreference
    recent = UserPreference.get("recent_export_folders", [])
    return JsonResponse({"folders": recent})


# ---------------------------------------------------------------------------
# Tools — Check duplicate found logs
# ---------------------------------------------------------------------------

def tools_duped_my_logs(request):
    """Find caches where the user has duplicate 'Found it' logs per source."""
    from accounts.models import UserAccount
    from django.db.models import Count

    qs, _ = _filtered_qs(request)
    query_string = request.GET.urlencode()

    accounts = list(UserAccount.objects.all())
    if not accounts:
        return render(request, "geocaches/tools_result.html", {
            "title": "Duplicate found logs (mine)",
            "message": "No accounts configured. Add your accounts in Settings > Accounts first.",
        })

    # Build per-platform username/id sets
    gc_ids = [a.user_id for a in accounts if a.platform == "gc" and a.user_id]
    gc_usernames = [a.username for a in accounts if a.platform == "gc" and a.username]
    oc_accounts = [(a.platform, a.username, a.user_id) for a in accounts if a.platform != "gc"]

    results = []  # list of (cache, source, count, logs)

    if not qs.exists():
        return render(request, "geocaches/tools_result.html", {
            "title": "Duplicate found logs (mine)",
            "message": "No caches in the current filter.",
        })

    found_type = "Found it"
    # Use subquery to avoid SQLite variable limit
    cache_filter = Q(geocache__in=qs)

    # Check GC logs
    if gc_ids or gc_usernames:
        gc_q = Q(source="gc", log_type=found_type) & cache_filter
        finder_q = Q()
        if gc_ids:
            finder_q |= Q(user_id__in=gc_ids)
        if gc_usernames:
            finder_q |= Q(user_name__in=gc_usernames)
        dupes = (
            Log.objects.filter(gc_q & finder_q)
            .values("geocache_id")
            .annotate(cnt=Count("id"))
            .filter(cnt__gt=1)
        )
        for row in dupes:
            cache = Geocache.objects.get(pk=row["geocache_id"])
            logs = list(
                Log.objects.filter(
                    gc_q & finder_q, geocache_id=row["geocache_id"]
                ).order_by("logged_date")
            )
            results.append({
                "cache": cache,
                "source": "GC",
                "count": row["cnt"],
                "logs": logs,
            })

    # Check OC logs per platform
    for platform, username, user_id in oc_accounts:
        oc_q = Q(source=platform, log_type=found_type) & cache_filter
        finder_q = Q()
        if user_id:
            finder_q |= Q(user_id=user_id)
        if username:
            finder_q |= Q(user_name=username)
        if not finder_q:
            continue
        dupes = (
            Log.objects.filter(oc_q & finder_q)
            .values("geocache_id")
            .annotate(cnt=Count("id"))
            .filter(cnt__gt=1)
        )
        for row in dupes:
            cache = Geocache.objects.get(pk=row["geocache_id"])
            logs = list(
                Log.objects.filter(
                    oc_q & finder_q, geocache_id=row["geocache_id"]
                ).order_by("logged_date")
            )
            results.append({
                "cache": cache,
                "source": platform.upper().replace("_", " "),
                "count": row["cnt"],
                "logs": logs,
            })

    return render(request, "geocaches/tools_duped_logs.html", {
        "title": "Duplicate found logs (mine)",
        "description": "Caches where you have more than one 'Found it' log on the same platform.",
        "results": results,
        "total": len(results),
        "query_string": query_string,
    })


def tools_duped_cache_logs(request):
    """Find owned caches where any single user has duplicate 'Found it' logs."""
    from accounts.models import UserAccount
    from django.db.models import Count

    qs, _ = _filtered_qs(request)
    query_string = request.GET.urlencode()

    # Filter to owned caches only
    owned_qs = qs.filter(mine_q())
    owned_count = owned_qs.count()
    if not owned_count:
        return render(request, "geocaches/tools_result.html", {
            "title": "Duplicate found logs (my caches)",
            "message": "No owned caches found in the current filter. Check that your accounts are configured in Settings > Accounts.",
        })

    found_type = "Found it"

    # Find (cache, user_name) pairs with >1 Found it log (any source)
    # Use subquery to avoid SQLite variable limit.
    # Exclude "opted-out user" — API placeholder for privacy-opted-out accounts.
    dupes = (
        Log.objects.filter(
            geocache__in=owned_qs,
            log_type=found_type,
        )
        .exclude(user_name="opted-out user")
        .values("geocache_id", "user_name")
        .annotate(cnt=Count("id"))
        .filter(cnt__gt=1)
        .order_by("geocache_id", "user_name")
    )

    results = []
    cache_map = {}
    for row in dupes:
        cid = row["geocache_id"]
        if cid not in cache_map:
            cache_map[cid] = Geocache.objects.get(pk=cid)
        logs = list(
            Log.objects.filter(
                geocache_id=cid,
                log_type=found_type,
                user_name=row["user_name"],
            ).order_by("logged_date")
        )
        results.append({
            "cache": cache_map[cid],
            "finder": row["user_name"],
            "count": row["cnt"],
            "logs": logs,
        })

    return render(request, "geocaches/tools_duped_logs.html", {
        "title": "Duplicate found logs (my caches)",
        "description": f"Checked {owned_count} owned cache(s) for finders with more than one 'Found it' log.",
        "results": results,
        "total": len(results),
        "query_string": query_string,
        "show_finder": True,
    })


def tools_check_ftf(request):
    """Start FTF candidate verification as a background task."""
    from django.contrib import messages
    from geocaches.update_task import start_update
    from geocaches.filters import EVENT_TYPES, FOUND_LOG_TYPES
    from geocaches.query import mine_q

    qs = (
        Geocache.objects.filter(found=False, completed=False, status="Active")
        .exclude(cache_type__in=EVENT_TYPES)
        .exclude(cache_type="Adventure Lab")
        .exclude(logs__log_type__in=FOUND_LOG_TYPES)
        .exclude(mine_q())
    )
    count = qs.count()

    if request.method == "POST":
        started = start_update(qs, "verify_ftf")
        if started:
            messages.success(request, f"FTF check started for {count} candidate(s).")
        else:
            messages.warning(request, "An update task is already running.")
        return redirect("geocaches:list")

    return render(request, "geocaches/tools_confirm.html", {
        "title": "Check FTF candidates",
        "description": f"This will fetch recent logs for {count} cache(s) that have no found-type logs, "
                       f"are not events, adventure labs, or owned by you. "
                       f"This runs in the background and may take a while.",
        "action_url": request.path,
        "submit_label": "Start check",
    })


def tools_ftf_markers(request):
    from django.contrib import messages
    from django.db.models import Q
    from accounts.models import UserAccount
    from geocaches.filters import FOUND_LOG_TYPES

    all_accounts = list(UserAccount.objects.all())
    my_ids = {a.user_id for a in all_accounts if a.user_id}
    my_names = {a.username for a in all_accounts if a.username}

    if not my_ids and not my_names:
        return render(request, "geocaches/tools_ftf_markers.html", {
            "items": [], "total": 0,
            "no_accounts": True,
        })

    finder_q = Q()
    if my_ids:
        finder_q |= Q(user_id__in=my_ids)
    if my_names:
        finder_q |= Q(user_name__in=my_names)

    found_caches = Geocache.objects.filter(found=True)

    if request.method == "POST":
        action = request.POST.get("action", "")

        if action == "apply_all":
            pks_set = request.POST.get("set_pks", "")
            pks_unset = request.POST.get("unset_pks", "")
            set_count = unset_count = 0
            if pks_set:
                pk_list = [int(p) for p in pks_set.split(",") if p.strip()]
                set_count = Geocache.objects.filter(pk__in=pk_list).update(ftf=True)
            if pks_unset:
                pk_list = [int(p) for p in pks_unset.split(",") if p.strip()]
                unset_count = Geocache.objects.filter(pk__in=pk_list).update(ftf=False)
            messages.success(request, f"Applied all: {set_count} set, {unset_count} unset.")
            return redirect("geocaches:tools_ftf_markers")

        pk = request.POST.get("pk", "")
        try:
            cache = Geocache.objects.get(pk=pk)
        except (Geocache.DoesNotExist, ValueError):
            return redirect("geocaches:tools_ftf_markers")

        if action == "set":
            cache.ftf = True
            cache.save(update_fields=["ftf"])
            messages.success(request, f"FTF set for {cache.display_code}.")
        elif action == "unset":
            cache.ftf = False
            cache.save(update_fields=["ftf"])
            messages.success(request, f"FTF unset for {cache.display_code}.")

        return redirect("geocaches:tools_ftf_markers")

    # --- GET: scan for FTF candidates ---
    items = []
    seen_pks = set()

    # 1) Caches with ftf=True already
    for cache in found_caches.filter(ftf=True):
        seen_pks.add(cache.pk)
        items.append({
            "cache": cache,
            "reasons": ["Flag already set"],
            "current_ftf": True,
            "suggestion": None,
            "needs_verify": False,
        })

    # 2) User's found logs containing [FTF] in text
    ftf_text_logs = Log.objects.filter(
        finder_q,
        log_type__in=FOUND_LOG_TYPES,
        text__icontains="[ftf]",
        geocache__found=True,
    ).select_related("geocache")
    for log in ftf_text_logs:
        cache = log.geocache
        if cache.pk in seen_pks:
            existing = next(i for i in items if i["cache"].pk == cache.pk)
            if "[FTF] in log text" not in existing["reasons"]:
                existing["reasons"].append("[FTF] in log text")
            if not existing["current_ftf"]:
                existing["suggestion"] = "set"
            continue
        seen_pks.add(cache.pk)
        items.append({
            "cache": cache,
            "reasons": ["[FTF] in log text"],
            "current_ftf": cache.ftf,
            "suggestion": "set" if not cache.ftf else None,
            "needs_verify": False,
        })

    # 3) User's found log is on the same day as (or before) the earliest "Found it" log.
    #    GC API only provides dates, not times, so same-day finders are all FTF candidates.
    my_found_logs = Log.objects.filter(
        finder_q,
        log_type__in=FOUND_LOG_TYPES,
        geocache__found=True,
    ).select_related("geocache").order_by("geocache_id", "logged_date")

    checked_cache_ids = set()
    for log in my_found_logs:
        if log.geocache_id in checked_cache_ids:
            continue
        checked_cache_ids.add(log.geocache_id)

        earliest = (
            Log.objects.filter(
                geocache_id=log.geocache_id,
                log_type__in=FOUND_LOG_TYPES,
            )
            .order_by("logged_date")
            .first()
        )
        if earliest and log.logged_date == earliest.logged_date:
            cache = log.geocache
            if cache.pk in seen_pks:
                existing = next(i for i in items if i["cache"].pk == cache.pk)
                if "First found log" not in existing["reasons"]:
                    existing["reasons"].append("First found log")
                if not existing["current_ftf"]:
                    existing["suggestion"] = "set"
                continue
            seen_pks.add(cache.pk)
            items.append({
                "cache": cache,
                "reasons": ["First found log"],
                "current_ftf": cache.ftf,
                "suggestion": "set" if not cache.ftf else None,
                "needs_verify": True,
            })

    items.sort(key=lambda i: i["cache"].display_code)
    verify_count = sum(1 for i in items if i.get("needs_verify"))

    return render(request, "geocaches/tools_ftf_markers.html", {
        "items": items,
        "total": len(items),
        "verify_count": verify_count,
    })


def ftf_verify_row(request, pk):
    """Fetch earlier logs for a single cache, re-check FTF, return updated row partial."""
    from django.db.models import Q
    from accounts.models import UserAccount
    from geocaches.filters import FOUND_LOG_TYPES

    cache = Geocache.objects.filter(pk=pk).first()
    if not cache:
        return HttpResponse("")

    # Fetch ALL logs to reliably verify FTF (API returns newest-first,
    # so we must page through everything to find logs near the publish date).
    saved = 0
    try:
        if cache.gc_code and cache.gc_code.startswith("GC"):
            from geocaches.sync.gc_client import GCClient
            from geocaches.sync.log_fetch import fetch_all_gc_logs
            client = GCClient()
            saved = fetch_all_gc_logs(client, cache.gc_code)
        elif cache.oc_code:
            from geocaches.sync.oc_client import OCClient
            from geocaches.sync.log_fetch import fetch_oc_logs
            platform = cache.primary_source or cache.oc_platform or "oc_de"
            acct = UserAccount.objects.filter(platform=platform).first()
            user_id = acct.user_id if acct else ""
            client = OCClient(platform=platform, user_id=user_id)
            # OC OKAPI caps at ~1000 logs via lpc; fetch max available
            saved = fetch_oc_logs(client, cache.oc_code, count=1000)
        else:
            saved = 0
    except Exception:
        saved = 0

    # Update local log count to reflect newly fetched logs
    actual_count = cache.logs.count()
    if actual_count != cache.platform_log_count:
        cache.platform_log_count = actual_count
        cache.save(update_fields=["platform_log_count"])

    # Re-check: is user's log still the earliest found log?
    all_accounts = list(UserAccount.objects.all())
    my_ids = {a.user_id for a in all_accounts if a.user_id}
    my_names = {a.username for a in all_accounts if a.username}
    finder_q = Q()
    if my_ids:
        finder_q |= Q(user_id__in=my_ids)
    if my_names:
        finder_q |= Q(user_name__in=my_names)

    reasons = []
    if cache.ftf:
        reasons.append("Flag already set")
    # Check [FTF] in text
    has_ftf_text = Log.objects.filter(
        finder_q,
        geocache=cache,
        log_type__in=FOUND_LOG_TYPES,
        text__icontains="[ftf]",
    ).exists()
    if has_ftf_text:
        reasons.append("[FTF] in log text")

    # Check if still first found log (same-day = candidate, since API lacks time)
    my_log = (
        Log.objects.filter(finder_q, geocache=cache, log_type__in=FOUND_LOG_TYPES)
        .order_by("logged_date")
        .first()
    )
    earliest = (
        Log.objects.filter(geocache=cache, log_type__in=FOUND_LOG_TYPES)
        .order_by("logged_date")
        .first()
    )
    is_first = my_log and earliest and my_log.logged_date == earliest.logged_date
    if is_first:
        reasons.append("First found log")

    suggestion = None
    if reasons and not cache.ftf:
        suggestion = "set"
    elif not reasons and cache.ftf:
        suggestion = "unset"

    verified = not is_first or has_ftf_text or cache.ftf
    status_msg = ""
    if is_first:
        status_msg = f"Verified ({saved} new log(s) fetched)" if saved else "Verified (no new logs)"
    elif my_log and earliest:
        status_msg = "Not first — earlier log exists"
        if not cache.ftf and not has_ftf_text:
            reasons = []
            suggestion = None

    return render(request, "geocaches/_ftf_row.html", {
        "item": {
            "cache": cache,
            "reasons": reasons,
            "current_ftf": cache.ftf,
            "suggestion": suggestion,
            "needs_verify": False,
            "verified": True,
            "verify_msg": status_msg,
        },
    })


def tools_misplaced_codes(request):
    """Detect caches where an OC code is stored in the gc_code field."""
    from django.contrib import messages
    from django.db.models import Q
    from geocaches.importers.lookups import OC_PREFIXES

    # Find all caches where gc_code starts with a known OC prefix
    q = Q()
    for pfx in OC_PREFIXES:
        q |= Q(gc_code__startswith=pfx)
    misplaced = list(Geocache.objects.filter(q).order_by("gc_code"))

    # For each misplaced cache, check if a correct record already exists
    items = []
    for cache in misplaced:
        oc_code = cache.gc_code
        correct = Geocache.objects.filter(oc_code=oc_code).first()
        items.append({
            "cache": cache,
            "oc_code": oc_code,
            "correct_record": correct,
            "strategy": "merge" if correct else "move",
        })

    if request.method == "POST":
        action = request.POST.get("action", "")

        if action == "fix_all":
            moved = merged = 0
            for item in items:
                c = item["cache"]
                code = c.gc_code
                if item["correct_record"]:
                    _merge_misplaced(source=c, dest=item["correct_record"])
                    merged += 1
                else:
                    c.oc_code = code
                    c.gc_code = ""
                    c.save(update_fields=["gc_code", "oc_code"])
                    moved += 1
            messages.success(request, f"Fixed {moved + merged} cache(s): {moved} moved, {merged} merged.")
            return redirect("geocaches:tools_misplaced_codes")

        pk = request.POST.get("pk", "")
        try:
            target = Geocache.objects.get(pk=pk)
        except (Geocache.DoesNotExist, ValueError):
            return redirect("geocaches:tools_misplaced_codes")

        oc_code = target.gc_code
        if action == "move":
            target.oc_code = oc_code
            target.gc_code = ""
            target.save(update_fields=["gc_code", "oc_code"])
            messages.success(request, f"Moved {oc_code} from gc_code to oc_code.")
        elif action == "merge":
            correct = Geocache.objects.filter(oc_code=oc_code).first()
            if correct:
                _merge_misplaced(source=target, dest=correct)
                messages.success(request, f"Merged misplaced record into {correct.display_code} and deleted duplicate.")
            else:
                target.oc_code = oc_code
                target.gc_code = ""
                target.save(update_fields=["gc_code", "oc_code"])
                messages.success(request, f"Correct record gone; moved {oc_code} to oc_code instead.")

        return redirect("geocaches:tools_misplaced_codes")

    return render(request, "geocaches/tools_misplaced_codes.html", {
        "items": items,
        "total": len(items),
    })


def _merge_misplaced(*, source, dest):
    """Merge a misplaced-code record into the correct one, then delete the source."""
    from geocaches.models import Log, Waypoint, Note, Image

    # Move logs that don't already exist on dest
    for log in source.logs.all():
        exists = dest.logs.filter(
            logged_date=log.logged_date, user_name=log.user_name, log_type=log.log_type,
        ).exists()
        if not exists:
            log.geocache = dest
            log.save(update_fields=["geocache"])

    # Move waypoints
    for wp in source.waypoints.all():
        exists = dest.waypoints.filter(lookup=wp.lookup).exists()
        if not exists:
            wp.geocache = dest
            wp.save(update_fields=["geocache"])

    # Move notes
    for note in source.notes.all():
        note.geocache = dest
        note.save(update_fields=["geocache"])

    # Move images
    for img in source.images.all():
        exists = dest.images.filter(url=img.url).exists()
        if not exists:
            img.geocache = dest
            img.save(update_fields=["geocache"])

    # Copy tags
    for tag in source.tags.all():
        dest.tags.add(tag)

    # Fill empty fields on dest from source
    fill_fields = [
        "name", "owner", "placed_by", "cache_type", "size", "difficulty", "terrain",
        "country", "iso_country_code", "state", "county", "elevation",
        "short_description", "long_description", "hint",
    ]
    updated = []
    for f in fill_fields:
        src_val = getattr(source, f)
        dst_val = getattr(dest, f)
        if src_val and not dst_val:
            setattr(dest, f, src_val)
            updated.append(f)
    if updated:
        dest.save(update_fields=updated)

    source.delete()


# ---------------------------------------------------------------------------
# Duplicate GC/OC detection tool
# ---------------------------------------------------------------------------

def tools_duplicate_caches(request):
    """Find and merge duplicate GC/OC entries for the same physical cache."""
    from django.contrib import messages
    from geocaches.services import find_potential_duplicates, merge_duplicate, set_fusion_decision

    if request.method == "POST":
        action = request.POST.get("action", "")

        if action == "merge_all":
            dupes = find_potential_duplicates()
            merged = 0
            for d in dupes:
                try:
                    merge_duplicate(d["gc_pk"], d["oc_pk"])
                    merged += 1
                except Exception:
                    pass
            messages.success(request, f"Merged {merged} duplicate(s). Details in the log.")
            return redirect("geocaches:tools_duplicate_caches")

        if action == "merge":
            gc_pk = request.POST.get("gc_pk", "")
            oc_pk = request.POST.get("oc_pk", "")
            try:
                desc = merge_duplicate(int(gc_pk), int(oc_pk))
                messages.success(request, desc)
            except Exception as exc:
                messages.error(request, f"Merge failed: {exc}")
            return redirect("geocaches:tools_duplicate_caches")

        if action == "dont_fuse":
            gc_code = request.POST.get("gc_code", "")
            oc_code = request.POST.get("oc_code", "")
            if gc_code and oc_code:
                set_fusion_decision(gc_code, oc_code, "dont_fuse")
                messages.success(request, f"Marked {gc_code}/{oc_code} as 'don't fuse'. It will no longer appear here.")
            return redirect("geocaches:tools_duplicate_caches")

        if action == "postpone":
            gc_code = request.POST.get("gc_code", "")
            oc_code = request.POST.get("oc_code", "")
            if gc_code and oc_code:
                set_fusion_decision(gc_code, oc_code, "postpone")
                messages.success(request, f"Postponed {gc_code}/{oc_code}.")
            return redirect("geocaches:tools_duplicate_caches")

    duplicates = find_potential_duplicates()
    return render(request, "geocaches/tools_duplicate_caches.html", {
        "duplicates": duplicates,
        "total": len(duplicates),
    })


def _build_manage_fused_rows(tab):
    """Build the row list for tools_manage_fused for the given tab. Shared by GET and POST."""
    from geocaches.models import Geocache, CacheFusionRecord

    fused_qs = list(
        Geocache.objects
        .filter(gc_code__startswith="GC", oc_code__gt="")
        .values("gc_code", "oc_code", "name", "owner", "primary_source")
        .order_by("gc_code")
    )
    fused_gc_codes = [c["gc_code"] for c in fused_qs]
    fusion_map = {
        (r.gc_code, r.oc_code): r
        for r in CacheFusionRecord.objects.filter(gc_code__in=fused_gc_codes)
    } if fused_qs else {}

    rows = []
    for c in fused_qs:
        rec = fusion_map.get((c["gc_code"], c["oc_code"]))
        rows.append({
            "gc_code": c["gc_code"],
            "oc_code": c["oc_code"],
            "name": c["name"],
            "owner": c["owner"],
            "auto_linked": rec.auto_linked if rec else False,
            "user_decision": rec.user_decision if rec else None,
            "is_fused": True,
        })

    # dont_fuse decisions for pairs no longer fused in DB
    for rec in CacheFusionRecord.objects.filter(user_decision="dont_fuse").exclude(
        gc_code__in=fused_gc_codes
    ):
        rows.append({
            "gc_code": rec.gc_code,
            "oc_code": rec.oc_code,
            "name": "",
            "owner": "",
            "auto_linked": rec.auto_linked,
            "user_decision": rec.user_decision,
            "is_fused": False,
        })

    if tab == "auto":
        rows = [r for r in rows if r["auto_linked"] and r["is_fused"]]
    elif tab == "dont_fuse":
        rows = [r for r in rows if r["user_decision"] == "dont_fuse"]
    elif tab == "fused":
        rows = [r for r in rows if r["is_fused"] and not r["auto_linked"]]
    # "all" → everything

    return rows


def tools_manage_fused(request):
    """View and manage all fused GC+OC caches and user decisions."""
    from django.contrib import messages
    from geocaches.models import Geocache
    from geocaches.services import set_fusion_decision

    if request.method == "POST":
        action = request.POST.get("action", "")
        gc_code = request.POST.get("gc_code", "")
        oc_code = request.POST.get("oc_code", "")
        tab = request.POST.get("tab", "fused")

        if action == "mark_dont_fuse" and gc_code and oc_code:
            set_fusion_decision(gc_code, oc_code, "dont_fuse")
            messages.success(request, f"Marked {gc_code}/{oc_code} as 'don't fuse'.")

        elif action == "remove_decision" and gc_code and oc_code:
            set_fusion_decision(gc_code, oc_code, None)
            messages.success(request, f"Cleared decision for {gc_code}/{oc_code}.")

        elif action == "refresh_all":
            rows = _build_manage_fused_rows(tab)
            oc_codes = [r["oc_code"] for r in rows if r["oc_code"]]
            if oc_codes:
                # Synchronous pass: promote existing OCExtension.related_gc_code data
                # into CacheFusionRecord.auto_linked without requiring an API call.
                # This covers caches fused from GPX imports where oc:other_code was set.
                from geocaches.models import CacheFusionRecord
                promoted = 0
                for item in (
                    Geocache.objects
                    .filter(gc_code__startswith="GC", oc_code__in=oc_codes)
                    .exclude(oc_extension__related_gc_code="")
                    .values("gc_code", "oc_code", "oc_extension__related_gc_code")
                ):
                    if item["oc_extension__related_gc_code"] == item["gc_code"]:
                        _, created = CacheFusionRecord.objects.update_or_create(
                            gc_code=item["gc_code"],
                            oc_code=item["oc_code"],
                            defaults={"auto_linked": True},
                        )
                        promoted += 1

                # Background pass: re-fetch OC API data; OKAPI's gc_code field will
                # set auto_linked for any caches where the owner explicitly linked them.
                # Use oc_link_refresh (OC-only, deduplicated) to avoid sending fused
                # caches to the GC API and to prevent OKAPI duplicate-code 400 errors.
                qs = Geocache.objects.filter(oc_code__in=oc_codes)
                from geocaches.update_task import start_update
                started = start_update(qs, "oc_link_refresh")
                if started:
                    msg = f"Refreshing OC data for {len(oc_codes)} cache(s) in background"
                    if promoted:
                        msg += f"; {promoted} already updated from local data"
                    msg += "."
                    messages.success(request, msg)
                else:
                    if promoted:
                        messages.success(request, f"Updated {promoted} record(s) from local data. An API refresh is already running.")
                    else:
                        messages.warning(request, "An update is already running.")
            else:
                messages.info(request, "No OC caches to refresh in this tab.")

        from django.urls import reverse
        return redirect(reverse("geocaches:tools_manage_fused") + f"?tab={tab}")

    tab = request.GET.get("tab", "fused")
    rows = _build_manage_fused_rows(tab)
    return render(request, "geocaches/tools_manage_fused.html", {
        "rows": rows,
        "tab": tab,
        "total": len(rows),
    })


def tools_unlinked_oc(request):
    """OC caches that reference a GC code but only have OC data (GC not yet imported)."""
    from django.contrib import messages
    from geocaches.models import Geocache

    if request.method == "POST":
        action = request.POST.get("action", "")
        gc_code = request.POST.get("gc_code", "")

        if action == "import_gc" and gc_code:
            from accounts.gc_client import has_api_tokens
            if not has_api_tokens():
                messages.error(request, "No GC API tokens — cannot import.")
                return redirect("geocaches:tools_unlinked_oc")
            try:
                from geocaches.sync.gc_client import GCClient
                from geocaches.sync.base import SyncMode
                from geocaches.services import save_geocache
                client = GCClient()
                data = client.get_cache(gc_code, SyncMode.FULL, log_count=5)
                kwargs = dict(data)
                kwargs["fields"] = dict(data["fields"])
                save_geocache(**kwargs)
                messages.success(request, f"GC data imported for {gc_code}.")
            except Exception as exc:
                messages.error(request, f"Import failed for {gc_code}: {exc}")

        return redirect("geocaches:tools_unlinked_oc")

    # Case A: fused records where primary_source is OC (GC data not yet fetched from GC API)
    case_a = list(
        Geocache.objects
        .filter(gc_code__startswith="GC", oc_code__gt="", primary_source__startswith="oc")
        .values("pk", "gc_code", "oc_code", "name", "owner", "latitude", "longitude")
    )

    # Case B: standalone OC caches (no gc_code) whose OCExtension states a related GC code
    case_b_qs = (
        Geocache.objects
        .filter(gc_code="", oc_code__gt="", oc_extension__related_gc_code__startswith="GC")
        .values("pk", "gc_code", "oc_code", "name", "owner", "latitude", "longitude",
                "oc_extension__related_gc_code")
    )
    case_a_pks = {c["pk"] for c in case_a}

    caches = list(case_a)
    for c in case_b_qs:
        if c["pk"] not in case_a_pks:
            caches.append({
                "pk": c["pk"],
                "gc_code": c["oc_extension__related_gc_code"],  # the GC to import
                "oc_code": c["oc_code"],
                "name": c["name"],
                "owner": c["owner"],
                "latitude": c["latitude"],
                "longitude": c["longitude"],
            })

    caches.sort(key=lambda c: c["gc_code"])

    return render(request, "geocaches/tools_unlinked_oc.html", {
        "caches": caches,
        "total": len(caches),
    })


# ---------------------------------------------------------------------------
# Pocket Query management
# ---------------------------------------------------------------------------

def pq_management(request):
    from preferences.models import UserPreference
    from geocaches.tasks import submit_task, get_task

    error = None
    pqs = []
    task_id = None
    task_result = None

    # Load saved PQ tag mappings
    pq_tags = UserPreference.get("pq_tag_map", {})

    # Check for completed task result
    result_task_id = request.GET.get("task_id")
    if result_task_id:
        task_data = get_task(result_task_id)
        if task_data:
            task_result = task_data

    if request.method == "POST":
        action = request.POST.get("action", "")

        # Save tag mappings from form (only if changed)
        old_pq_tags = dict(pq_tags)
        for key, value in request.POST.items():
            if key.startswith("tags_"):
                ref = key[5:]
                tags = [t.strip() for t in value.split(",") if t.strip()]
                if tags:
                    pq_tags[ref] = tags
                elif ref in pq_tags:
                    del pq_tags[ref]
        if pq_tags != old_pq_tags:
            UserPreference.set("pq_tag_map", pq_tags)

        if action == "download":
            ref = request.POST.get("reference_code", "")
            name = request.POST.get("pq_name", ref)
            tag_names = pq_tags.get(ref)
            if ref:
                from geocaches.pq_service import enqueue_pq_download
                task_id = enqueue_pq_download(ref, name, tag_names=tag_names)
                return redirect(f"{request.path}?task_id={task_id}")

        elif action == "download_all":
            try:
                from geocaches.pq_service import list_pocket_queries, download_all_ready
                pq_list = list_pocket_queries()
                tag_map = pq_tags
                task_id = submit_task(
                    "PQ: Download all",
                    download_all_ready,
                    pq_list, tag_map=tag_map,
                )
                return redirect(f"{request.path}?task_id={task_id}")
            except Exception as exc:
                error = str(exc)

        elif action == "trigger":
            guid = request.POST.get("pq_guid", "")
            name = request.POST.get("pq_name", "")
            if guid:
                from geocaches.pq_service import trigger_pq_run
                try:
                    task_id = submit_task(
                        f"PQ trigger: {name}",
                        trigger_pq_run,
                        guid, name,
                    )
                    return redirect(f"{request.path}?task_id={task_id}")
                except Exception as exc:
                    error = str(exc)

        elif action == "trigger_download":
            ref = request.POST.get("reference_code", "")
            guid = request.POST.get("pq_guid", "")
            name = request.POST.get("pq_name", ref)
            tag_names = pq_tags.get(ref)
            if guid and ref:
                from geocaches.pq_service import trigger_and_download_pq
                task_id = submit_task(
                    f"PQ trigger+download: {name}",
                    trigger_and_download_pq,
                    ref, guid, name, tag_names=tag_names,
                )
                return redirect(f"{request.path}?task_id={task_id}")

        elif action in ("trigger_pattern", "trigger_download_pattern"):
            pattern = request.POST.get("trigger_pattern", "").strip()
            if pattern:
                from geocaches.pq_service import (
                    trigger_pqs_by_pattern, trigger_and_download_by_pattern,
                )
                if action == "trigger_pattern":
                    task_id = submit_task(
                        f"PQ trigger: *{pattern}*",
                        trigger_pqs_by_pattern, pattern,
                    )
                else:
                    task_id = submit_task(
                        f"PQ trigger+download: *{pattern}*",
                        trigger_and_download_by_pattern,
                        pattern, pq_tags,
                    )
                return redirect(f"{request.path}?task_id={task_id}")

    # GET: fetch PQ list
    try:
        from geocaches.pq_service import list_pocket_queries
        pqs = list_pocket_queries()
    except Exception as exc:
        error = str(exc)

    # Try to fetch web status (GUIDs + trigger availability)
    web_status = {}
    pq_summary = {}
    try:
        from geocaches.pq_trigger import get_pq_web_status
        web_rows, pq_summary = get_pq_web_status()
        for wr in web_rows:
            if wr["name"]:
                web_status[wr["name"]] = wr
    except Exception as exc:
        logger.debug("Could not fetch PQ web status: %s", exc)

    # Annotate each PQ with its saved tags string, web status, and formatted times
    from zoneinfo import ZoneInfo
    gc_tz = ZoneInfo("America/Los_Angeles")
    local_tz = datetime.now().astimezone().tzinfo

    from geocaches.pq_service import get_imported_pqs
    imported_pqs = get_imported_pqs()

    for pq in pqs:
        ref = pq.get("referenceCode", "")
        pq["saved_tags"] = ", ".join(pq_tags.get(ref, []))
        ws = web_status.get(pq.get("name", ""), {})
        pq["guid"] = ws.get("guid", "")
        pq["can_trigger"] = bool(ws.get("trigger_url"))
        pq["already_ran"] = ws.get("already_ran", False)
        pq["already_sched"] = ws.get("already_sched", False)
        pq["imported"] = ref in imported_pqs

        # Format timestamps
        raw_utc = pq.get("lastUpdatedDateUtc", "")
        pq["local_time"] = ""
        pq["server_time"] = ""
        if raw_utc:
            try:
                dt = datetime.fromisoformat(raw_utc.rstrip("Z")).replace(tzinfo=timezone.utc)
                pq["local_time"] = dt.astimezone(local_tz).strftime("%Y-%m-%d %H:%M")
                pq["server_time"] = dt.astimezone(gc_tz).strftime("%Y-%m-%d %H:%M")
            except (ValueError, TypeError):
                pass

    # Existing tags for quick-pick
    from geocaches.models import Tag
    all_tags = list(Tag.objects.order_by("name").values_list("name", flat=True))

    return render(request, "geocaches/pq_management.html", {
        "pqs": pqs,
        "error": error,
        "task_result": task_result,
        "all_tags": all_tags,
        "has_web_session": bool(web_status),
        "pq_summary": pq_summary,
    })


def pq_list_json(request):
    """JSON endpoint returning the current PQ list (for polling refresh)."""
    try:
        from geocaches.pq_service import list_pocket_queries
        pqs = list_pocket_queries()
    except Exception as exc:
        return JsonResponse({"error": str(exc)}, status=500)

    result = []
    for pq in pqs:
        result.append({
            "referenceCode": pq.get("referenceCode", ""),
            "name": pq.get("name", ""),
            "count": pq.get("count"),
            "lastUpdatedDateUtc": pq.get("lastUpdatedDateUtc", ""),
        })
    return JsonResponse({"pqs": result})


# ---------------------------------------------------------------------------
# Event calendar
# ---------------------------------------------------------------------------

_EVENT_TYPES = {
    CacheType.EVENT, CacheType.CITO, CacheType.MEGA_EVENT, CacheType.GIGA_EVENT,
    CacheType.COMMUNITY_CELEBRATION, CacheType.GC_HQ, CacheType.GC_HQ_CELEBRATION,
    CacheType.GC_HQ_BLOCK_PARTY,
}

import re as _re
import html as _html_module

_TIME_RE = _re.compile(
    r'(?:(?:Beginn|Start|Zeit|Time|um|at)\s*[:\s]?\s*)?'
    r'\b(\d{1,2})[:\.](\d{2})\s*(?:Uhr|uhr|h\b|AM|PM|am|pm)?',
    _re.IGNORECASE,
)


def _strip_html(text):
    """Remove HTML tags and decode entities."""
    text = _re.sub(r'<[^>]+>', ' ', text)
    return _html_module.unescape(text)


def _extract_event_time(cache):
    """Return (hour, minute) parsed from description, or None if not found."""
    combined = _strip_html(
        (cache.short_description or '') + ' ' + (cache.long_description or '')
    )
    m = _TIME_RE.search(combined)
    if not m:
        return None
    h, mn = int(m.group(1)), int(m.group(2))
    if 0 <= h <= 23 and 0 <= mn <= 59:
        return h, mn
    return None


def tools_event_calendar(request):
    from datetime import date
    from preferences.models import ReferencePoint, UserPreference
    from .distance_cache import _haversine_km

    today = date.today()
    events = (
        Geocache.objects
        .filter(cache_type__in=_EVENT_TYPES, hidden_date__gte=today)
        .prefetch_related('tags')
        .order_by('hidden_date', 'name')
    )

    distance_unit = UserPreference.get("distance_unit", "km")
    ref = ReferencePoint.objects.filter(is_default=True).first()
    if ref is None:
        refs = list(ReferencePoint.objects.all())
        ref = refs[0] if refs else None

    rows = []
    for ev in events:
        if ref and ev.latitude is not None and ev.longitude is not None:
            km = _haversine_km(ref.latitude, ref.longitude, ev.latitude, ev.longitude)
            dist = km if distance_unit == "km" else km * 0.621371
            dist_str = f"{dist:.1f} {distance_unit}"
        else:
            dist_str = "—"
        rows.append({"cache": ev, "distance": dist_str})

    return render(request, "geocaches/tools_event_calendar.html", {
        "rows": rows,
        "ref": ref,
        "today": today,
    })


def tools_event_ical(request, pk):
    from datetime import date, timedelta
    from django.http import HttpResponse

    cache = get_object_or_404(Geocache, pk=pk)
    event_date = cache.hidden_date or date.today()
    time_info = _extract_event_time(cache)

    uid = f"{cache.display_code or cache.pk}@gcforge"
    summary = cache.name or cache.display_code or str(cache.pk)
    location = f"{cache.latitude},{cache.longitude}" if cache.latitude and cache.longitude else ""

    if time_info:
        h, mn = time_info
        dtstart = f"{event_date.strftime('%Y%m%d')}T{h:02d}{mn:02d}00"
        dtend_dt = event_date
        end_h = h + 2
        if end_h >= 24:
            end_h -= 24
            dtend_dt = event_date + timedelta(days=1)
        dtend = f"{dtend_dt.strftime('%Y%m%d')}T{end_h:02d}{mn:02d}00"
        dt_prefix = "DTSTART"
        dt_end_prefix = "DTEND"
    else:
        dtstart = event_date.strftime('%Y%m%d')
        dtend = (event_date + timedelta(days=1)).strftime('%Y%m%d')
        dt_prefix = "DTSTART;VALUE=DATE"
        dt_end_prefix = "DTEND;VALUE=DATE"

    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//GCForge//Event Calendar//EN",
        "BEGIN:VEVENT",
        f"UID:{uid}",
        f"{dt_prefix}:{dtstart}",
        f"{dt_end_prefix}:{dtend}",
        f"SUMMARY:{summary}",
    ]
    if location:
        lines.append(f"LOCATION:{location}")
    if cache.external_url:
        lines.append(f"URL:{cache.external_url}")
    lines += ["END:VEVENT", "END:VCALENDAR"]

    ics = "\r\n".join(lines) + "\r\n"
    slug = _re.sub(r'[^\w-]', '_', summary)[:40]
    response = HttpResponse(ics, content_type="text/calendar; charset=utf-8")
    response['Content-Disposition'] = f'attachment; filename="{slug}.ics"'
    return response


def pq_match_preview(request):
    """JSON endpoint: preview which PQs match a name pattern."""
    pattern = request.GET.get("pattern", "").strip()
    if not pattern:
        return JsonResponse({"error": "No pattern specified."}, status=400)

    try:
        from geocaches.pq_trigger import match_pqs_by_pattern
        matching, summary = match_pqs_by_pattern(pattern)
    except Exception as exc:
        return JsonResponse({"error": str(exc)}, status=500)

    return JsonResponse({
        "matching": [
            {
                "name": r["name"],
                "already_ran": r["already_ran"],
                "already_sched": r["already_sched"],
                "has_trigger_url": bool(r["trigger_url"]),
            }
            for r in matching
        ],
        "summary": summary,
    })
