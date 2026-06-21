from django.db.models.signals import post_delete, post_save, pre_delete
from django.dispatch import receiver

# ── Distance cache invalidation ─────────────────────────────────────────


@receiver(post_save, sender="preferences.ReferencePoint")
def invalidate_distance_cache_on_ref_save(sender, instance, **kwargs):
    """Recompute distances when a reference point is created or updated."""
    from geocaches.geo.distance_cache import invalidate
    invalidate(ref_point=instance)


@receiver(post_delete, sender="preferences.ReferencePoint")
def invalidate_distance_cache_on_ref_delete(sender, instance, **kwargs):
    """Remove cached distances when a reference point is deleted."""
    from geocaches.geo.distance_cache import invalidate
    invalidate(ref_point=instance)


@receiver(pre_delete, sender="geocaches.Geocache")
def cascade_al_parent_to_stages(sender, instance, **kwargs):
    """When an AL parent geocache is deleted, cascade-delete its child stages.

    AL parents have adventure_id set and stage_number=None; stages have
    stage_number set.  The relationship goes through the shared Adventure
    record, so Django's FK cascade doesn't cover this automatically.
    """
    if instance.adventure_id is not None:
        from geocaches.services.adventures import is_al_parent
        if is_al_parent(instance):
            from geocaches.models import Geocache
            # all_objects so purging a trashed parent also removes its trashed
            # stages — they were soft-deleted alongside it (services.trash).
            Geocache.all_objects.filter(
                adventure_id=instance.adventure_id,
                al_detail__isnull=False,
            ).delete()


@receiver(post_save, sender="geocaches.Geocache")
def update_adventure_completed(sender, instance, **kwargs):
    """
    When an AL stage is saved, recompute the parent adventure's completed flag.
    Only fires for stage rows (adventure set, stage_number not None).
    """
    if instance.adventure_id is None:
        return
    from geocaches.services.adventures import is_al_parent
    if is_al_parent(instance):
        return
    from geocaches.models import Adventure
    from geocaches.services.adventures import recompute_adventure_completed
    adv = Adventure.objects.filter(pk=instance.adventure_id).first()
    if adv:
        recompute_adventure_completed(adv)


@receiver(post_delete, sender="geocaches.Geocache")
def cleanup_orphan_adventure(sender, instance, **kwargs):
    """Delete an Adventure when its last linked Geocache is removed."""
    if not instance.adventure_id:
        return
    from geocaches.models import Adventure
    adv = Adventure.objects.filter(pk=instance.adventure_id).first()
    if adv and not adv.stages.exists():
        adv.delete()


# ── Cached image cascade ────────────────────────────────────────────────
# When an owner entity is deleted, any CachedImage that loses its last
# link gets pruned (row + file on disk). Implemented via pre_delete to
# snapshot the linked image IDs before Django removes the M2M rows,
# then post_delete to check + prune.


_OWNER_LINK = {
    "geocaches.Geocache":      "linked_geocaches",
    "geocaches.Trackable":     "linked_trackables",
    "geocaches.Adventure":     "linked_adventures",
    "geocaches.Log":           "linked_logs",
    "geocaches.TrackableLog":  "linked_trackable_logs",
}


def _snapshot_linked_ids(sender_label):
    field_name = _OWNER_LINK[sender_label]

    @receiver(pre_delete, sender=sender_label, weak=False)
    def _stash(sender, instance, **kwargs):
        from geocaches.models import CachedImage
        instance._cached_image_ids_to_check = list(
            CachedImage.objects.filter(**{field_name: instance}).values_list("id", flat=True)
        )

    @receiver(post_delete, sender=sender_label, weak=False)
    def _prune(sender, instance, **kwargs):
        ids = getattr(instance, "_cached_image_ids_to_check", None)
        if not ids:
            return
        from geocaches.models import CachedImage
        from geocaches.services.image_cache import _path_for
        for img in CachedImage.objects.filter(id__in=ids):
            if img.has_any_link():
                continue
            _path_for(img).unlink(missing_ok=True)
            img.delete()


for _label in _OWNER_LINK:
    _snapshot_linked_ids(_label)
