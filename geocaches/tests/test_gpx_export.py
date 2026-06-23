"""
Tests for geocaches.exporters.gpx_gc — GPX export.
"""

import xml.etree.ElementTree as ET
from datetime import date

from django.test import TestCase

from geocaches.exporters.gpx_gc import GS_NS, GSAK_NS, GPX_NS, export_gpx
from geocaches.models import ALStageDetail, Adventure, CacheSize, CacheStatus, CacheType, Geocache, Log, LogType, Waypoint, WaypointType


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


class TestExportGpxChildWaypoints(TestCase):
    def setUp(self):
        self.cache = _make_cache(gc_code="GC30001")

    def _add_wp(self, wp_type, prefix="", lat=48.1, lon=9.1, name="WP"):
        return Waypoint.objects.create(
            geocache=self.cache,
            waypoint_type=wp_type,
            prefix=prefix,
            name=name,
            latitude=lat,
            longitude=lon,
        )

    def _child_wpts(self):
        root = _parse_gpx(export_gpx(Geocache.objects.all()))
        # child waypoints follow the parent wpt (index 0)
        return _wpts(root)[1:]

    def test_blank_prefix_derives_from_waypoint_type(self):
        """No stored prefix → code prefix comes from waypoint_type mapping."""
        cases = [
            (WaypointType.PARKING,   "PK"),
            (WaypointType.STAGE,     "ST"),
            (WaypointType.QUESTION,  "QA"),
            (WaypointType.FINAL,     "FL"),
            (WaypointType.TRAILHEAD, "TH"),
            (WaypointType.REFERENCE, "RP"),
            (WaypointType.OTHER,     "WP"),
        ]
        for wp_type, expected_prefix in cases:
            with self.subTest(wp_type=wp_type):
                Waypoint.objects.all().delete()
                self._add_wp(wp_type, prefix="")
                child = self._child_wpts()[0]
                name = child.find(f"{{{GPX_NS}}}name").text
                self.assertTrue(name.startswith(expected_prefix),
                                f"{wp_type}: expected prefix {expected_prefix!r}, got name {name!r}")

    def test_explicit_valid_prefix_overrides_type_derivation(self):
        """A stored valid prefix is used verbatim regardless of waypoint_type."""
        self._add_wp(WaypointType.PARKING, prefix="FL")
        child = self._child_wpts()[0]
        name = child.find(f"{{{GPX_NS}}}name").text
        self.assertTrue(name.startswith("FL"))

    def test_numeric_prefix_falls_back_to_type(self):
        """A numeric prefix left over from old imports is ignored; type derivation is used."""
        self._add_wp(WaypointType.FINAL, prefix="01")
        child = self._child_wpts()[0]
        name = child.find(f"{{{GPX_NS}}}name").text
        self.assertTrue(name.startswith("FL"),
                        f"Expected FL prefix for Final with numeric stored prefix, got {name!r}")

    def test_unrecognised_alpha_prefix_falls_back_to_type(self):
        """An unrecognised alpha prefix (e.g. 'P1') is ignored; type derivation is used."""
        self._add_wp(WaypointType.PARKING, prefix="P1")
        child = self._child_wpts()[0]
        name = child.find(f"{{{GPX_NS}}}name").text
        self.assertTrue(name.startswith("PK"))

    def test_child_wp_name_suffix_matches_parent_code(self):
        """Child wp name suffix is the GC code without 'GC' (e.g. GC30001 → '30001')."""
        self._add_wp(WaypointType.PARKING)
        child = self._child_wpts()[0]
        name = child.find(f"{{{GPX_NS}}}name").text
        self.assertTrue(name.endswith("30001"), f"Expected suffix '30001', got {name!r}")

    def test_child_wp_sym_uses_full_gc_string(self):
        """<sym> uses the full GC display string, not the enum value."""
        cases = [
            (WaypointType.PARKING,   "Parking Area"),
            (WaypointType.STAGE,     "Stages of a Multicache"),
            (WaypointType.QUESTION,  "Question to Answer"),
            (WaypointType.FINAL,     "Final Location"),
            (WaypointType.TRAILHEAD, "Trailhead"),
            (WaypointType.REFERENCE, "Reference Point"),
            (WaypointType.OTHER,     "Reference Point"),
        ]
        for wp_type, expected_sym in cases:
            with self.subTest(wp_type=wp_type):
                Waypoint.objects.all().delete()
                self._add_wp(wp_type)
                child = self._child_wpts()[0]
                sym = child.find(f"{{{GPX_NS}}}sym").text
                self.assertEqual(sym, expected_sym)

    def test_child_wp_type_element_uses_full_gc_string(self):
        """<type> uses 'Waypoint|<full GC string>', not 'Waypoint|<enum value>'."""
        cases = [
            (WaypointType.PARKING,   "Waypoint|Parking Area"),
            (WaypointType.FINAL,     "Waypoint|Final Location"),
            (WaypointType.STAGE,     "Waypoint|Stages of a Multicache"),
            (WaypointType.QUESTION,  "Waypoint|Question to Answer"),
            (WaypointType.TRAILHEAD, "Waypoint|Trailhead"),
            (WaypointType.REFERENCE, "Waypoint|Reference Point"),
        ]
        for wp_type, expected_type in cases:
            with self.subTest(wp_type=wp_type):
                Waypoint.objects.all().delete()
                self._add_wp(wp_type)
                child = self._child_wpts()[0]
                type_el = child.find(f"{{{GPX_NS}}}type")
                self.assertEqual(type_el.text, expected_type)

    def test_waypoint_without_coords_is_skipped(self):
        Waypoint.objects.create(
            geocache=self.cache, waypoint_type=WaypointType.FINAL,
            latitude=None, longitude=None,
        )
        self.assertEqual(len(self._child_wpts()), 0)


def _make_alc(stage_count=2, question=""):
    """Create an Adventure with parent + N stage geocaches.

    Returns (Adventure, parent_geocache, [stage_geocaches]).
    """
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
            latitude=52.52 + i * 0.01, longitude=13.405 + i * 0.01,
            adventure=adv,
            long_description=f"Stage {i} description.",
        )
        ALStageDetail.objects.create(geocache=stage, stage_number=i, question_text=question)
        stages.append(stage)
    return adv, parent, stages


def _long_desc(wpt):
    cache_el = wpt.find(f"{{{GS_NS}}}cache")
    return cache_el.find(f"{{{GS_NS}}}long_description").text or ""


class TestExportGpxAlcStageQuestion(TestCase):
    """ALC stage question_text should appear in the exported description."""

    def test_standalone_stage_appends_question_to_long_desc(self):
        _, _, stages = _make_alc(stage_count=1, question="What colour is the door?")
        root = _parse_gpx(export_gpx(Geocache.objects.filter(pk=stages[0].pk)))
        wpt = _wpts(root)[0]
        desc = _long_desc(wpt)
        self.assertIn("Stage 1 description.", desc)
        self.assertIn("Question: What colour is the door?", desc)
        self.assertIn("\n\nQuestion:", desc)  # separator present

    def test_standalone_stage_no_question_unchanged(self):
        _, _, stages = _make_alc(stage_count=1, question="")
        root = _parse_gpx(export_gpx(Geocache.objects.filter(pk=stages[0].pk)))
        desc = _long_desc(_wpts(root)[0])
        self.assertEqual(desc, "Stage 1 description.")
        self.assertNotIn("Question:", desc)

    def test_standalone_stage_empty_base_desc_still_shows_question(self):
        _, _, stages = _make_alc(stage_count=1, question="Find the number.")
        stages[0].long_description = ""
        stages[0].save(update_fields=["long_description"])
        root = _parse_gpx(export_gpx(Geocache.objects.filter(pk=stages[0].pk)))
        desc = _long_desc(_wpts(root)[0])
        self.assertIn("Question: Find the number.", desc)

    def test_child_wp_desc_appends_question(self):
        """Stage exported as child WP under parent should include question in <desc>."""
        adv, parent, stages = _make_alc(stage_count=1, question="How many steps?")
        opts = {"alc_stages": "child_and_export"}
        qs = Geocache.objects.filter(pk__in=[parent.pk, stages[0].pk])
        root = _parse_gpx(export_gpx(qs, opts=opts))
        # wpt[0] = parent, wpt[1] = stage standalone, wpt[2] = stage child WP
        child_wpts = [
            w for w in _wpts(root)
            if w.find(f"{{{GPX_NS}}}type") is not None
            and w.find(f"{{{GPX_NS}}}type").text == "Waypoint|Stage"
        ]
        self.assertEqual(len(child_wpts), 1)
        desc = child_wpts[0].find(f"{{{GPX_NS}}}desc").text
        self.assertIn("Stage 1", desc)
        self.assertIn("Question: How many steps?", desc)

    def test_child_wp_desc_no_question_unchanged(self):
        adv, parent, stages = _make_alc(stage_count=1, question="")
        opts = {"alc_stages": "child_and_export"}
        qs = Geocache.objects.filter(pk__in=[parent.pk, stages[0].pk])
        root = _parse_gpx(export_gpx(qs, opts=opts))
        child_wpts = [
            w for w in _wpts(root)
            if w.find(f"{{{GPX_NS}}}type") is not None
            and w.find(f"{{{GPX_NS}}}type").text == "Waypoint|Stage"
        ]
        self.assertEqual(len(child_wpts), 1)
        desc = child_wpts[0].find(f"{{{GPX_NS}}}desc").text
        self.assertNotIn("Question:", desc)
