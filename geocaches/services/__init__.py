import logging
import re
import sqlite3
import configparser
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

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
    merged_from = ""
    if geocache is None:
        if gc_code:
            Geocache.all_objects.filter(gc_code=gc_code, deleted_at__isnull=False).delete()
            geocache, created = Geocache.objects.get_or_create(
                gc_code=gc_code, defaults=fields
            )
        elif al_code:
            Geocache.all_objects.filter(al_code=al_code, deleted_at__isnull=False).delete()
            geocache, created = Geocache.objects.get_or_create(
                al_code=al_code, defaults=fields
            )
        elif oc_code:
            Geocache.all_objects.filter(oc_code=oc_code, deleted_at__isnull=False).delete()
            geocache, created = Geocache.objects.get_or_create(
                oc_code=oc_code, defaults=fields
            )
        else:
            raise ValueError("At least one of gc_code, oc_code, al_code, or al_stage_uuid required")

    # 2b. Proximity duplicate detection for newly created OC caches.
    #     Auto-merge only happens via explicit cross-reference (gc_code from
    #     OC data, handled in step 2 above).  Proximity matches are logged
    #     so the user can review them via Tools → Duplicate Caches.
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
        from geocaches.models import CorrectedCoordinates
        CorrectedCoordinates.objects.update_or_create(
            geocache=geocache, defaults=corrected_coords
        )
        if not geocache.has_corrected_coordinates:
            geocache.has_corrected_coordinates = True
            geocache.save(update_fields=["has_corrected_coordinates"])

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

    return SaveResult(
        geocache=geocache,
        created=created,
        updated=not created,
        locked=False,
        merged_from=merged_from,
    )


# ---------------------------------------------------------------------------
# Fusion record helpers (canonical home: services.fusion)
# ---------------------------------------------------------------------------

from .fusion import _record_auto_link, set_fusion_decision  # noqa: E402,F401


# ---------------------------------------------------------------------------
# Coordinate proximity matching for dual-listed caches
# ---------------------------------------------------------------------------

_PROXIMITY_DEG = 0.00015  # ~15m at mid-latitudes


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


from .dedup import _merge_into  # noqa: E402,F401 — re-export for back-compat


# ---------------------------------------------------------------------------
# Duplicate detection (post-import scan)
# ---------------------------------------------------------------------------

def find_potential_duplicates(include_dont_fuse: bool = False):
    """Find GC caches that likely have a separate OC record (same coordinates).

    Returns a list of dicts enriched with fusion decision data.
    Pairs marked 'dont_fuse' are excluded unless include_dont_fuse=True.
    """
    from geocaches.models import Geocache, CacheFusionRecord
    from geocaches.geo import haversine_km

    # Get all OC-only caches (have oc_code, no gc_code)
    oc_caches = list(
        Geocache.objects.filter(oc_code__gt="", gc_code="")
        .values_list("pk", "oc_code", "name", "owner", "latitude", "longitude")
    )
    if not oc_caches:
        return []

    # Build a rough lat/lon index of GC-only caches (have gc_code, no oc_code)
    # Exclude Adventure Lab caches (LC prefix) — they must never be fused
    gc_caches = list(
        Geocache.objects.filter(gc_code__startswith="GC", oc_code="")
        .values_list("pk", "gc_code", "name", "owner", "latitude", "longitude")
    )
    if not gc_caches:
        return []

    raw_duplicates = []
    for oc_pk, oc_code, oc_name, oc_owner, oc_lat, oc_lon in oc_caches:
        for gc_pk, gc_code, gc_name, gc_owner, gc_lat, gc_lon in gc_caches:
            if abs(oc_lat - gc_lat) > _PROXIMITY_DEG or abs(oc_lon - gc_lon) > _PROXIMITY_DEG:
                continue
            dist = haversine_km(oc_lat, oc_lon, gc_lat, gc_lon)
            if dist < 0.015:
                raw_duplicates.append({
                    "gc_pk": gc_pk, "gc_code": gc_code, "gc_name": gc_name, "gc_owner": gc_owner,
                    "oc_pk": oc_pk, "oc_code": oc_code, "oc_name": oc_name, "oc_owner": oc_owner,
                    "distance_m": round(dist * 1000, 1),
                })
                break  # one match per OC cache is enough

    if not raw_duplicates:
        return []

    # Enrich with fusion decision data
    fusion_map = {
        (r.gc_code, r.oc_code): r
        for r in CacheFusionRecord.objects.filter(
            gc_code__in=[d["gc_code"] for d in raw_duplicates],
            oc_code__in=[d["oc_code"] for d in raw_duplicates],
        )
    }

    result = []
    for d in raw_duplicates:
        rec = fusion_map.get((d["gc_code"], d["oc_code"]))
        d["user_decision"] = rec.user_decision if rec else None
        d["auto_linked"] = rec.auto_linked if rec else False
        if not include_dont_fuse and d["user_decision"] == "dont_fuse":
            continue
        result.append(d)

    return result


def merge_duplicate(gc_pk, oc_pk):
    """Merge an OC-only cache into a GC cache by pk. Returns a description string."""
    from geocaches.models import Geocache

    gc_cache = Geocache.objects.get(pk=gc_pk)
    oc_cache = Geocache.objects.get(pk=oc_pk)

    oc_code = oc_cache.oc_code
    gc_code = gc_cache.gc_code

    _merge_into(source=oc_cache, dest=gc_cache, oc_code=oc_code)
    _merge_logger.info("Manual merge: %s + %s → %s (dual-listed)", gc_code, oc_code, gc_code)
    set_fusion_decision(gc_code, oc_code, "fuse")
    return f"{gc_code} + {oc_code} merged"


def _start_auto_enrich(since):
    from preferences.models import UserPreference
    if not UserPreference.get("enrich_auto", True):
        return
    fields = set()
    if UserPreference.get("enrich_elevation", True):
        fields.add("elevation")
    if UserPreference.get("enrich_location", True):
        fields.add("location")
    if not fields:
        return

    from geocaches.models import Geocache
    ids = list(
        Geocache.objects.filter(last_gpx_date__gte=since).values_list("id", flat=True)
    )
    if not ids:
        return

    from geocaches.tasks.enrich import start_enrichment
    start_enrichment(Geocache.objects.filter(id__in=ids), fields)


def import_and_enrich(source_type, path, tag_names, auto_enrich=True, wpts_path=None):
    from datetime import datetime, timezone
    since = datetime.now(timezone.utc)

    if source_type == "unified_gpx":
        from geocaches.importers import import_gpx
        result = import_gpx(path, wpts_path=wpts_path, tag_names=tag_names)
    elif source_type == "gpx":
        from geocaches.importers import import_gc_gpx
        result = import_gc_gpx(path, wpts_path=wpts_path, tag_names=tag_names)
    elif source_type == "oc_gpx":
        from geocaches.importers import import_oc_gpx
        result = import_oc_gpx(path, tag_names=tag_names)
    elif source_type == "gsak":
        from geocaches.importers.gsak import import_gsak_db
        result = import_gsak_db(path, tag_names=tag_names)
    elif source_type == "lab2gpx":
        from geocaches.importers.lab2gpx import import_lab2gpx
        result = import_lab2gpx(path, tag_names=tag_names)
    else:
        raise ValueError(f"Unknown source_type: {source_type!r}")

    if auto_enrich and result:
        _start_auto_enrich(since)

    # Invalidate distance cache so it is recomputed on next request
    # (new/updated caches may have changed distances).
    if result:
        from geocaches.geo.distance_cache import invalidate
        invalidate()

    return result


def export_caches(queryset, format="gpx", username="", opts=None):
    from geocaches.exporters.gpx_gc import export_gpx
    return export_gpx(queryset, gc_username=username, opts=opts)


def parse_and_import_gsak_locations(gsak_path):
    from geocaches.geo.coords import parse_coordinate
    from preferences.models import ReferencePoint

    GSAK_DIR = Path(gsak_path)
    GSAK_DB = GSAK_DIR / "gsak.db3"

    def _parse_gsak_line(line):
        line = line.strip()
        if not line or line.startswith('#'):
            return None
        comma_idx = line.find(',')
        if comma_idx < 0:
            return None
        name = line[:comma_idx].strip()
        coord_part = line[comma_idx + 1:].strip()
        if not name or not coord_part:
            return None
        m = re.search(r'\s+([EWew]\s*\d)', coord_part)
        if m:
            lat = parse_coordinate(coord_part[:m.start()].strip())
            lon = parse_coordinate(coord_part[m.start():].strip())
            if lat is not None and lon is not None and -90 <= lat <= 90 and -180 <= lon <= 180:
                return name, lat, lon
        tokens = [t for t in re.split(r'[,\s]+', coord_part) if t]
        if len(tokens) == 2:
            lat = parse_coordinate(tokens[0])
            lon = parse_coordinate(tokens[1])
            if lat is not None and lon is not None and -90 <= lat <= 90 and -180 <= lon <= 180:
                return name, lat, lon
        return None

    candidates = []
    errors = []

    if GSAK_DB.exists():
        try:
            conn = sqlite3.connect(str(GSAK_DB))
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT Data FROM Settings WHERE Type='LO' AND Description='Locations'"
            ).fetchone()
            conn.close()
            if row and row["Data"]:
                for line in row["Data"].splitlines():
                    parsed = _parse_gsak_line(line)
                    if parsed:
                        candidates.append({"name": parsed[0], "lat": parsed[1], "lon": parsed[2], "source": "GSAK Locations"})
        except Exception as exc:
            errors.append(f"Could not read GSAK Locations: {exc}")
    else:
        errors.append(f"GSAK database not found at {GSAK_DB}")

    FSG_DB = GSAK_DIR / "Macros" / "FoundStatsSQLLite.db3"
    if FSG_DB.exists():
        try:
            conn = sqlite3.connect(str(FSG_DB))
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT hdate, hlat, hlon FROM Home WHERE hsettings=1 ORDER BY hdate"
            ).fetchall()
            conn.close()
            for row in rows:
                try:
                    lat = float(row["hlat"])
                    lon = float(row["hlon"])
                except (ValueError, TypeError):
                    continue
                if not (-90 <= lat <= 90 and -180 <= lon <= 180):
                    continue
                hdate = str(row["hdate"])[:10]
                candidates.append({
                    "name": f"Home (from {hdate})",
                    "lat": lat, "lon": lon,
                    "source": "FindStatGen home history",
                    "valid_from": hdate,
                    "is_home": True,
                })
        except Exception as exc:
            errors.append(f"Could not read FindStatGen home history: {exc}")

    data_dir = GSAK_DIR / "data"
    if data_dir.exists():
        for db_dir in sorted(data_dir.iterdir()):
            ini_path = db_dir / "settings.ini"
            if not ini_path.exists():
                continue
            try:
                cfg = configparser.ConfigParser(strict=False)
                cfg.read(str(ini_path), encoding="cp1252")
                lat_str = cfg.get("General", "CentreLat", fallback="").strip()
                lon_str = cfg.get("General", "CentreLon", fallback="").strip()
                name_str = cfg.get("General", "CentreDes", fallback="").strip() or db_dir.name
                if lat_str and lon_str:
                    lat = parse_coordinate(lat_str)
                    lon = parse_coordinate(lon_str)
                    if lat is not None and lon is not None and -90 <= lat <= 90 and -180 <= lon <= 180:
                        candidates.append({
                            "name": name_str,
                            "lat": lat, "lon": lon,
                            "source": f"DB centre: {db_dir.name}",
                        })
            except Exception as exc:
                errors.append(f"Could not read {ini_path}: {exc}")

    seen = set()
    unique_candidates = []
    for c in candidates:
        key = (c["name"].lower(), round(c["lat"], 4), round(c["lon"], 4))
        if key not in seen:
            seen.add(key)
            unique_candidates.append(c)

    existing = list(ReferencePoint.objects.all())
    existing_keys = {(rp.name.lower(), round(rp.latitude, 4), round(rp.longitude, 4)) for rp in existing}
    existing_names = {rp.name.lower() for rp in existing}

    for c in unique_candidates:
        c_key = (c["name"].lower(), round(c["lat"], 4), round(c["lon"], 4))
        c["already_exists"] = c_key in existing_keys or c["name"].lower() in existing_names

    return unique_candidates, errors, existing


def import_gsak_location_candidates(selected_candidates):
    """
    Create ReferencePoint objects for a list of pre-selected location candidates.

    Args:
        selected_candidates: list of candidate dicts as returned by
                             parse_and_import_gsak_locations, already filtered
                             to only those the user chose to import.

    Returns:
        List of names of the created ReferencePoint objects.
    """
    from preferences.models import ReferencePoint

    imported = []
    for c in selected_candidates:
        ReferencePoint.objects.create(
            name=c["name"],
            latitude=c["lat"],
            longitude=c["lon"],
            note=c["source"],
            valid_from=c.get("valid_from"),
            is_home=c.get("is_home", False),
        )
        imported.append(c["name"])
    return imported


def manage_tags(action, tag_name=None, queryset=None, new_name=None, tag_id=None, rp_id=None, propagate_alc=False):
    from geocaches.models import Tag, Geocache

    if action == "rename":
        if tag_id and new_name:
            old_tag = Tag.objects.filter(id=tag_id).first()
            if old_tag:
                target = Tag.objects.filter(name=new_name).first()
                if target is None:
                    # Simple rename: keep caches and center point in place.
                    old_tag.name = new_name
                    old_tag.save(update_fields=["name"])
                elif target.id != old_tag.id:
                    # Fuse into the existing target tag: migrate caches over.
                    Through = Geocache.tags.through
                    cache_ids = list(old_tag.geocaches.values_list("id", flat=True))
                    Through.objects.bulk_create(
                        [Through(geocache_id=cid, tag_id=target.id) for cid in cache_ids],
                        ignore_conflicts=True,
                    )
                    # Keep the target's center point; only inherit the old tag's
                    # when the target has none.
                    if target.default_ref_point_id is None and old_tag.default_ref_point_id is not None:
                        target.default_ref_point_id = old_tag.default_ref_point_id
                        target.save(update_fields=["default_ref_point"])
                    old_tag.geocaches.clear()
                    old_tag.delete()
        return 0

    elif action == "delete":
        if tag_id:
            Tag.objects.filter(id=tag_id).delete()
        return 0

    elif action == "set_tag_refpoint":
        if tag_id:
            tag = Tag.objects.filter(id=tag_id).first()
            if tag:
                tag.default_ref_point_id = int(rp_id) if rp_id else None
                tag.save(update_fields=["default_ref_point"])
        return 0

    elif action == "bulk_add":
        if tag_name and queryset is not None:
            tag, _ = Tag.objects.get_or_create(name=tag_name)
            cache_ids = set(queryset.values_list("id", flat=True))
            if propagate_alc:
                adv_ids = list(
                    queryset.filter(adventure__isnull=False, al_detail__isnull=True)
                    .values_list("adventure_id", flat=True)
                )
                if adv_ids:
                    child_ids = Geocache.objects.filter(
                        adventure_id__in=adv_ids, al_detail__isnull=False
                    ).values_list("id", flat=True)
                    cache_ids |= set(child_ids)
            Through = Geocache.tags.through
            Through.objects.bulk_create(
                [Through(geocache_id=cid, tag_id=tag.id) for cid in cache_ids],
                ignore_conflicts=True,
            )
            return len(cache_ids)
        return 0

    elif action == "bulk_remove":
        if tag_id and queryset is not None:
            if propagate_alc:
                adv_ids = list(
                    queryset.filter(adventure__isnull=False, al_detail__isnull=True)
                    .values_list("adventure_id", flat=True)
                )
                if adv_ids:
                    child_ids = set(
                        Geocache.objects.filter(
                            adventure_id__in=adv_ids, al_detail__isnull=False
                        ).values_list("id", flat=True)
                    )
                    all_ids = set(queryset.values_list("id", flat=True)) | child_ids
                    count = Geocache.tags.through.objects.filter(
                        geocache_id__in=all_ids, tag_id=tag_id
                    ).delete()[0]
                    return count
            count = Geocache.tags.through.objects.filter(
                geocache__in=queryset, tag_id=tag_id
            ).delete()[0]
            return count
        return 0

    return 0
