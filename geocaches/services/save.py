"""Canonical single-cache persistence.

``save_geocache()`` is the one way a Geocache row and its related objects are
written: every importer, the GC/OC API sync, map sync, the refresh task and the
ALC save funnel through it.
"""
import logging
from dataclasses import dataclass
from datetime import date

from geocaches.runtime_cache import invalidate_filter_dialog_options, invalidate_platform_availability

from .dedup import _PROXIMITY_DEG, _merge_into
from .fusion import _record_auto_link

_merge_logger = logging.getLogger("geocaches.import")

# ---------------------------------------------------------------------------
# SaveResult + save_geocache — canonical single-cache persistence
# ---------------------------------------------------------------------------

@dataclass
class SaveResult:
    geocache: object  # Geocache instance
    created: bool
    updated: bool
    locked: bool
    merged_from: str = ""  # non-empty if a proximity merge happened


# Shared fields that GC "owns" — OC updates must not overwrite these
# when the cache already has GC data.
_GC_OWNED_FIELDS = frozenset({
    "name", "owner", "placed_by", "owner_gc_id",
    "cache_type", "size", "status",
    "latitude", "longitude",
    "difficulty", "terrain",
    "short_description", "long_description", "hint",
    "hidden_date", "event_start_time", "event_end_time",
    "country", "iso_country_code", "state",
    "fav_points", "has_trackable", "is_premium",
    "background_image_url",
})


def save_geocache(
    *,
    # --- Identity (at least one required) ---
    gc_code: str = "",
    oc_code: str = "",
    al_code: str = "",
    al_stage_uuid: str = "",
    # --- Core fields (dict of model field -> value) ---
    fields: dict,
    # --- Found status (optional, never demotes) ---
    found: bool | None = None,
    found_date: date | None = None,
    # --- Related objects (all optional) ---
    tags: list | None = None,
    logs: list[dict] | None = None,
    waypoints: list[dict] | None = None,
    attributes: list[dict] | None = None,
    corrected_coords: dict | None = None,
    images: list[dict] | None = None,
    notes: list[dict] | None = None,
    oc_ext: dict | None = None,
    trackable_mentions: list[dict] | None = None,
    # --- Options ---
    skip_notes_if_exist: bool = True,
    update_source: str = "",
    # Import-time cache for Attribute lookups, avoids ~N get_or_create per cache.
    # Bulk-importers build this once via build_attribute_cache() and pass it in.
    attribute_cache: dict | None = None,
) -> SaveResult:
    """
    Canonical function to persist a single geocache and its related objects.

    Lookup order: al_stage_uuid -> gc_code -> al_code -> oc_code.
    Found status is only promoted, never demoted.
    Import-locked caches skip ALL updates.

    Source precedence (update_source):
      - "oc": if the cache already has GC data (gc_code set), shared fields
        (name, descriptions, etc.) are preserved from the GC import.
        OC-only fields (oc_code, logs, attributes,
        waypoints) are still added.
      - "gc" or "": always update shared fields (default behaviour).
    """
    from geocaches.db_lock import db_write
    from geocaches.models import Geocache

    # All writes funnel through the global write lock so concurrent background
    # tasks can't open overlapping SQLite write transactions.  RLock makes this
    # a no-op when the caller already holds it (e.g. importer atomic blocks).
    with db_write():
        from geocaches.services.ignore_list import is_internally_ignored
        candidate_code = gc_code or oc_code or al_code
        if candidate_code and is_internally_ignored(candidate_code):
            # Only block first-time imports; existing caches are updated normally.
            from geocaches.models import Geocache
            if gc_code:
                exists = Geocache.objects.filter(gc_code=gc_code).exists()
            elif al_code:
                exists = Geocache.objects.filter(al_code=al_code).exists()
            else:
                exists = Geocache.objects.filter(oc_code=oc_code).exists()
            if not exists:
                return SaveResult(geocache=None, created=False, updated=False, locked=False)

        return _save_geocache_inner(
            gc_code=gc_code, oc_code=oc_code, al_code=al_code,
            al_stage_uuid=al_stage_uuid, fields=fields, found=found,
            found_date=found_date, tags=tags, logs=logs, waypoints=waypoints,
            attributes=attributes, corrected_coords=corrected_coords,
            images=images, notes=notes, oc_ext=oc_ext,
            trackable_mentions=trackable_mentions,
            skip_notes_if_exist=skip_notes_if_exist, update_source=update_source,
            attribute_cache=attribute_cache,
        )


def _save_geocache_inner(
    *,
    gc_code: str,
    oc_code: str,
    al_code: str,
    al_stage_uuid: str,
    fields: dict,
    found,
    found_date,
    tags,
    logs,
    waypoints,
    attributes,
    corrected_coords,
    images,
    notes,
    oc_ext,
    trackable_mentions,
    skip_notes_if_exist: bool,
    update_source: str,
    attribute_cache: dict | None = None,
) -> SaveResult:
    from geocaches.models import Geocache

    geocache = None
    created = False

    # 1. UUID lookup (Adventure Lab stages)
    if al_stage_uuid:
        from geocaches.models import ALStageDetail
        detail = ALStageDetail.objects.filter(al_stage_uuid=al_stage_uuid).select_related("geocache").first()
        if detail:
            geocache = detail.geocache
            if geocache.deleted_at is not None:
                # Re-import of a trashed ALC stage: hard-delete the stale entry
                # so the code-lookup below creates a fresh record.
                geocache.delete()
                geocache = None

    # 2. Code lookup — hard-delete any matching trash entry first so
    #    get_or_create always produces a clean new row on re-import.
    from geocaches.services.codes import CODE_FIELDS

    merged_from = ""
    lookup_field = ""
    lookup_value = ""
    if geocache is None:
        if gc_code:
            lookup_field, lookup_value = "gc_code", gc_code
        elif al_code:
            lookup_field, lookup_value = "al_code", al_code
        elif oc_code:
            lookup_field, lookup_value = "oc_code", oc_code
        else:
            raise ValueError("At least one of gc_code, oc_code, al_code, or al_stage_uuid required")
        Geocache.all_objects.filter(
            deleted_at__isnull=False, **{lookup_field: lookup_value}
        ).delete()

    # 2a. A *second* external code in `fields` (a dual-listed OC import names
    #     its GC counterpart) may already belong to another live record, and
    #     live codes are unique.  Hold those codes back and write them in
    #     _apply_extra_codes() once the row exists.
    extra_codes = {
        f: fields[f] for f in CODE_FIELDS
        if fields.get(f) and not (f == lookup_field and fields[f] == lookup_value)
    }
    if extra_codes:
        fields = {k: v for k, v in fields.items() if k not in extra_codes}

    if geocache is None:
        geocache, created = Geocache.objects.get_or_create(
            defaults=fields, **{lookup_field: lookup_value}
        )

    # 2b. Proximity duplicate detection for newly created OC caches.
    #     Auto-merge only happens via explicit cross-reference (gc_code from
    #     OC data, handled in _apply_extra_codes below).  Proximity matches are
    #     logged so the user can review them via Tools → Duplicate Caches.
    if created and oc_code and not gc_code:
        lat = fields.get("latitude")
        lon = fields.get("longitude")
        if lat is not None and lon is not None:
            match = _find_proximity_match(geocache, lat, lon)
            if match:
                _merge_logger.info(
                    "Potential duplicate: %s is within 15m of %s "
                    "(use Tools → Duplicate Caches to review)",
                    oc_code, match.display_code,
                )

    # 3. Import lock check
    if not created and geocache.import_locked:
        return SaveResult(geocache=geocache, created=False, updated=False, locked=True)

    # 4. Update fields + found promotion in a single save (if not created).
    #    If the cache has a gc_code, GC owns the shared fields. OC updates
    #    (any OC platform, incl. oc_de) may only touch OC-specific fields and
    #    append new logs. See test_field_precedence.py for the truth matrix.
    from geocaches.services.adventures import ensure_not_al_parent_found

    # Snapshot prior trackable-mention state before setattr overwrites it; used
    # later to skip the prune query when neither old nor new has any mentions.
    had_trackables_before = (not created) and bool(getattr(geocache, "has_trackable", False))
    # Same for the listed coordinates — a cache that moves needs its cached
    # distances refreshed (see services.coords / geo.distance_cache).
    coords_before = (geocache.latitude, geocache.longitude)

    if not created:
        skip_shared = update_source == "oc" and bool(geocache.gc_code)
        for key, value in fields.items():
            if skip_shared and key in _GC_OWNED_FIELDS:
                continue
            setattr(geocache, key, value)
        if geocache.is_placeholder:
            geocache.is_placeholder = False
        # Fold found promotion (never demotes) into the same save.
        if found is True and not geocache.found:
            geocache.found = True
            if found_date:
                geocache.found_date = found_date
        elif geocache.found and not geocache.found_date and found_date:
            geocache.found_date = found_date
        ensure_not_al_parent_found(geocache)
        geocache.save()
    else:
        # Row was just inserted via get_or_create(defaults=fields). Found
        # promotion may still need to fire; emit a single update_fields save.
        promote_fields = []
        if found is True and not geocache.found:
            geocache.found = True
            promote_fields.append("found")
            if found_date:
                geocache.found_date = found_date
                promote_fields.append("found_date")
        elif geocache.found and not geocache.found_date and found_date:
            geocache.found_date = found_date
            promote_fields.append("found_date")
        ensure_not_al_parent_found(geocache)
        if promote_fields:
            geocache.save(update_fields=promote_fields)

    # 5. The codes held back in step 2a.
    if extra_codes:
        merged_from = _apply_extra_codes(geocache, extra_codes) or merged_from

    # 6. Tags
    if tags:
        geocache.tags.add(*tags)

    # 7. Attributes — use the import-time cache when present (avoids ~N
    # get_or_create lookups per cache during PQ imports).
    if attributes:
        from geocaches.models import Attribute
        attr_objs = []
        for a in attributes:
            key = (a["source"], a["attribute_id"], a["is_positive"])
            obj = attribute_cache.get(key) if attribute_cache is not None else None
            if obj is None:
                obj, _ = Attribute.objects.get_or_create(
                    source=a["source"],
                    attribute_id=a["attribute_id"],
                    is_positive=a["is_positive"],
                    defaults={"name": a.get("name", f"Attribute #{a['attribute_id']}")},
                )
                if attribute_cache is not None:
                    attribute_cache[key] = obj
            attr_objs.append(obj)
        geocache.attributes.add(*attr_objs)

    # 8. Logs (dedup by source_id, then by date+user_name+type as fallback)
    if logs:
        from geocaches.models import Log
        # Single query: fetch source_id, text, and the fallback dedup tuple in
        # one pass. Re-imports normally just confirm existing logs unchanged,
        # so skip the UPDATE in that case.
        existing_rows = list(
            geocache.logs.values_list(
                "source_id", "text", "logged_date", "user_name", "log_type",
            )
        )
        existing_text_by_sid = {sid: text for sid, text, *_ in existing_rows if sid}
        existing_date_user_type = {(ld, u, t) for _, _, ld, u, t in existing_rows}

        new_logs = []
        for log_data in logs:
            sid = log_data.get("source_id", "")
            if not sid:
                continue
            if sid in existing_text_by_sid:
                fresh_text = log_data.get("text", "")
                if existing_text_by_sid[sid] != fresh_text:
                    Log.objects.filter(
                        geocache=geocache, source_id=sid,
                    ).update(text=fresh_text)
                continue
            # Fallback: same date + user_name + type already exists?
            from datetime import date as _date
            ld = log_data.get("logged_date")
            if isinstance(ld, str):
                try:
                    y, m, d = ld.split("-")
                    ld = _date(int(y), int(m), int(d))
                except (ValueError, AttributeError):
                    ld = None
            user_name = log_data.get("user_name", "")
            ltype = log_data.get("log_type", "")
            if (ld, user_name, ltype) in existing_date_user_type:
                continue
            new_logs.append(Log(geocache=geocache, **log_data))
            # Track the new log to prevent duplicates within the same batch
            existing_date_user_type.add((ld, user_name, ltype))
        if new_logs:
            Log.objects.bulk_create(new_logs)

    # 9. Waypoints (upsert by lookup) — pre-fetch existing rows once and
    # update in place, so we issue at most 1 SELECT + N targeted writes per
    # cache (was: 2-3 queries per waypoint).
    if waypoints:
        from geocaches.models import Waypoint
        existing_wps = {wp.lookup: wp for wp in geocache.waypoints.all()} if not created else {}
        for wp_data in waypoints:
            wp_copy = dict(wp_data)
            lookup = wp_copy.pop("lookup")
            existing = existing_wps.get(lookup)
            if existing is None:
                Waypoint.objects.create(geocache=geocache, lookup=lookup, **wp_copy)
                continue
            # Don't overwrite fields on waypoints the user has manually edited
            if existing.is_user_modified:
                continue
            changed_fields = []
            for k, v in wp_copy.items():
                if getattr(existing, k) != v:
                    setattr(existing, k, v)
                    changed_fields.append(k)
            if changed_fields:
                existing.save(update_fields=changed_fields)

    # 10. Corrected coordinates
    if corrected_coords:
        from geocaches.services.coords import set_corrected_coords
        if set_corrected_coords(
            geocache,
            corrected_coords.get("latitude"),
            corrected_coords.get("longitude"),
            note=corrected_coords.get("note"),
        ):
            # Corrected coordinates win over the listed ones, so the refresh
            # they just triggered already covers any listed-coordinate change.
            coords_before = (geocache.latitude, geocache.longitude)

    # 11. Images (dedup by URL)
    if images:
        from geocaches.models import Image
        existing_urls = set(geocache.images.values_list("url", flat=True))
        new_images = [
            Image(geocache=geocache, **img)
            for img in images if img["url"] not in existing_urls
        ]
        if new_images:
            Image.objects.bulk_create(new_images)

    # Cache-side image caching (Image rows, background_image_url, inline <img>
    # in descriptions and log text) is intentionally NOT pre-warmed here.
    # Imports — especially Pocket Queries — would otherwise download thousands
    # of images on every run. The lazy proxy in image_cache.serve_proxy fills
    # on first render; bulk fill is available via 'Download missing offline
    # images (filtered caches)' on the cache list. TB + ALC syncs prefetch
    # eagerly because they're small and explicitly user-initiated.

    # 12. Notes (conditionally)
    if notes:
        from geocaches.models import Note
        if not skip_notes_if_exist or not geocache.notes.exists():
            for note_data in notes:
                Note.objects.create(geocache=geocache, **note_data)

    # 13. OC extension (req_passwd etc. from OKAPI sync; never overwrites passphrase)
    if oc_ext:
        from geocaches.models import OCExtension
        _OC_EXT_KEYS = ("req_passwd", "trip_time", "trip_distance",
                        "attribution_html", "long_description", "short_description",
                        "needs_maintenance", "user_recommended", "related_gc_code")
        ext_fields = {k: v for k, v in oc_ext.items() if k in _OC_EXT_KEYS}
        if ext_fields:
            OCExtension.objects.update_or_create(geocache=geocache, defaults=ext_fields)

    # Trackable mentions — GC-only. Upsert + prune so a refresh removes
    # TBs that have left the cache.
    if trackable_mentions is not None and update_source in ("gc", ""):
        from geocaches.models import CacheTrackableMention
        seen: set[str] = set()
        for m in trackable_mentions:
            ref = (m.get("ref_code") or "").strip()
            if not ref:
                continue
            seen.add(ref)
            CacheTrackableMention.objects.update_or_create(
                geocache=geocache,
                ref_code=ref,
                defaults={
                    "gc_id": m.get("gc_id"),
                    "name":  m.get("name") or "",
                },
            )
        # Skip the prune when there can't be any stale rows: newly-created
        # caches have no existing mentions, and unchanged-empty caches (no TBs
        # before, no TBs now) have nothing to delete either.
        if not created and (seen or had_trackables_before):
            CacheTrackableMention.objects.filter(geocache=geocache).exclude(ref_code__in=seen).delete()

    # Record auto-link when OC data explicitly references a GC code
    if gc_code and oc_code and update_source.startswith("oc"):
        _record_auto_link(gc_code, oc_code)

    if created:
        invalidate_platform_availability()
        invalidate_filter_dialog_options()
    elif (geocache.latitude, geocache.longitude) != coords_before:
        from geocaches.services.coords import coords_changed
        coords_changed(geocache.pk)

    return SaveResult(
        geocache=geocache,
        created=created,
        updated=not created,
        locked=False,
        merged_from=merged_from,
    )


# ---------------------------------------------------------------------------
# Coordinate proximity matching for dual-listed caches
# ---------------------------------------------------------------------------

def _find_proximity_match(new_cache, lat, lon):
    """Find an existing GC cache within ~15m of the given OC cache coordinates."""
    from geocaches.models import Geocache

    candidates = Geocache.objects.filter(
        gc_code__startswith="GC",
        oc_code="",
        latitude__range=(lat - _PROXIMITY_DEG, lat + _PROXIMITY_DEG),
        longitude__range=(lon - _PROXIMITY_DEG, lon + _PROXIMITY_DEG),
    ).exclude(pk=new_cache.pk)

    for c in candidates[:5]:
        # Haversine check for accuracy (the box filter is just a rough pre-filter)
        from geocaches.geo import haversine_km
        dist = haversine_km(lat, lon, c.latitude, c.longitude)
        if dist is not None and dist < 0.015:  # 15m
            return c
    return None


def _apply_extra_codes(geocache, codes):
    """Write the codes held back in step 2a onto *geocache*.

    Returns the code of a record that was folded in, or "".  Live codes are
    unique, so a code another live record already carries cannot simply be
    written:

      * an OC-only record whose code the incoming GC-listed cache claims is the
        same physical cache — the owner said so in the listing — so it is
        merged in, exactly as Tools → Duplicate Caches would do it;
      * anything else is left alone and logged.  The two records stay separate
        and the user resolves them by hand.
    """
    from geocaches.services.codes import live_code_conflict

    merged_from = ""
    changed = []
    for code_field, value in codes.items():
        if getattr(geocache, code_field) == value:
            continue
        other = live_code_conflict(code_field, value, exclude_pk=geocache.pk)
        if other is None:
            setattr(geocache, code_field, value)
            changed.append(code_field)
            continue
        if code_field == "oc_code" and geocache.gc_code and not other.gc_code and not other.al_code:
            _merge_logger.info(
                "Auto-merge: %s references %s — folding the standalone OC record in",
                geocache.gc_code, value,
            )
            _merge_into(source=other, dest=geocache, oc_code=value)
            merged_from = value
            continue
        _merge_logger.warning(
            "%s already belongs to cache #%s — not written onto %s "
            "(use Tools → Duplicate Caches to resolve)",
            value, other.pk, geocache.display_code,
        )
    if changed:
        geocache.save(update_fields=changed)
    return merged_from
