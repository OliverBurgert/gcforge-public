"""Tests for geocaches.services.found_accuracy — bulk found_accuracy set."""

from django.test import TestCase

from geocaches.models import ALStageDetail, Adventure, CacheType, Geocache
from geocaches.services.found_accuracy import bulk_set_found_accuracy


class BulkSetFoundAccuracyTest(TestCase):
    def _make_stage(self, al_code, found=True, found_accuracy=""):
        adv, _ = Adventure.objects.get_or_create(
            code="LCX", defaults={"title": "X", "latitude": 52.0, "longitude": 13.0},
        )
        stage = Geocache.objects.create(
            al_code=al_code, name=al_code, cache_type=CacheType.LAB,
            latitude=52.0, longitude=13.0, adventure=adv,
            found=found, found_accuracy=found_accuracy,
        )
        ALStageDetail.objects.create(geocache=stage)
        return stage

    def test_sets_value_on_found_al_stages(self):
        s1 = self._make_stage("LCX-1")
        s2 = self._make_stage("LCX-2")
        changed = bulk_set_found_accuracy(Geocache.objects.all(), "app_verified")
        self.assertEqual(changed, 2)
        s1.refresh_from_db()
        s2.refresh_from_db()
        self.assertEqual(s1.found_accuracy, "app_verified")
        self.assertEqual(s2.found_accuracy, "app_verified")

    def test_skips_unfound_stages(self):
        s1 = self._make_stage("LCX-1", found=False)
        changed = bulk_set_found_accuracy(Geocache.objects.all(), "app_verified")
        self.assertEqual(changed, 0)
        s1.refresh_from_db()
        self.assertEqual(s1.found_accuracy, "")

    def test_skips_non_al_geocaches(self):
        gc = Geocache.objects.create(
            gc_code="GC123", name="Trad", cache_type=CacheType.TRADITIONAL,
            latitude=52.0, longitude=13.0, found=True,
        )
        changed = bulk_set_found_accuracy(Geocache.objects.all(), "app_verified")
        self.assertEqual(changed, 0)
        gc.refresh_from_db()
        self.assertEqual(gc.found_accuracy, "")

    def test_already_matching_value_not_counted(self):
        self._make_stage("LCX-1", found_accuracy="app_verified")
        changed = bulk_set_found_accuracy(Geocache.objects.all(), "app_verified")
        self.assertEqual(changed, 0)

    def test_clear_sets_blank(self):
        s1 = self._make_stage("LCX-1", found_accuracy="armchair")
        changed = bulk_set_found_accuracy(Geocache.objects.all(), "")
        self.assertEqual(changed, 1)
        s1.refresh_from_db()
        self.assertEqual(s1.found_accuracy, "")

    def test_qs_restricts_targets(self):
        self._make_stage("LCX-1")
        other = self._make_stage("LCX-2")
        changed = bulk_set_found_accuracy(
            Geocache.objects.exclude(al_code="LCX-2"), "app_verified",
        )
        self.assertEqual(changed, 1)
        other.refresh_from_db()
        self.assertEqual(other.found_accuracy, "")
