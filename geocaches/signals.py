from django.db.models.signals import post_delete, post_save, pre_delete
from django.dispatch import receiver

# ── Distance cache invalidation ─────────────────────────────────────────


@receiver(post_save, sender="preferences.ReferencePoint")
def invalidate_distance_cache_on_ref_save(sender, instance, **kwargs):
    """Recompute distances when a reference point is created or updated."""
    from geocaches.distance_cache import invalidate
    invalidate(ref_point=instance)


@receiver(post_delete, sender="preferences.ReferencePoint")
def invalidate_distance_cache_on_ref_delete(sender, instance, **kwargs):
    """Remove cached distances when a reference point is deleted."""
    from geocaches.distance_cache import invalidate
    invalidate(ref_point=instance)


@receiver(pre_delete, sender="geocaches.Geocache")
def cascade_al_parent_to_stages(sender, instance, **kwargs):
    """When an AL parent geocache is deleted, cascade-delete its child stages.

    AL parents have adventure_id set and stage_number=None; stages have
    stage_number set.  The relationship goes through the shared Adventure
    record, so Django's FK cascade doesn't cover this automatically.
    """
    if instance.adventure_id is not None and instance.stage_number is None:
        from geocaches.models import Geocache
        Geocache.objects.filter(
            adventure_id=instance.adventure_id,
            stage_number__isnull=False,
        ).delete()


@receiver(post_save, sender="geocaches.Geocache")
def update_adventure_completed(sender, instance, **kwargs):
    """
    When an AL stage is saved, recompute the parent adventure's completed flag.
    Only fires for stage rows (adventure set, stage_number not None).
    """
    if instance.adventure_id is None or instance.stage_number is None:
        return
    from geocaches.models import recompute_adventure_completed, Adventure
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
