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
    "hidden_date", "country", "iso_country_code", "state",
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
    # --- Options ---
    skip_notes_if_exist: bool = True,
    update_source: str = "",
) -> SaveResult:
    """
    Canonical function to persist a single geocache and its related objects.

    Lookup order: al_stage_uuid -> gc_code -> al_code -> oc_code.
    Found status is only promoted, never demoted.
    Import-locked caches skip ALL updates.

    Source precedence (update_source):
      - "oc": if the cache already has GC data (gc_code set, primary_source
        is not OC), shared fields (name, descriptions, etc.) are preserved
        from the GC import.  OC-only fields (oc_code, logs, attributes,
        waypoints) are still added.
      - "gc" or "": always update shared fields (default behaviour).
    """
    from geocaches.models import Geocache

    geocache = None
    created = False

    # 1. UUID lookup (Adventure Lab stages)
    if al_stage_uuid:
        geocache = Geocache.objects.filter(al_stage_uuid=al_stage_uuid).first()

    # 2. Code lookup
    merged_from = ""
    if geocache is None:
        if gc_code:
            geocache, created = Geocache.objects.get_or_create(
                gc_code=gc_code, defaults=fields
            )
        elif al_code:
            geocache, created = Geocache.objects.get_or_create(
                al_code=al_code, defaults=fields
            )
        elif oc_code:
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

    # 4. Update fields (if not created and not locked)
    #    Source precedence: OC must not overwrite GC-owned shared fields
    #    when the cache already has GC data.
    if not created:
        skip_shared = (
            update_source == "oc"
            and geocache.gc_code          # cache already has GC data
            and geocache.primary_source not in ("", "oc_de")  # GC is primary
        )
        for key, value in fields.items():
            if skip_shared and key in _GC_OWNED_FIELDS:
                continue
            setattr(geocache, key, value)
        if geocache.is_placeholder:
            geocache.is_placeholder = False
        geocache.save()

    # 5. Found promotion (never demotes)
    if found is True and not geocache.found:
        geocache.found = True
        if found_date:
            geocache.found_date = found_date
        geocache.save(update_fields=["found", "found_date"])
    elif geocache.found and not geocache.found_date and found_date:
        # Backfill found_date if missing
        geocache.found_date = found_date
        geocache.save(update_fields=["found_date"])

    # 6. Tags
    if tags:
        geocache.tags.add(*tags)

    # 7. Attributes
    if attributes:
        from geocaches.models import Attribute
        attr_objs = []
        for a in attributes:
            obj, _ = Attribute.objects.get_or_create(
                source=a["source"],
                attribute_id=a["attribute_id"],
                is_positive=a["is_positive"],
                defaults={"name": a.get("name", f"Attribute #{a['attribute_id']}")},
            )
            attr_objs.append(obj)
        geocache.attributes.add(*attr_objs)

    # 8. Logs (dedup by source_id, then by date+user_name+type as fallback)
    if logs:
        from geocaches.models import Log
        existing_source_ids = set(
            geocache.logs.values_list("source_id", flat=True)
        )
        # Fallback dedup: (date, user_name, log_type) — catches cases where the
        # same log has different source_id formats (GPX numeric vs API ref code)
        existing_date_user_type = set(
            geocache.logs.values_list("logged_date", "user_name", "log_type")
        )

        new_logs = []
        for log_data in logs:
            sid = log_data.get("source_id", "")
            if not sid:
                continue
            if sid in existing_source_ids:
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

    # 9. Waypoints (update_or_create by lookup) — copy to avoid mutating caller's data
    if waypoints:
        from geocaches.models import Waypoint
        for wp_data in waypoints:
            wp_copy = dict(wp_data)
            lookup = wp_copy.pop("lookup")
            # Don't overwrite fields on waypoints the user has manually edited
            existing = geocache.waypoints.filter(lookup=lookup).first()
            if existing and existing.is_user_modified:
                continue
            Waypoint.objects.update_or_create(
                geocache=geocache, lookup=lookup, defaults=wp_copy
            )

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
# Fusion record helpers
# ---------------------------------------------------------------------------

def _record_auto_link(gc_code: str, oc_code: str) -> None:
    """Ensure a CacheFusionRecord exists marking this pair as owner-confirmed."""
    from geocaches.models import CacheFusionRecord
    CacheFusionRecord.objects.update_or_create(
        gc_code=gc_code,
        oc_code=oc_code,
        defaults={"auto_linked": True},
    )


def set_fusion_decision(gc_code: str, oc_code: str, decision) -> None:
    """Create or update the user decision for a GC/OC pair (fuse/dont_fuse/postpone/None)."""
    from geocaches.models import CacheFusionRecord
    CacheFusionRecord.objects.update_or_create(
        gc_code=gc_code,
        oc_code=oc_code,
        defaults={"user_decision": decision},
    )


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
        from gcforge.settings import _haversine_km
        dist = _haversine_km(lat, lon, c.latitude, c.longitude)
        if dist is not None and dist < 0.015:  # 15m
            return c
    return None


def _merge_into(*, source, dest, oc_code):
    """Merge a newly created OC cache record into an existing GC cache.

    Sets oc_code on dest, moves related objects, then deletes source.
    """
    from geocaches.models import OCExtension

    # Set oc_code on the destination
    dest.oc_code = oc_code
    update_fields = ["oc_code"]

    # Fill empty fields from the OC source
    fill_fields = [
        "country", "iso_country_code", "state", "county", "elevation",
    ]
    for f in fill_fields:
        src_val = getattr(source, f, None)
        dst_val = getattr(dest, f, None)
        if src_val and not dst_val:
            setattr(dest, f, src_val)
            update_fields.append(f)

    dest.save(update_fields=update_fields)

    # Move logs
    for log in source.logs.all():
        exists = dest.logs.filter(
            logged_date=log.logged_date, user_name=log.user_name, log_type=log.log_type,
        ).exists()
        if not exists:
            log.geocache = dest
            log.save(update_fields=["geocache"])

    # Move waypoints
    for wp in source.waypoints.all():
        if not dest.waypoints.filter(lookup=wp.lookup).exists():
            wp.geocache = dest
            wp.save(update_fields=["geocache"])

    # Move notes, images
    for note in source.notes.all():
        note.geocache = dest
        note.save(update_fields=["geocache"])
    for img in source.images.all():
        if not dest.images.filter(url=img.url).exists():
            img.geocache = dest
            img.save(update_fields=["geocache"])

    # Move tags
    for tag in source.tags.all():
        dest.tags.add(tag)

    # Move OC extension if it exists
    try:
        oc_ext = source.oc_extension
        oc_ext.geocache = dest
        oc_ext.save(update_fields=["geocache"])
    except OCExtension.DoesNotExist:
        pass

    source.delete()


# ---------------------------------------------------------------------------
# Duplicate detection (post-import scan)
# ---------------------------------------------------------------------------

def find_potential_duplicates(include_dont_fuse: bool = False):
    """Find GC caches that likely have a separate OC record (same coordinates).

    Returns a list of dicts enriched with fusion decision data.
    Pairs marked 'dont_fuse' are excluded unless include_dont_fuse=True.
    """
    from geocaches.models import Geocache, CacheFusionRecord
    from gcforge.settings import _haversine_km

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
            dist = _haversine_km(oc_lat, oc_lon, gc_lat, gc_lon)
            if dist is not None and dist < 0.015:
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

    from geocaches.enrich_task import start_enrichment
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
        from geocaches.distance_cache import invalidate
        invalidate()

    return result


def export_caches(queryset, format="gpx", username="", opts=None):
    from geocaches.exporters.gpx_gc import export_gpx
    return export_gpx(queryset, gc_username=username, opts=opts)


def parse_and_import_gsak_locations(gsak_path):
    from geocaches.coords import parse_coordinate
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


def manage_tags(action, tag_name=None, queryset=None, new_name=None, tag_id=None, rp_id=None):
    from geocaches.models import Tag, Geocache

    if action == "rename":
        if tag_id and new_name:
            old_tag = Tag.objects.filter(id=tag_id).first()
            if old_tag:
                target, created = Tag.objects.get_or_create(name=new_name)
                if not created:
                    for cache in old_tag.geocaches.all():
                        cache.tags.add(target)
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
            cache_ids = list(queryset.values_list("id", flat=True))
            Through = Geocache.tags.through
            Through.objects.bulk_create(
                [Through(geocache_id=cid, tag_id=tag.id) for cid in cache_ids],
                ignore_conflicts=True,
            )
            return len(cache_ids)
        return 0

    elif action == "bulk_remove":
        if tag_id and queryset is not None:
            count = Geocache.tags.through.objects.filter(
                geocache__in=queryset, tag_id=tag_id
            ).delete()[0]
            return count
        return 0

    return 0
