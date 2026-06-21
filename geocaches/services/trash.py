"""Soft-delete (Trash) helpers with Adventure Lab parent → stage cascade.

Hard deletes cascade from an AL parent to its stages via the
``cascade_al_parent_to_stages`` signal (the relationship runs through the shared
Adventure record, so Django's FK cascade doesn't cover it).  Soft-deletes set
``deleted_at`` via ``save()`` and never reach that signal, so the cascade is
reproduced explicitly here.
"""

from django.utils.timezone import now

from geocaches.services.adventures import is_al_parent


def _stage_qs(cache):
    """All stage geocaches (incl. trashed) belonging to *cache*'s adventure."""
    from geocaches.models import Geocache

    return Geocache.all_objects.filter(
        adventure_id=cache.adventure_id, al_detail__isnull=False
    )


def trash_cache(cache):
    """Move *cache* to Trash; cascade to its stages when it's an AL parent.

    Returns the ``deleted_at`` timestamp applied.
    """
    stamp = now()
    cache.deleted_at = stamp
    cache.save(update_fields=["deleted_at"])
    if cache.adventure_id is not None and is_al_parent(cache):
        _stage_qs(cache).filter(deleted_at__isnull=True).update(deleted_at=stamp)
    return stamp


def restore_cache(cache):
    """Restore *cache* from Trash; cascade-restore its stages for AL parents."""
    cache.deleted_at = None
    cache.save(update_fields=["deleted_at"])
    if cache.adventure_id is not None and is_al_parent(cache):
        _stage_qs(cache).filter(deleted_at__isnull=False).update(deleted_at=None)


def stage_pks_for_parents(pk_list):
    """Live stage pks to add when bulk-trashing *pk_list*.

    Any AL parent in *pk_list* drags its (still-live) stages along, except those
    already present in *pk_list*.  Returns a list of extra pks to soft-delete.
    """
    from geocaches.models import Geocache

    adventure_ids = list(
        Geocache.objects.filter(
            pk__in=pk_list, adventure_id__isnull=False, al_detail__isnull=True
        ).values_list("adventure_id", flat=True)
    )
    if not adventure_ids:
        return []
    return list(
        Geocache.objects.filter(
            adventure_id__in=adventure_ids, al_detail__isnull=False
        )
        .exclude(pk__in=pk_list)
        .values_list("pk", flat=True)
    )
