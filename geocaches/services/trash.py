"""Soft-delete (Trash) helpers with Adventure Lab parent → stage cascade.

Hard deletes cascade from an AL parent to its stages via the
``cascade_al_parent_to_stages`` signal (the relationship runs through the shared
Adventure record, so Django's FK cascade doesn't cover it).  Soft-deletes set
``deleted_at`` via ``save()`` and never reach that signal, so the cascade is
reproduced explicitly here.
"""

from django.utils.timezone import now

from geocaches.runtime_cache import invalidate_filter_dialog_options, invalidate_platform_availability
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
    invalidate_platform_availability()
    invalidate_filter_dialog_options()
    return stamp


class RestoreConflict(Exception):
    """Restoring would leave two live caches sharing an external code.

    ``conflicts`` is a list of ``(code, live_pk)`` pairs.  The caller decides
    how to report it; GCForge never merges the two records on its own.
    """

    def __init__(self, conflicts):
        self.conflicts = conflicts
        super().__init__(", ".join(code for code, _pk in conflicts))


def restore_conflicts(cache):
    """Codes of *cache* — and of the stages it would drag along — already in use.

    Returns a list of ``(code, live_pk)`` pairs; empty means the restore is safe.
    """
    from geocaches.services.codes import CODE_FIELDS, live_code_conflict

    rows = [cache]
    if cache.adventure_id is not None and is_al_parent(cache):
        rows.extend(_stage_qs(cache).filter(deleted_at__isnull=False))

    conflicts = []
    for row in rows:
        for field in CODE_FIELDS:
            other = live_code_conflict(field, getattr(row, field), exclude_pk=row.pk)
            if other is not None:
                conflicts.append((getattr(row, field), other.pk))
    return conflicts


def restore_cache(cache):
    """Restore *cache* from Trash; cascade-restore its stages for AL parents.

    Raises :class:`RestoreConflict` when a live cache already holds one of the
    codes involved — ``uniq_live_gc_code`` and friends forbid the duplicate.
    """
    conflicts = restore_conflicts(cache)
    if conflicts:
        raise RestoreConflict(conflicts)

    cache.deleted_at = None
    cache.save(update_fields=["deleted_at"])
    if cache.adventure_id is not None and is_al_parent(cache):
        _stage_qs(cache).filter(deleted_at__isnull=False).update(deleted_at=None)
    invalidate_platform_availability()
    invalidate_filter_dialog_options()


def stage_pks_for_parents(pk_list):
    """Live stage pks to add when bulk-trashing *pk_list*.

    Any AL parent in *pk_list* drags its (still-live) stages along, except those
    already present in *pk_list*.  Returns a list of extra pks to soft-delete.
    """
    from geocaches.models import Geocache

    adventure_ids = list(
        Geocache.objects.filter(
            pk__in=pk_list, is_al_parent=True
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
