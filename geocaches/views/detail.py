from datetime import datetime, timezone

from django.core.paginator import Paginator
from django.db.models import Q
from django.http import Http404
from django.shortcuts import render

from ..models import EVENT_CACHE_TYPES, Geocache, Log, Tag

# Single source of truth: geocaches.models.EVENT_CACHE_TYPES (CacheType.event_types()).
_LOG_SUBMIT_EVENT_TYPES = EVENT_CACHE_TYPES


def _get_cache(code, qs=None):
    """Look up a Geocache by gc_code or oc_code. Raises Http404 if not found."""
    if qs is None:
        qs = Geocache.objects.all()
    cache = qs.filter(gc_code=code).first() or qs.filter(al_code=code).first() or qs.filter(oc_code=code).first()
    if cache is None:
        raise Http404(f"No cache with code {code!r}")
    return cache


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
        except (ValueError, OSError, KeyError) as exc:
            import logging
            logging.getLogger(__name__).debug("Skipping image_%d: %s", i, exc)
        i += 1
    return attachments


def _parse_logged_at(s: str) -> "datetime | None":
    for fmt in ("%Y-%m-%dT%H:%M", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(s.strip(), fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


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
    # Seed Find # from the cached total platform finds (refreshed on the user
    # profile page). Falls back to local max_seq if no cache exists yet so
    # behaviour is sane on a brand-new install.
    from preferences.models import UserPreference as _UPref
    cached_total = _UPref.get("cached_total_finds", 0) or 0
    sequence_seed = max(cached_total, max_seq or 0)

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
        sequence_number_value = (sequence_seed + 1) if sequence_seed else None
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

    from preferences.models import UserPreference as _UP, LogTemplate
    from geocaches.log_format import COMPOSE_SMILEYS, expand_placeholders, PLACEHOLDER_KEYS

    # Templates whose scope is "any" or matches one of the offered log types.
    offered_types = [v for v, _ in log_type_choices]
    log_templates_qs = LogTemplate.objects.filter(scope__in=["any", *offered_types]).order_by("scope", "name")
    compose_templates = []
    for t in log_templates_qs:
        compose_templates.append({
            "id":     t.pk,
            "name":   t.name,
            "scope":  t.scope,
            # Pre-expand non-log-type-dependent placeholders against the cache.
            # The toolbar JS chooses which template to insert based on the
            # *currently selected* log type, so we expand per-template here
            # using its scope as the log_type when scope is concrete.
            "body":   expand_placeholders(
                t.body, cache=cache,
                log_type=t.scope if t.scope != "any" else (selected_log_type or ""),
            ),
        })

    # Trackables in the cache (parsed from GPX). Only meaningful for GC caches
    # — Geokrety (OC) are handled separately. The user-inventory section is
    # loaded by JS via the trackable_inventory endpoint when the panel opens.
    tb_mentions = []
    if cache.gc_code:
        tb_mentions = [
            {"ref_code": m.ref_code, "name": m.name}
            for m in cache.trackable_mentions.all()
        ]
    tb_log_templates = []
    for t in LogTemplate.objects.filter(
        scope__in=["any", "tb_discover", "tb_retrieve", "tb_drop", "tb_grab", "tb_note"],
    ).order_by("scope", "name"):
        tb_log_templates.append({
            "id":    t.pk,
            "name":  t.name,
            "scope": t.scope,
            # No cache-specific expansion here — TB placeholders are per-row
            # and resolved client-side when the user inserts a template.
            "body":  t.body,
        })

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
        "compose_smileys": COMPOSE_SMILEYS,
        "compose_templates": compose_templates,
        "compose_placeholder_keys": PLACEHOLDER_KEYS,
        "tb_mentions": tb_mentions,
        "tb_log_templates": tb_log_templates,
        "tb_enabled": bool(cache.gc_code and not cache.al_code),
        "tb_cache_gc_code": cache.gc_code or "",
    }


def cache_detail(request, gc_code):
    from preferences.models import UserPreference
    from geocaches.geo.coords import format_coords

    cache = _get_cache(gc_code, Geocache.objects.select_related(
        "adventure", "oc_extension", "al_journal", "al_detail",
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

    # AL reviews for ALC parents
    al_reviews = []
    al_review_api_total = 0
    al_review_skip = 0
    al_review_has_more = False
    from geocaches.services.adventures import is_al_parent
    if is_al_parent(cache) and getattr(cache, "adventure", None):
        al_reviews = list(cache.adventure.al_reviews.all())
        al_review_api_total = request.session.get(
            f"al_review_total_{cache.pk}", cache.adventure.ratings_total_count or 0
        )
        al_review_skip = request.session.get(f"al_review_skip_{cache.pk}", len(al_reviews))
        al_review_has_more = request.session.get(f"al_review_has_more_{cache.pk}", len(al_reviews) < al_review_api_total)

    # For parent ALC (LC{base}): pass stages so they appear on map + in table.
    # Query Geocache.objects directly (not via reverse relation) to ensure no
    # scope/list filter from the list view can bleed into this independent query.
    stages = None
    stages_found_count = 0
    if is_al_parent(cache):
        stages = list(
            Geocache.objects
            .select_related("al_journal", "al_detail")
            .filter(adventure_id=cache.adventure_id, al_detail__isnull=False)
            .order_by("al_detail__stage_number")
        )
        stages_found_count = sum(1 for s in stages if s.found)

    # For ALC children: compute prev/next sibling stage for navigation.
    prev_stage = next_stage = None
    from geocaches.services.adventures import is_al_parent as _is_al_parent
    if not _is_al_parent(cache) and cache.adventure_id and hasattr(cache, "al_detail") and cache.al_detail:
        siblings = list(
            Geocache.objects
            .filter(adventure_id=cache.adventure_id, al_detail__isnull=False)
            .order_by("al_detail__stage_number")
            .values_list("al_code", "gc_code", "al_detail__stage_number")
        )
        my_num = cache.al_detail.stage_number
        for i, (_al_c, _gc_c, num) in enumerate(siblings):
            if num == my_num:
                if i > 0:
                    pc = siblings[i - 1]
                    prev_stage = {"code": pc[0] or pc[1], "num": pc[2]}
                if i < len(siblings) - 1:
                    nc = siblings[i + 1]
                    next_stage = {"code": nc[0] or nc[1], "num": nc[2]}
                break

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
        from functools import reduce
        import operator

        from ..query import resolve_my_identities

        # Shared identity source (same data mine_finder_q() builds its Q from),
        # with the legacy gc_username preference folded into the GC platform.
        # Applied here as a per-source log filter — see the per-platform /
        # blank-source / is_local rules below.
        _ids = resolve_my_identities(gc_username=gc_username)
        platform_map = _ids.platform_map
        all_ids = _ids.all_ids
        all_names = _ids.all_names

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
                # Single source of truth: UserAccount.oc_platforms().
                from accounts.models import UserAccount
                owner_q |= Q(source__in=UserAccount.oc_platforms()) & gc_identity_q

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

    from geocaches.services.map_visibility import get_state as _mv_get_state
    map_visibility_state = _mv_get_state(cache, request.session)
    # When the cache is an AL parent, the dropdown shows a cascade hint
    # ("Applies to all N stages") — N is the count of sibling stage rows.
    map_visibility_stages_count = len(stages) if stages else 0

    from geocaches.models import IgnoreListEntry, IgnoreSource
    _il = list(IgnoreListEntry.objects.filter(code=cache.display_code).values("source", "oc_platform"))
    _local_sources = (IgnoreSource.INTERNAL, IgnoreSource.GSAK)
    _il_local = any(e["source"] in _local_sources for e in _il)
    _il_gc    = any(e["source"] == IgnoreSource.GC for e in _il)
    _il_oc    = any(e["source"] == IgnoreSource.OC for e in _il)
    ignore_state = {
        "local":       _il_local,
        "gc":          _il_gc,
        "oc":          _il_oc,
        "oc_platform": next((e["oc_platform"] for e in _il if e["source"] == IgnoreSource.OC), ""),
        "any":         _il_local or _il_gc or _il_oc,
        "count":       sum([_il_local, _il_gc, _il_oc]),
    }

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
    from .map import TYPE_SHORT, WP_TYPE_SHORT
    map_waypoints = [
        {
            "lat": wp.latitude,
            "lon": wp.longitude,
            "type": wp.waypoint_type,
            "t": WP_TYPE_SHORT.get(wp.waypoint_type, "O"),
            "name": wp.name or wp.lookup,
        }
        for wp in _visible_coord_wps
    ]
    cache_type_short = TYPE_SHORT.get(cache.cache_type, "?")

    from preferences.models import UserPreference as _UP
    gc_public_guid = _UP.get("gc_public_guid", "")

    context = {
        "cache": cache,
        "gc_public_guid": gc_public_guid,
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
        "stages_found_count": stages_found_count,
        "map_state": getattr(cache, "map_state", None),
        "log_skip": log_skip,
        "log_has_more": log_has_more,
        "embed": request.GET.get("embed") == "1",
        **log_submit_ctx,
        "hidden_waypoint_count": hidden_waypoint_count,
        "coord_waypoints": coord_waypoints,
        "map_waypoints": map_waypoints,
        "cache_type_short": cache_type_short,
        # ALC reviews
        "al_reviews": al_reviews,
        "al_review_api_total": al_review_api_total,
        "al_review_skip": al_review_skip,
        "al_review_has_more": al_review_has_more,
        # ALC stage navigation
        "prev_stage": prev_stage,
        "next_stage": next_stage,
        # De-fuse
        "ignore_state": ignore_state,
        "map_visibility_state": map_visibility_state,
        "map_visibility_stages_count": map_visibility_stages_count,
        "defuse_available": defuse_available,
        "defuse_gc_ok": defuse_gc_ok,
        "defuse_oc_ok": defuse_oc_ok,
        "defuse_has_corrected": defuse_has_corrected,
        "defuse_has_notes": defuse_has_notes,
        "is_event": cache.cache_type in _LOG_SUBMIT_EVENT_TYPES,
    }
    response = render(request, "geocaches/detail.html", context)
    response["X-Frame-Options"] = "SAMEORIGIN"
    return response


def al_answer_save(request, gc_code):
    """Save and verify a user's AL stage answer."""
    from django.shortcuts import redirect
    from preferences.models import UserPreference
    from ..models import ALStageDetail

    cache = _get_cache(gc_code)
    if request.method != "POST":
        return redirect("geocaches:detail", gc_code=gc_code)

    answer = request.POST.get("user_answer", "").strip()
    user_public_guid = UserPreference.get("gc_public_guid", "")

    detail, _ = ALStageDetail.objects.get_or_create(geocache=cache)
    detail.user_answer = answer
    if detail.answer_hash and answer and user_public_guid:
        detail.verify_answer(answer, user_public_guid)
    else:
        detail.answer_is_correct = None
    detail.save()
    return redirect("geocaches:detail", gc_code=gc_code)
