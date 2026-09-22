"""Deduplication / merge services.

Houses the canonical `_merge_into` function used when two Geocache rows
represent the same physical cache (typically a GC and an OC record) and
must be consolidated into one, plus the post-import duplicate scan behind
Tools → Duplicate Caches.
"""
import logging

from .fusion import set_fusion_decision

_merge_logger = logging.getLogger("geocaches.import")

_PROXIMITY_DEG = 0.00015  # ~15m at mid-latitudes


def _merge_into(*, source, dest, oc_code):
    """Merge a newly created OC cache record into an existing GC cache.

    Moves related objects, deletes source, and only then writes oc_code onto
    dest: ``uniq_live_oc_code`` forbids the two rows carrying the same code
    even for the moment in between (see geocaches.services.codes).
    """
    from geocaches.db_lock import db_write_atomic
    from geocaches.models import OCExtension
    with db_write_atomic():
        # Move logs (dedup by date/user/type)
        for log in source.logs.all():
            exists = dest.logs.filter(
                logged_date=log.logged_date, user_name=log.user_name, log_type=log.log_type,
            ).exists()
            if not exists:
                log.geocache = dest
                log.save(update_fields=["geocache"])

        # Move waypoints (dedup by lookup code)
        for wp in source.waypoints.all():
            if not dest.waypoints.filter(lookup=wp.lookup).exists():
                wp.geocache = dest
                wp.save(update_fields=["geocache"])

        # Move notes (no dedup — both sides kept)
        for note in source.notes.all():
            note.geocache = dest
            note.save(update_fields=["geocache"])

        # Move images (dedup by url)
        for img in source.images.all():
            if not dest.images.filter(url=img.url).exists():
                img.geocache = dest
                img.save(update_fields=["geocache"])

        # Merge tags
        for tag in source.tags.all():
            dest.tags.add(tag)

        # Move OC extension if the source carries one; delete dest's first if needed
        try:
            oc_ext = source.oc_extension
            try:
                dest.oc_extension.delete()
            except OCExtension.DoesNotExist:
                pass
            oc_ext.geocache = dest
            oc_ext.save(update_fields=["geocache"])
        except OCExtension.DoesNotExist:
            pass

        source.delete()

        dest.oc_code = oc_code
        update_fields = ["oc_code"]

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
