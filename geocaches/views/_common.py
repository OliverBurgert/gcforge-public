"""Private helpers shared by more than one geocaches view module."""

from django.http import Http404

from ..models import EVENT_CACHE_TYPES, Geocache, Log


def _get_cache(code, qs=None):
    """Look up a Geocache by gc_code or oc_code. Raises Http404 if not found."""
    if qs is None:
        qs = Geocache.objects.all()
    cache = qs.filter(gc_code=code).first() or qs.filter(al_code=code).first() or qs.filter(oc_code=code).first()
    if cache is None:
        raise Http404(f"No cache with code {code!r}")
    return cache


def _filtered_qs(request, qs=None):
    """Apply all filters (scope + params + where + distance/bearing) to a queryset.

    Resolves the active reference point and distance unit so radius/bearing
    filters are honoured — unlike a bare ``apply_all(qs, request.GET)`` call
    which silently skips distance filters when no *ref* is passed.
    """
    from preferences.models import UserPreference
    from preferences.services import resolve_active_reference_point
    from ..filtering.query import apply_all

    if qs is None:
        qs = Geocache.objects.all()

    distance_unit = UserPreference.get("distance_unit", "km")
    rrp = resolve_active_reference_point(request)
    ref = rrp.ref_point

    if ref:
        from ..geo.distance_cache import ensure_cached
        ensure_cached(ref)

    from ..filtering.query import apply_action_scope
    qs, fv = apply_all(qs, request.GET, ref=ref, distance_unit=distance_unit)
    qs = apply_action_scope(qs, request.GET)
    return qs, fv


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


def _build_log_submit_context(cache, *, selected_log_type="", logged_at_value=None,
                               sequence_number_value=None, log_text_value=""):
    """Build log submission form context. Shared by cache_detail and bulk_logging."""
    from geocaches.sync.log_submit import cache_timezone
    from datetime import datetime as _dt, timezone as _tz
    from accounts.gc_client import has_api_tokens
    from accounts.keyring_util import get_oauth_token
    from accounts.models import UserAccount

    cache_tz = cache_timezone(cache.latitude, cache.longitude)

    if cache.cache_type in EVENT_CACHE_TYPES:
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
