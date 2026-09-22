"""The single funnel for geocache coordinate writes.

Anything that moves a cache — its listed ``latitude``/``longitude`` or its
:class:`~geocaches.models.CorrectedCoordinates` row — must go through here, so
the pre-computed ``DistanceCache`` rows cannot silently go stale
(docs/architecture-review-2026-09.md §4.2 item 2).

Callers: ``geocaches.views.notes_logs.corrected_coords_save`` (the detail
page's corrected-coordinates form) and ``services._save_geocache_inner``, which
every importer, the GC/OC API sync, map sync, the refresh task and the ALC save
funnel through already.
"""


def set_corrected_coords(geocache, latitude, longitude, note=None):
    """Store corrected coordinates for *geocache*; returns True if it moved.

    *note* of ``None`` leaves any existing note alone (importers don't carry
    one).  Writes only what changed, so re-importing the same data costs one
    SELECT and leaves the distance rows alone.
    """
    from geocaches.models import CorrectedCoordinates

    existing = CorrectedCoordinates.objects.filter(geocache=geocache).first()
    if existing is None:
        CorrectedCoordinates.objects.create(
            geocache=geocache, latitude=latitude, longitude=longitude,
            note=note or "",
        )
        moved = True
    else:
        moved = (existing.latitude, existing.longitude) != (latitude, longitude)
        note_changed = note is not None and existing.note != note
        if moved or note_changed:
            existing.latitude = latitude
            existing.longitude = longitude
            if note is not None:
                existing.note = note
            existing.save(update_fields=["latitude", "longitude", "note", "updated_at"])
    if not geocache.has_corrected_coordinates:
        geocache.has_corrected_coordinates = True
        geocache.save(update_fields=["has_corrected_coordinates"])
    if moved:
        coords_changed(geocache.pk)
    return moved


def clear_corrected_coords(geocache):
    """Drop the corrected coordinates of *geocache*; returns True if it moved."""
    from geocaches.models import CorrectedCoordinates

    deleted, _ = CorrectedCoordinates.objects.filter(geocache=geocache).delete()
    if geocache.has_corrected_coordinates:
        geocache.has_corrected_coordinates = False
        geocache.save(update_fields=["has_corrected_coordinates"])
    if deleted:
        coords_changed(geocache.pk)
    return bool(deleted)


def coords_changed(geocache_id):
    """Recompute the cached distance/bearing rows of one cache."""
    from geocaches.geo.distance_cache import update_for_cache

    return update_for_cache(geocache_id)
