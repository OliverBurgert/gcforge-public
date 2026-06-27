"""Tests for geocaches.services.adventures — AL completion tracking + parent guard."""

from django.test import TestCase

from geocaches.models import ALStageDetail, Adventure, CacheType, Geocache
from geocaches.services.adventures import (
    ensure_not_al_parent_found,
    is_al_parent,
    recompute_adventure_completed,
)


def _make_adventure(stage_count=3, found_stages=None):
    """Create an Adventure with parent + N stages.

    found_stages: set of 1-based stage numbers to mark found.
    """
    found_stages = found_stages or set()
    adv = Adventure.objects.create(
        code="LCTEST", title="Test Adventure",
        latitude=52.52, longitude=13.405,
    )
    parent = Geocache.objects.create(
        al_code="LCTEST", name="Test Adventure",
        cache_type=CacheType.LAB, latitude=52.52, longitude=13.405,
        adventure=adv,
    )
    stages = []
    for i in range(1, stage_count + 1):
        stage = Geocache.objects.create(
            al_code=f"LCTEST-{i}", name=f"Stage {i}",
            cache_type=CacheType.LAB,
            latitude=52.52 + i * 0.001, longitude=13.405 + i * 0.001,
            adventure=adv,
            found=i in found_stages,
        )
        ALStageDetail.objects.create(geocache=stage, stage_number=i)
        stages.append(stage)
    return adv, parent, stages


class IsAlParentTest(TestCase):
    def test_parent_is_detected(self):
        adv, parent, _ = _make_adventure(1)
        self.assertTrue(is_al_parent(parent))

    def test_stage_is_not_parent(self):
        _, _, stages = _make_adventure(1)
        self.assertFalse(is_al_parent(stages[0]))

    def test_regular_cache_is_not_parent(self):
        cache = Geocache.objects.create(
            gc_code="GC0001", name="Regular",
            cache_type=CacheType.TRADITIONAL,
            latitude=48.0, longitude=9.0,
        )
        self.assertFalse(is_al_parent(cache))


class EnsureNotAlParentFoundTest(TestCase):
    def test_rejects_found_on_al_parent(self):
        _, parent, _ = _make_adventure(1)
        parent.found = True
        with self.assertRaises(ValueError) as ctx:
            ensure_not_al_parent_found(parent)
        self.assertIn("Adventure Lab parent", str(ctx.exception))

    def test_accepts_unfound_al_parent(self):
        _, parent, _ = _make_adventure(1)
        parent.found = False
        ensure_not_al_parent_found(parent)

    def test_accepts_found_on_al_stage(self):
        _, _, stages = _make_adventure(1)
        stages[0].found = True
        ensure_not_al_parent_found(stages[0])

    def test_accepts_completed_on_al_parent(self):
        _, parent, _ = _make_adventure(1)
        parent.completed = True
        parent.found = False
        ensure_not_al_parent_found(parent)

    def test_accepts_found_true_when_completed_true(self):
        _, parent, _ = _make_adventure(1)
        parent.completed = True
        parent.found = True
        ensure_not_al_parent_found(parent)

    def test_accepts_found_on_regular_cache(self):
        cache = Geocache.objects.create(
            gc_code="GC0001", name="Regular",
            cache_type=CacheType.TRADITIONAL,
            latitude=48.0, longitude=9.0,
            found=True,
        )
        ensure_not_al_parent_found(cache)


class RecomputeAdventureCompletedTest(TestCase):
    def test_no_stages_returns_false(self):
        adv = Adventure.objects.create(
            code="LCEMPTY", title="Empty",
            latitude=52.52, longitude=13.405,
        )
        Geocache.objects.create(
            al_code="LCEMPTY", name="Parent",
            cache_type=CacheType.LAB,
            latitude=52.52, longitude=13.405,
            adventure=adv,
        )
        self.assertFalse(recompute_adventure_completed(adv))

    def test_partial_found_not_completed(self):
        adv, parent, _ = _make_adventure(3, found_stages={1})
        result = recompute_adventure_completed(adv)
        self.assertFalse(result)
        parent.refresh_from_db()
        self.assertFalse(parent.completed)

    def test_all_found_marks_completed(self):
        adv, parent, _ = _make_adventure(3, found_stages={1, 2, 3})
        result = recompute_adventure_completed(adv)
        self.assertTrue(result)
        parent.refresh_from_db()
        self.assertTrue(parent.completed)

    def test_all_found_sets_found_flag(self):
        adv, parent, _ = _make_adventure(2, found_stages={1, 2})
        recompute_adventure_completed(adv)
        parent.refresh_from_db()
        self.assertTrue(parent.found)

    def test_all_found_sets_found_date_from_completion_date(self):
        from datetime import date, datetime, timezone
        adv, parent, _ = _make_adventure(2, found_stages={1, 2})
        adv.completion_date = datetime(2025, 6, 15, 10, 30, tzinfo=timezone.utc)
        adv.save(update_fields=["completion_date"])
        recompute_adventure_completed(adv)
        parent.refresh_from_db()
        self.assertEqual(parent.found_date, date(2025, 6, 15))

    def test_all_found_no_found_date_when_no_completion_date(self):
        adv, parent, _ = _make_adventure(2, found_stages={1, 2})
        self.assertIsNone(adv.completion_date)
        recompute_adventure_completed(adv)
        parent.refresh_from_db()
        self.assertIsNone(parent.found_date)

    def test_completion_reverts_when_stage_unfound(self):
        adv, parent, stages = _make_adventure(3, found_stages={1, 2, 3})
        recompute_adventure_completed(adv)
        parent.refresh_from_db()
        self.assertTrue(parent.completed)
        stages[0].found = False
        stages[0].save(update_fields=["found"])
        result = recompute_adventure_completed(adv)
        self.assertFalse(result)
        parent.refresh_from_db()
        self.assertFalse(parent.completed)

    def test_no_parent_row_still_returns_result(self):
        adv = Adventure.objects.create(
            code="LCNOP", title="No parent",
            latitude=52.52, longitude=13.405,
        )
        stage = Geocache.objects.create(
            al_code="LCNOP-1", name="Stage 1",
            cache_type=CacheType.LAB,
            latitude=52.52, longitude=13.405,
            adventure=adv, found=True,
        )
        ALStageDetail.objects.create(geocache=stage, stage_number=1)
        result = recompute_adventure_completed(adv)
        self.assertTrue(result)
