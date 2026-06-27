"""
Tests that deleting an Adventure Lab parent geocache cascades to its child stages.
"""

from django.test import TestCase

from geocaches.models import ALStageDetail, Adventure, CacheType, Geocache


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
        )
        ALStageDetail.objects.create(geocache=stage, stage_number=i)
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
            Geocache.objects.filter(adventure=adv, al_detail__isnull=False).count(),
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


class ALTrashCascadeTest(TestCase):
    """Soft-deleting (Trash) an AL parent must cascade to its stages, and the
    restore/purge paths must keep the stages in step with the parent."""

    def test_trash_parent_cascades_to_stages(self):
        adv, parent, stages = _make_adventure_with_stages(3)
        stage_pks = [s.pk for s in stages]

        from geocaches.services.trash import trash_cache
        trash_cache(parent)

        self.assertEqual(
            Geocache.objects.filter(pk__in=stage_pks).count(), 0,
            "Stages should be hidden from the live manager after trashing parent",
        )
        self.assertEqual(
            Geocache.all_objects.filter(pk__in=stage_pks, deleted_at__isnull=False).count(),
            3,
            "Stages should be soft-deleted (in Trash), not removed",
        )

    def test_restore_parent_cascades_to_stages(self):
        adv, parent, stages = _make_adventure_with_stages(3)
        stage_pks = [s.pk for s in stages]

        from geocaches.services.trash import restore_cache, trash_cache
        trash_cache(parent)
        parent.refresh_from_db()
        restore_cache(parent)

        self.assertEqual(
            Geocache.objects.filter(pk__in=stage_pks).count(), 3,
            "Restoring the parent should restore its stages too",
        )

    def test_trash_stage_does_not_cascade(self):
        """Trashing a single stage must not touch the parent or other stages."""
        adv, parent, stages = _make_adventure_with_stages(3)

        from geocaches.services.trash import trash_cache
        trash_cache(stages[0])

        self.assertTrue(Geocache.objects.filter(pk=parent.pk).exists())
        self.assertEqual(
            Geocache.objects.filter(adventure=adv, al_detail__isnull=False).count(), 2,
            "Other stages should stay live when one stage is trashed",
        )

    def test_purge_trashed_parent_removes_trashed_stages(self):
        adv, parent, stages = _make_adventure_with_stages(3)
        stage_pks = [s.pk for s in stages]

        from geocaches.services.trash import trash_cache
        trash_cache(parent)

        # Permanent delete from Trash (hard delete) must reach the trashed stages.
        Geocache.all_objects.get(pk=parent.pk).delete()

        self.assertEqual(
            Geocache.all_objects.filter(pk__in=stage_pks).count(), 0,
            "Purging a trashed parent should permanently remove its trashed stages",
        )
        self.assertFalse(
            Adventure.objects.filter(pk=adv.pk).exists(),
            "Adventure should be cleaned up once all its geocaches are purged",
        )


class ALBulkTrashExpansionTest(TestCase):
    """The bulk (filtered) delete must also drag AL stages along with parents."""

    def test_parent_pk_expands_to_stages(self):
        adv, parent, stages = _make_adventure_with_stages(3)
        stage_pks = {s.pk for s in stages}

        from geocaches.services.trash import stage_pks_for_parents
        extra = stage_pks_for_parents([parent.pk])

        self.assertEqual(set(extra), stage_pks)

    def test_no_duplicate_when_stages_already_selected(self):
        adv, parent, stages = _make_adventure_with_stages(3)
        pk_list = [parent.pk, stages[0].pk]

        from geocaches.services.trash import stage_pks_for_parents
        extra = stage_pks_for_parents(pk_list)

        self.assertEqual(set(extra), {stages[1].pk, stages[2].pk})

    def test_stage_only_selection_does_not_expand(self):
        adv, parent, stages = _make_adventure_with_stages(3)

        from geocaches.services.trash import stage_pks_for_parents
        self.assertEqual(stage_pks_for_parents([stages[0].pk]), [])

    def test_non_al_cache_does_not_expand(self):
        plain = Geocache.objects.create(
            gc_code="GC0001", name="Plain", cache_type=CacheType.TRADITIONAL,
            latitude=1.0, longitude=2.0,
        )

        from geocaches.services.trash import stage_pks_for_parents
        self.assertEqual(stage_pks_for_parents([plain.pk]), [])
