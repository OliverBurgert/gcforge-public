"""Tests for geocaches.services.dedup and find_potential_duplicates."""

from datetime import date

from django.test import TestCase

from geocaches.models import (
    CacheSize,
    CacheStatus,
    CacheType,
    Geocache,
    Image,
    Log,
    LogType,
    OCExtension,
    Tag,
)
from geocaches.services import find_potential_duplicates, merge_duplicate
from geocaches.services.dedup import _merge_into


def _gc(**kwargs):
    defaults = dict(
        name="GC Cache", cache_type=CacheType.TRADITIONAL, size=CacheSize.SMALL,
        status=CacheStatus.ACTIVE, latitude=48.5, longitude=9.1,
    )
    defaults.update(kwargs)
    return Geocache.objects.create(**defaults)


def _oc(**kwargs):
    defaults = dict(
        name="OC Cache", cache_type=CacheType.TRADITIONAL, size=CacheSize.SMALL,
        status=CacheStatus.ACTIVE, latitude=48.5, longitude=9.1,
    )
    defaults.update(kwargs)
    return Geocache.objects.create(**defaults)


class MergeIntoTest(TestCase):
    def test_oc_code_set_on_dest(self):
        dest = _gc(gc_code="GC11111")
        source = _oc(oc_code="OC11111")
        _merge_into(source=source, dest=dest, oc_code="OC11111")
        dest.refresh_from_db()
        self.assertEqual(dest.oc_code, "OC11111")

    def test_source_deleted(self):
        dest = _gc(gc_code="GC11111")
        source = _oc(oc_code="OC11111")
        source_pk = source.pk
        _merge_into(source=source, dest=dest, oc_code="OC11111")
        self.assertFalse(Geocache.objects.filter(pk=source_pk).exists())

    def test_elevation_filled_from_source(self):
        dest = _gc(gc_code="GC11111")
        source = _oc(oc_code="OC11111", elevation=250.0)
        _merge_into(source=source, dest=dest, oc_code="OC11111")
        dest.refresh_from_db()
        self.assertEqual(dest.elevation, 250.0)

    def test_elevation_not_overwritten_if_dest_has_value(self):
        dest = _gc(gc_code="GC11111", elevation=100.0)
        source = _oc(oc_code="OC11111", elevation=250.0)
        _merge_into(source=source, dest=dest, oc_code="OC11111")
        dest.refresh_from_db()
        self.assertEqual(dest.elevation, 100.0)

    def test_country_filled_from_source(self):
        dest = _gc(gc_code="GC11111")
        source = _oc(oc_code="OC11111", iso_country_code="DE", country="Germany")
        _merge_into(source=source, dest=dest, oc_code="OC11111")
        dest.refresh_from_db()
        self.assertEqual(dest.iso_country_code, "DE")

    def test_logs_moved(self):
        dest = _gc(gc_code="GC11111")
        source = _oc(oc_code="OC11111")
        Log.objects.create(
            geocache=source, log_type=LogType.FOUND, user_name="User1",
            logged_date=date(2023, 1, 1), source_id="LOG1", source="oc_de",
        )
        _merge_into(source=source, dest=dest, oc_code="OC11111")
        self.assertEqual(dest.logs.count(), 1)

    def test_duplicate_logs_not_added(self):
        dest = _gc(gc_code="GC11111")
        source = _oc(oc_code="OC11111")
        Log.objects.create(
            geocache=dest, log_type=LogType.FOUND, user_name="User1",
            logged_date=date(2023, 1, 1), source_id="LOG1", source="gc",
        )
        Log.objects.create(
            geocache=source, log_type=LogType.FOUND, user_name="User1",
            logged_date=date(2023, 1, 1), source_id="LOG2", source="oc_de",
        )
        _merge_into(source=source, dest=dest, oc_code="OC11111")
        self.assertEqual(dest.logs.count(), 1)

    def test_images_moved(self):
        dest = _gc(gc_code="GC11111")
        source = _oc(oc_code="OC11111")
        Image.objects.create(geocache=source, url="https://example.com/img.jpg", name="Photo")
        _merge_into(source=source, dest=dest, oc_code="OC11111")
        self.assertEqual(dest.images.count(), 1)

    def test_duplicate_images_not_added(self):
        dest = _gc(gc_code="GC11111")
        source = _oc(oc_code="OC11111")
        url = "https://example.com/img.jpg"
        Image.objects.create(geocache=dest, url=url, name="GC Photo")
        Image.objects.create(geocache=source, url=url, name="OC Photo")
        _merge_into(source=source, dest=dest, oc_code="OC11111")
        self.assertEqual(dest.images.count(), 1)

    def test_tags_merged(self):
        dest = _gc(gc_code="GC11111")
        source = _oc(oc_code="OC11111")
        tag = Tag.objects.create(name="MyTag")
        source.tags.add(tag)
        _merge_into(source=source, dest=dest, oc_code="OC11111")
        self.assertIn(tag, dest.tags.all())

    def test_oc_extension_moved(self):
        dest = _gc(gc_code="GC11111")
        source = _oc(oc_code="OC11111")
        OCExtension.objects.create(geocache=source, req_passwd=True)
        _merge_into(source=source, dest=dest, oc_code="OC11111")
        self.assertTrue(OCExtension.objects.filter(geocache=dest).exists())

    def test_oc_extension_dest_replaced(self):
        dest = _gc(gc_code="GC11111")
        source = _oc(oc_code="OC11111")
        OCExtension.objects.create(geocache=dest, trip_time=30)
        OCExtension.objects.create(geocache=source, req_passwd=True)
        _merge_into(source=source, dest=dest, oc_code="OC11111")
        ext = OCExtension.objects.get(geocache=dest)
        self.assertTrue(ext.req_passwd)


class FindPotentialDuplicatesTest(TestCase):
    def _make_nearby(self, gc_code, oc_code, lat=48.5, lon=9.1, offset=0.0001):
        gc = _gc(gc_code=gc_code, latitude=lat, longitude=lon)
        oc = _oc(oc_code=oc_code, latitude=lat + offset, longitude=lon + offset)
        return gc, oc

    def test_empty_if_no_oc_caches(self):
        _gc(gc_code="GC11111")
        result = find_potential_duplicates()
        self.assertEqual(result, [])

    def test_empty_if_no_gc_caches(self):
        _oc(oc_code="OC11111")
        result = find_potential_duplicates()
        self.assertEqual(result, [])

    def test_finds_nearby_pair(self):
        gc, oc = self._make_nearby("GC11111", "OC11111")
        result = find_potential_duplicates()
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["gc_code"], "GC11111")
        self.assertEqual(result[0]["oc_code"], "OC11111")

    def test_far_pair_not_returned(self):
        # 1 degree apart — well beyond the 15m threshold
        _gc(gc_code="GC11111", latitude=48.5, longitude=9.1)
        _oc(oc_code="OC11111", latitude=49.5, longitude=10.1)
        result = find_potential_duplicates()
        self.assertEqual(result, [])

    def test_includes_distance_m(self):
        self._make_nearby("GC11111", "OC11111", offset=0.00005)
        result = find_potential_duplicates()
        self.assertIn("distance_m", result[0])
        self.assertLess(result[0]["distance_m"], 15.0)

    def test_user_decision_none_if_no_fusion_record(self):
        self._make_nearby("GC11111", "OC11111")
        result = find_potential_duplicates()
        self.assertIsNone(result[0]["user_decision"])

    def test_dont_fuse_excluded_by_default(self):
        from geocaches.models import CacheFusionRecord
        gc, oc = self._make_nearby("GC11111", "OC11111")
        CacheFusionRecord.objects.create(gc_code="GC11111", oc_code="OC11111", user_decision="dont_fuse")
        result = find_potential_duplicates()
        self.assertEqual(result, [])

    def test_dont_fuse_included_with_flag(self):
        from geocaches.models import CacheFusionRecord
        gc, oc = self._make_nearby("GC11111", "OC11111")
        CacheFusionRecord.objects.create(gc_code="GC11111", oc_code="OC11111", user_decision="dont_fuse")
        result = find_potential_duplicates(include_dont_fuse=True)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["user_decision"], "dont_fuse")


class MergeDuplicateTest(TestCase):
    def test_merge_duplicate_sets_oc_code(self):
        gc = _gc(gc_code="GC11111")
        oc = _oc(oc_code="OC11111")
        merge_duplicate(gc.pk, oc.pk)
        gc.refresh_from_db()
        self.assertEqual(gc.oc_code, "OC11111")

    def test_merge_duplicate_returns_message(self):
        gc = _gc(gc_code="GC11111")
        oc = _oc(oc_code="OC11111")
        msg = merge_duplicate(gc.pk, oc.pk)
        self.assertIn("GC11111", msg)
        self.assertIn("OC11111", msg)

    def test_merge_duplicate_records_fusion_decision(self):
        from geocaches.models import CacheFusionRecord
        gc = _gc(gc_code="GC11111")
        oc = _oc(oc_code="OC11111")
        merge_duplicate(gc.pk, oc.pk)
        self.assertTrue(
            CacheFusionRecord.objects.filter(gc_code="GC11111", oc_code="OC11111").exists()
        )
