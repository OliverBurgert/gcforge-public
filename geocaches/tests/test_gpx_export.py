"""
Tests for geocaches.exporters.gpx_gc — GPX export.
"""

import xml.etree.ElementTree as ET
from datetime import date

from django.test import TestCase

from geocaches.exporters.gpx_gc import GS_NS, GSAK_NS, GPX_NS, export_gpx
from geocaches.models import CacheSize, CacheStatus, CacheType, Geocache, Log, LogType


def _make_cache(gc_code="GC10001", **kwargs):
    defaults = dict(
        name="Test Cache",
        cache_type=CacheType.TRADITIONAL,
        size=CacheSize.SMALL,
        status=CacheStatus.ACTIVE,
        latitude=48.5,
        longitude=9.1,
        difficulty=2.0,
        terrain=1.5,
    )
    defaults.update(kwargs)
    return Geocache.objects.create(gc_code=gc_code, **defaults)


def _parse_gpx(data: bytes) -> ET.Element:
    return ET.fromstring(data)


def _wpts(root):
    return root.findall(f"{{{GPX_NS}}}wpt")


def _log_finders(gpx_root, wpt_index=0):
    wpt = _wpts(gpx_root)[wpt_index]
    cache = wpt.find(f"{{{GS_NS}}}cache")
    logs = cache.find(f"{{{GS_NS}}}logs")
    return [
        log.find(f"{{{GS_NS}}}finder").text
        for log in logs.findall(f"{{{GS_NS}}}log")
    ]


class TestExportGpxBasic(TestCase):
    def test_exports_single_cache(self):
        _make_cache()
        data = export_gpx(Geocache.objects.all())
        root = _parse_gpx(data)
        self.assertEqual(len(_wpts(root)), 1)

    def test_wpt_lat_lon(self):
        _make_cache(gc_code="GC10001", latitude=48.123456, longitude=9.654321)
        root = _parse_gpx(export_gpx(Geocache.objects.all()))
        wpt = _wpts(root)[0]
        self.assertAlmostEqual(float(wpt.get("lat")), 48.123456, places=5)
        self.assertAlmostEqual(float(wpt.get("lon")), 9.654321, places=5)

    def test_uses_corrected_coords_when_present(self):
        """Main wpt uses corrected coords; GSAK extension stores originals."""
        from geocaches.models import CorrectedCoordinates
        cache = _make_cache(gc_code="GC10002", latitude=48.0, longitude=9.0)
        CorrectedCoordinates.objects.create(
            geocache=cache, latitude=48.999, longitude=9.999
        )
        root = _parse_gpx(export_gpx(Geocache.objects.all()))
        wpt = _wpts(root)[0]
        # Main wpt has corrected coordinates
        self.assertAlmostEqual(float(wpt.get("lat")), 48.999, places=3)
        self.assertAlmostEqual(float(wpt.get("lon")), 9.999, places=3)
        # GSAK extension stores original coordinates
        gsak_ext = wpt.find(f"{{{GSAK_NS}}}wptExtension")
        self.assertIsNotNone(gsak_ext)
        self.assertAlmostEqual(
            float(gsak_ext.find(f"{{{GSAK_NS}}}LatBeforeCorrect").text), 48.0, places=3
        )
        self.assertAlmostEqual(
            float(gsak_ext.find(f"{{{GSAK_NS}}}LonBeforeCorrect").text), 9.0, places=3
        )

    def test_found_cache_uses_found_sym(self):
        _make_cache(gc_code="GC10003", found=True)
        root = _parse_gpx(export_gpx(Geocache.objects.all()))
        wpt = _wpts(root)[0]
        sym = wpt.find(f"{{{GPX_NS}}}sym").text
        self.assertEqual(sym, "Geocache Found")

    def test_unfound_cache_uses_geocache_sym(self):
        _make_cache(gc_code="GC10004", found=False)
        root = _parse_gpx(export_gpx(Geocache.objects.all()))
        wpt = _wpts(root)[0]
        sym = wpt.find(f"{{{GPX_NS}}}sym").text
        self.assertEqual(sym, "Geocache")

    def test_type_element_uses_groundspeak_string(self):
        _make_cache(gc_code="GC10005", cache_type=CacheType.MYSTERY)
        root = _parse_gpx(export_gpx(Geocache.objects.all()))
        wpt = _wpts(root)[0]
        type_el = wpt.find(f"{{{GPX_NS}}}type")
        self.assertEqual(type_el.text, "Geocache|Unknown Cache")

    def test_groundspeak_type_element(self):
        _make_cache(gc_code="GC10006", cache_type=CacheType.MULTI)
        root = _parse_gpx(export_gpx(Geocache.objects.all()))
        wpt = _wpts(root)[0]
        cache_el = wpt.find(f"{{{GS_NS}}}cache")
        gs_type = cache_el.find(f"{{{GS_NS}}}type").text
        self.assertEqual(gs_type, "Multi-cache")

    def test_output_is_valid_xml(self):
        _make_cache()
        data = export_gpx(Geocache.objects.all())
        self.assertIsNotNone(_parse_gpx(data))
        self.assertTrue(data.startswith(b"<?xml"))

    def test_empty_queryset(self):
        data = export_gpx(Geocache.objects.none())
        root = _parse_gpx(data)
        self.assertEqual(len(_wpts(root)), 0)


class TestExportGpxUserLogsFirst(TestCase):
    def setUp(self):
        self.cache = _make_cache(gc_code="GC20001")
        Log.objects.create(
            geocache=self.cache, source_id="1", log_type=LogType.FOUND,
            user_name="OtherCacher", logged_date=date(2024, 6, 1), text="",
        )
        Log.objects.create(
            geocache=self.cache, source_id="2", log_type=LogType.FOUND,
            user_name="MyGCName", logged_date=date(2024, 5, 1), text="Found it!",
        )
        Log.objects.create(
            geocache=self.cache, source_id="3", log_type=LogType.NOTE,
            user_name="SomeoneElse", logged_date=date(2024, 4, 1), text="",
        )

    def test_without_username_logs_in_natural_order(self):
        finders = _log_finders(_parse_gpx(export_gpx(Geocache.objects.all())))
        # natural order from DB (by source_id as strings from [:20])
        self.assertEqual(finders[0], "OtherCacher")

    def test_with_username_user_log_appears_first(self):
        finders = _log_finders(
            _parse_gpx(export_gpx(Geocache.objects.all(), gc_username="MyGCName"))
        )
        self.assertEqual(finders[0], "MyGCName")

    def test_with_username_other_logs_follow(self):
        finders = _log_finders(
            _parse_gpx(export_gpx(Geocache.objects.all(), gc_username="MyGCName"))
        )
        self.assertIn("OtherCacher", finders)
        self.assertIn("SomeoneElse", finders)

    def test_with_nonexistent_username_order_unchanged(self):
        finders_no_user = _log_finders(_parse_gpx(export_gpx(Geocache.objects.all())))
        finders_with_user = _log_finders(
            _parse_gpx(export_gpx(Geocache.objects.all(), gc_username="NoSuchUser"))
        )
        self.assertEqual(finders_no_user, finders_with_user)

    def test_multiple_user_logs_all_appear_first(self):
        Log.objects.create(
            geocache=self.cache, source_id="4", log_type=LogType.NOTE,
            user_name="MyGCName", logged_date=date(2023, 1, 1), text="",
        )
        finders = _log_finders(
            _parse_gpx(export_gpx(Geocache.objects.all(), gc_username="MyGCName"))
        )
        user_indices = [i for i, f in enumerate(finders) if f == "MyGCName"]
        non_user_indices = [i for i, f in enumerate(finders) if f != "MyGCName"]
        self.assertTrue(all(u < n for u in user_indices for n in non_user_indices))
