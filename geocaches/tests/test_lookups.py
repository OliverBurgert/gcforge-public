"""
Tests for geocaches.importers.lookups — pure helpers, no DB required.
"""

from datetime import date

import django
from django.test import SimpleTestCase

from geocaches.importers.lookups import (
    CACHE_TYPE_MAP,
    CONTAINER_MAP,
    LOG_TYPE_MAP,
    SYM_TO_WAYPOINT_TYPE,
    gpx,
    gpx_attrs_to_status,
    gpx_container_to_size,
    gpx_log_type_to_log_type,
    gpx_sym_to_waypoint_type,
    gpx_type_to_cache_type,
    gs,
    parse_gpx_date,
    unescape,
    NS_GPX,
    NS_GS,
)
from geocaches.models import CacheSize, CacheStatus, CacheType, LogType, WaypointType


class TestNamespaceHelpers(SimpleTestCase):
    def test_gs_returns_clark_notation(self):
        self.assertEqual(gs("cache"), f"{{{NS_GS}}}cache")

    def test_gpx_returns_clark_notation(self):
        self.assertEqual(gpx("wpt"), f"{{{NS_GPX}}}wpt")


class TestCacheTypeMap(SimpleTestCase):
    def test_all_values_are_valid_cache_types(self):
        valid = {c.value for c in CacheType}
        for gpx_str, model_val in CACHE_TYPE_MAP.items():
            self.assertIn(model_val, valid, f"{gpx_str!r} maps to unknown CacheType {model_val!r}")

    def test_traditional(self):
        self.assertEqual(gpx_type_to_cache_type("Traditional Cache"), CacheType.TRADITIONAL)

    def test_multi(self):
        self.assertEqual(gpx_type_to_cache_type("Multi-cache"), CacheType.MULTI)

    def test_mystery(self):
        self.assertEqual(gpx_type_to_cache_type("Unknown Cache"), CacheType.MYSTERY)

    def test_earthcache_both_spellings(self):
        self.assertEqual(gpx_type_to_cache_type("Earthcache"), CacheType.EARTH)
        self.assertEqual(gpx_type_to_cache_type("Earth Cache"), CacheType.EARTH)

    def test_lab_cache(self):
        self.assertEqual(gpx_type_to_cache_type("Lab Cache"), CacheType.LAB)

    def test_unknown_falls_back_to_unknown(self):
        self.assertEqual(gpx_type_to_cache_type("Some Future Type"), CacheType.UNKNOWN)

    def test_empty_string_falls_back_to_unknown(self):
        self.assertEqual(gpx_type_to_cache_type(""), CacheType.UNKNOWN)


class TestContainerMap(SimpleTestCase):
    def test_all_values_are_valid_cache_sizes(self):
        valid = {c.value for c in CacheSize}
        for gpx_str, model_val in CONTAINER_MAP.items():
            self.assertIn(model_val, valid, f"{gpx_str!r} maps to unknown CacheSize {model_val!r}")

    def test_micro(self):
        self.assertEqual(gpx_container_to_size("Micro"), CacheSize.MICRO)

    def test_small(self):
        self.assertEqual(gpx_container_to_size("Small"), CacheSize.SMALL)

    def test_not_chosen(self):
        self.assertEqual(gpx_container_to_size("Not chosen"), CacheSize.UNKNOWN)

    def test_unknown_string(self):
        self.assertEqual(gpx_container_to_size("Unknown"), CacheSize.UNKNOWN)

    def test_unrecognised_falls_back_to_unknown(self):
        self.assertEqual(gpx_container_to_size("Huge"), CacheSize.UNKNOWN)


class TestGpxAttrsToStatus(SimpleTestCase):
    def test_active(self):
        self.assertEqual(gpx_attrs_to_status("False", "True"), CacheStatus.ACTIVE)

    def test_disabled(self):
        self.assertEqual(gpx_attrs_to_status("False", "False"), CacheStatus.DISABLED)

    def test_archived(self):
        self.assertEqual(gpx_attrs_to_status("True", "False"), CacheStatus.ARCHIVED)

    def test_archived_takes_priority_over_available(self):
        # archived=True overrides available=True
        self.assertEqual(gpx_attrs_to_status("True", "True"), CacheStatus.ARCHIVED)

    def test_case_insensitive(self):
        self.assertEqual(gpx_attrs_to_status("true", "false"), CacheStatus.ARCHIVED)
        self.assertEqual(gpx_attrs_to_status("false", "false"), CacheStatus.DISABLED)


class TestLogTypeMap(SimpleTestCase):
    def test_all_values_are_valid_log_types(self):
        valid = {lt.value for lt in LogType}
        for gpx_str, model_val in LOG_TYPE_MAP.items():
            self.assertIn(model_val, valid, f"{gpx_str!r} maps to unknown LogType {model_val!r}")

    def test_found(self):
        self.assertEqual(gpx_log_type_to_log_type("Found it"), LogType.FOUND)

    def test_dnf(self):
        self.assertEqual(gpx_log_type_to_log_type("Didn't find it"), LogType.DNF)

    def test_unknown_falls_back_to_note(self):
        self.assertEqual(gpx_log_type_to_log_type("Unknown Log Type"), LogType.NOTE)


class TestSymToWaypointType(SimpleTestCase):
    def test_all_values_are_valid_waypoint_types(self):
        valid = {wt.value for wt in WaypointType}
        for sym, model_val in SYM_TO_WAYPOINT_TYPE.items():
            self.assertIn(model_val, valid, f"{sym!r} maps to unknown WaypointType {model_val!r}")

    def test_parking(self):
        self.assertEqual(gpx_sym_to_waypoint_type("Parking Area"), WaypointType.PARKING)

    def test_physical_stage(self):
        self.assertEqual(gpx_sym_to_waypoint_type("Physical Stage"), WaypointType.STAGE)

    def test_virtual_stage_maps_to_stage(self):
        self.assertEqual(gpx_sym_to_waypoint_type("Virtual Stage"), WaypointType.STAGE)

    def test_final_location(self):
        self.assertEqual(gpx_sym_to_waypoint_type("Final Location"), WaypointType.FINAL)

    def test_unknown_sym_falls_back_to_other(self):
        self.assertEqual(gpx_sym_to_waypoint_type("Something Else"), WaypointType.OTHER)


class TestUnescape(SimpleTestCase):
    def test_plain_string_unchanged(self):
        self.assertEqual(unescape("hello"), "hello")

    def test_single_encoded_entity(self):
        self.assertEqual(unescape("caf&eacute;"), "café")

    def test_numeric_entity(self):
        self.assertEqual(unescape("&#252;"), "ü")

    def test_double_encoded_entity(self):
        # XML parser decodes &amp; → &, leaving &#252;
        # html.unescape then decodes &#252; → ü
        self.assertEqual(unescape("&#252;"), "ü")

    def test_empty_string(self):
        self.assertEqual(unescape(""), "")


class TestParseGpxDate(SimpleTestCase):
    def test_datetime_string(self):
        self.assertEqual(parse_gpx_date("2005-04-28T00:00:00"), date(2005, 4, 28))

    def test_datetime_with_z(self):
        self.assertEqual(parse_gpx_date("2023-01-15T19:00:00Z"), date(2023, 1, 15))

    def test_date_only_string(self):
        self.assertEqual(parse_gpx_date("2005-04-28"), date(2005, 4, 28))

    def test_empty_string_returns_none(self):
        self.assertIsNone(parse_gpx_date(""))

    def test_invalid_string_returns_none(self):
        self.assertIsNone(parse_gpx_date("not-a-date"))

    def test_datetime_with_milliseconds(self):
        self.assertEqual(parse_gpx_date("2022-10-21T19:00:00.000Z"), date(2022, 10, 21))
