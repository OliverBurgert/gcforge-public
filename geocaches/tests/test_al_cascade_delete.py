"""
Tests that deleting an Adventure Lab parent geocache cascades to its child stages.
"""

from django.test import TestCase

from geocaches.models import Adventure, CacheType, Geocache


def _make_adventure_with_stages(stage_count=3):
    """Create an Adventure with a parent geocache and stage geocaches."""
    adv = Adventure.objects.create(
        code="LC1TEST",
        title="Test Adventure",
        owner="TestOwner",
        latitude=52.52,
        longitude=13.405,
    )
    parent = Geocache.objects.create(
        al_code="LC1TEST",
        name="Test Adventure",
        cache_type=CacheType.LAB,
        latitude=52.52,
        longitude=13.405,
        adventure=adv,
        stage_number=None,
    )
    stages = []
    for i in range(1, stage_count + 1):
        stage = Geocache.objects.create(
            al_code=f"LC1TEST-{i}",
            name=f"Stage {i}",
            cache_type=CacheType.LAB,
            latitude=52.52 + i * 0.001,
            longitude=13.405 + i * 0.001,
            adventure=adv,
            stage_number=i,
        )
        stages.append(stage)
    return adv, parent, stages


class ALCascadeDeleteTest(TestCase):
    """Deleting an AL parent must cascade to its child stages."""

    def test_delete_parent_cascades_to_stages(self):
        adv, parent, stages = _make_adventure_with_stages(3)
        stage_pks = [s.pk for s in stages]

        parent.delete()

        self.assertEqual(
            Geocache.objects.filter(pk__in=stage_pks).count(), 0,
            "Stage geocaches should be deleted when parent is deleted",
        )

    def test_delete_parent_cleans_up_adventure(self):
        adv, parent, stages = _make_adventure_with_stages(2)
        adv_pk = adv.pk

        parent.delete()

        self.assertFalse(
            Adventure.objects.filter(pk=adv_pk).exists(),
            "Adventure record should be cleaned up when all geocaches are gone",
        )

    def test_delete_stage_does_not_cascade(self):
        """Deleting a single stage must NOT delete the parent or other stages."""
        adv, parent, stages = _make_adventure_with_stages(3)

        stages[0].delete()

        self.assertTrue(
            Geocache.objects.filter(pk=parent.pk).exists(),
            "Parent should still exist after deleting one stage",
        )
        self.assertEqual(
            Geocache.objects.filter(adventure=adv, stage_number__isnull=False).count(),
            2,
            "Other stages should still exist after deleting one stage",
        )

    def test_batch_delete_parent_cascades(self):
        """Queryset .delete() on the parent must also remove stages."""
        adv, parent, stages = _make_adventure_with_stages(3)
        stage_pks = [s.pk for s in stages]

        Geocache.objects.filter(pk=parent.pk).delete()

        self.assertEqual(
            Geocache.objects.filter(pk__in=stage_pks).count(), 0,
            "Batch deletion of parent should cascade to stages",
        )
