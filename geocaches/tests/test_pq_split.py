"""
Unit tests for the Pocket Query date-range split proposal feature.

Covers:
- criteria._parse_criteria_soup (offline HTML parsing, via a saved fixture)
- criteria.diff_pq_criteria
- criteria.criteria_to_filter
- criteria._build_save_payload (write-path form serialization, offline)
- split._bucket_by_date
- split.apply_split (with apply_pq_date_range mocked, no live GC call)
"""

from datetime import date
from pathlib import Path
from unittest.mock import patch

from bs4 import BeautifulSoup
from django.test import TestCase

from geocaches.models import CacheSize, CacheStatus, CacheType, Geocache, IgnoreListEntry, IgnoreSource
from geocaches.pq.criteria import (
    _build_save_payload,
    _open_end_sentinel,
    _parse_criteria_soup,
    criteria_to_filter,
    diff_pq_criteria,
)
from geocaches.pq.split import _bucket_by_date, apply_split

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def make_cache(gc_code, **kwargs):
    defaults = dict(
        name=gc_code, latitude=48.5, longitude=9.1,
        cache_type=CacheType.TRADITIONAL, size=CacheSize.REGULAR,
        status=CacheStatus.ACTIVE, found=False, is_premium=False,
    )
    defaults.update(kwargs)
    return Geocache.objects.create(gc_code=gc_code, **defaults)


def base_criteria(**overrides):
    c = {
        "name": "Test",
        "results_cap": 1000,
        "type": {"mode": "any", "type_ids": []},
        "container": {"mode": "any", "size_ids": []},
        "options": {},
        "difficulty": {"enabled": False, "cmp": ">=", "score": 1.0},
        "terrain": {"enabled": False, "cmp": ">=", "score": 1.0},
        "country_state": {"mode": "none"},
        "origin": {"mode": "none", "lat": None, "lon": None, "radius_km": None},
        "placed": {"mode": "none", "date_from": None, "date_to": None},
    }
    c.update(overrides)
    return c


# ---------------------------------------------------------------------------
# _parse_criteria_soup (offline, via fixture)
# ---------------------------------------------------------------------------

class ParseCriteriaSoupTests(TestCase):
    def test_parses_fixture(self):
        html = (FIXTURES / "pq_edit_form.html").read_text(encoding="utf-8")
        soup = BeautifulSoup(html, "html.parser")
        criteria = _parse_criteria_soup(soup)

        self.assertEqual(criteria["name"], "_Testarea 30km 2000-01-01 bis 2011-05-01")
        self.assertEqual(criteria["results_cap"], 1000)
        self.assertEqual(criteria["type"], {"mode": "select", "type_ids": [2, 8]})
        self.assertEqual(criteria["container"], {"mode": "select", "size_ids": [3, 8]})
        self.assertTrue(criteria["options"]["not_found"])
        self.assertTrue(criteria["options"]["not_owned"])
        self.assertTrue(criteria["options"]["not_ignored"])
        self.assertFalse(criteria["options"]["found"])
        self.assertFalse(criteria["difficulty"]["enabled"])
        self.assertEqual(criteria["country_state"], {"mode": "none"})
        self.assertEqual(criteria["origin"]["mode"], "coords")
        self.assertAlmostEqual(criteria["origin"]["lat"], 48 + 31.467 / 60, places=5)
        self.assertAlmostEqual(criteria["origin"]["lon"], 9 + 7.209 / 60, places=5)
        self.assertAlmostEqual(criteria["origin"]["radius_km"], 30.0, places=5)
        self.assertEqual(criteria["placed"], {
            "mode": "between",
            "date_from": date(2000, 1, 1),
            "date_to": date(2011, 5, 1),
        })


# ---------------------------------------------------------------------------
# diff_pq_criteria
# ---------------------------------------------------------------------------

class DiffCriteriaTests(TestCase):
    def test_identical_no_diff(self):
        self.assertEqual(diff_pq_criteria(base_criteria(), base_criteria()), [])

    def test_name_and_placed_are_ignored(self):
        a = base_criteria(name="A", placed={"mode": "between", "date_from": date(2020, 1, 1), "date_to": None})
        b = base_criteria(name="B", placed={"mode": "between", "date_from": date(2021, 1, 1), "date_to": None})
        self.assertEqual(diff_pq_criteria(a, b), [])

    def test_type_diff_detected(self):
        a = base_criteria(type={"mode": "any", "type_ids": []})
        b = base_criteria(type={"mode": "select", "type_ids": [2]})
        diffs = diff_pq_criteria(a, b)
        self.assertEqual(len(diffs), 1)
        self.assertIn("Cache Type", diffs[0])

    def test_multiple_diffs_all_reported(self):
        a = base_criteria()
        b = base_criteria(
            type={"mode": "select", "type_ids": [2]},
            origin={"mode": "coords", "lat": 1.0, "lon": 2.0, "radius_km": 5.0},
        )
        diffs = diff_pq_criteria(a, b)
        self.assertEqual(len(diffs), 2)


# ---------------------------------------------------------------------------
# criteria_to_filter
# ---------------------------------------------------------------------------

class CriteriaToFilterTests(TestCase):
    def test_type_filter(self):
        make_cache("GC1", cache_type=CacheType.TRADITIONAL)
        make_cache("GC2", cache_type=CacheType.MYSTERY)
        q, geo, warnings = criteria_to_filter(base_criteria(type={"mode": "select", "type_ids": [2]}))
        codes = set(Geocache.objects.filter(q).values_list("gc_code", flat=True))
        self.assertEqual(codes, {"GC1"})

    def test_archived_always_excluded(self):
        make_cache("GC1", status=CacheStatus.ACTIVE)
        make_cache("GC2", status=CacheStatus.ARCHIVED)
        q, geo, warnings = criteria_to_filter(base_criteria())
        codes = set(Geocache.objects.filter(q).values_list("gc_code", flat=True))
        self.assertEqual(codes, {"GC1"})

    def test_not_found_filter(self):
        make_cache("GC1", found=False)
        make_cache("GC2", found=True)
        q, geo, warnings = criteria_to_filter(base_criteria(options={"not_found": True}))
        codes = set(Geocache.objects.filter(q).values_list("gc_code", flat=True))
        self.assertEqual(codes, {"GC1"})

    def test_size_respects_override(self):
        make_cache("GC1", size=CacheSize.SMALL, size_override=None)
        make_cache("GC2", size=CacheSize.LARGE, size_override=CacheSize.SMALL)
        make_cache("GC3", size=CacheSize.SMALL, size_override=CacheSize.LARGE)
        q, geo, warnings = criteria_to_filter(base_criteria(container={"mode": "select", "size_ids": [8]}))
        codes = set(Geocache.objects.filter(q).values_list("gc_code", flat=True))
        self.assertEqual(codes, {"GC1", "GC2"})

    def test_not_ignored_filters_and_warns(self):
        make_cache("GC1")
        make_cache("GC2")
        IgnoreListEntry.objects.create(source=IgnoreSource.GC, code="GC2", name="", status="")
        q, geo, warnings = criteria_to_filter(base_criteria(options={"not_ignored": True}))
        codes = set(Geocache.objects.filter(q).values_list("gc_code", flat=True))
        self.assertEqual(codes, {"GC1"})
        self.assertTrue(any("ignore list" in w for w in warnings))

    def test_unsupported_option_produces_warning_but_no_filter(self):
        make_cache("GC1")
        q, geo, warnings = criteria_to_filter(base_criteria(options={"has_travel_bugs": True}))
        codes = set(Geocache.objects.filter(q).values_list("gc_code", flat=True))
        self.assertEqual(codes, {"GC1"})
        self.assertTrue(any("Travel Bugs" in w for w in warnings))

    def test_origin_coords_produces_geo_param(self):
        q, geo, warnings = criteria_to_filter(
            base_criteria(origin={"mode": "coords", "lat": 48.5, "lon": 9.1, "radius_km": 30})
        )
        self.assertEqual(geo, "circle:48.5,9.1,30000")

    def test_origin_home_warns(self):
        q, geo, warnings = criteria_to_filter(base_criteria(origin={"mode": "home", "lat": None, "lon": None, "radius_km": None}))
        self.assertIsNone(geo)
        self.assertTrue(any("home" in w for w in warnings))


# ---------------------------------------------------------------------------
# _bucket_by_date
# ---------------------------------------------------------------------------

class BucketByDateTests(TestCase):
    def test_empty(self):
        self.assertEqual(_bucket_by_date([], cap=990), [])

    def test_all_under_cap_single_bucket(self):
        caches = [(f"GC{i}", date(2020, 1, i + 1)) for i in range(3)]
        buckets = _bucket_by_date(caches, cap=10)
        self.assertEqual([len(b) for b in buckets], [3])

    def test_even_split_on_distinct_dates(self):
        caches = [(f"GC{i}", date(2020, 1, i + 1)) for i in range(5)]
        buckets = _bucket_by_date(caches, cap=2)
        self.assertEqual([len(b) for b in buckets], [2, 2, 1])

    def test_never_splits_a_single_date(self):
        caches = [(f"GC{i}", date(2020, 1, 1)) for i in range(3)]
        buckets = _bucket_by_date(caches, cap=2)
        self.assertEqual([len(b) for b in buckets], [3])

    def test_date_change_closes_bucket_after_overflow(self):
        caches = [
            ("GC1", date(2020, 1, 1)),
            ("GC2", date(2020, 1, 1)),
            ("GC3", date(2020, 1, 2)),
        ]
        buckets = _bucket_by_date(caches, cap=2)
        self.assertEqual([len(b) for b in buckets], [2, 1])


# ---------------------------------------------------------------------------
# _build_save_payload (write path, offline via fixture)
# ---------------------------------------------------------------------------

class BuildSavePayloadTests(TestCase):
    def _load_form(self):
        html = (FIXTURES / "pq_edit_form.html").read_text(encoding="utf-8")
        soup = BeautifulSoup(html, "html.parser")
        return soup.find("form", id="aspnetForm")

    def test_explicit_range_overrides_dates_only(self):
        form = self._load_form()
        payload = _build_save_payload(form, date(2030, 3, 15), date(2031, 6, 20))

        self.assertEqual(payload["ctl00$ContentBody$Placed"], "rbPlacedBetween")
        self.assertEqual(payload["ctl00$ContentBody$DateTimeBegin$Month"], "3")
        self.assertEqual(payload["ctl00$ContentBody$DateTimeBegin$Day"], "15")
        self.assertEqual(payload["ctl00$ContentBody$DateTimeBegin$Year"], "2030")
        self.assertEqual(payload["ctl00$ContentBody$DateTimeEnd$Month"], "6")
        self.assertEqual(payload["ctl00$ContentBody$DateTimeEnd$Day"], "20")
        self.assertEqual(payload["ctl00$ContentBody$DateTimeEnd$Year"], "2031")

        # other criteria round-trip unchanged (checked-only fields carried through)
        self.assertEqual(payload["ctl00$ContentBody$Type"], "rbTypeSelect")
        self.assertEqual(payload["ctl00$ContentBody$cbTaxonomy$0"], "2")
        self.assertEqual(payload["ctl00$ContentBody$cbTaxonomy$5"], "8")
        self.assertNotIn("ctl00$ContentBody$cbTaxonomy$1", payload)
        self.assertEqual(payload["ctl00$ContentBody$Origin"], "rbOriginWpt")
        self.assertEqual(payload["ctl00$ContentBody$rbUnitType"], "km")

        # unchecked checkboxes are absent, not "false"
        self.assertNotIn("ctl00$ContentBody$cbDifficulty", payload)
        self.assertNotIn("ctl00$ContentBody$cbTerrain", payload)
        self.assertNotIn("ctl00$ContentBody$cbIncludePQNameInFileName", payload)

        # only the clicked submit button is present
        self.assertEqual(payload["ctl00$ContentBody$btnSubmit"], "Submit Information")
        self.assertNotIn("ctl00$ContentBody$btnDelete", payload)

        # untouched text fields carried through unchanged
        self.assertEqual(
            payload["ctl00$ContentBody$tbName"],
            "_Testarea 30km 2000-01-01 bis 2011-05-01",
        )
        self.assertEqual(payload["ctl00$ContentBody$tbRadius"], "30")

    def test_open_bounds_use_sentinel_dates(self):
        form = self._load_form()
        payload = _build_save_payload(form, None, None)
        sentinel_end = _open_end_sentinel()

        self.assertEqual(payload["ctl00$ContentBody$DateTimeBegin$Year"], "2000")
        self.assertEqual(payload["ctl00$ContentBody$DateTimeBegin$Month"], "1")
        self.assertEqual(payload["ctl00$ContentBody$DateTimeBegin$Day"], "1")
        self.assertEqual(payload["ctl00$ContentBody$DateTimeEnd$Year"], str(sentinel_end.year))
        self.assertEqual(payload["ctl00$ContentBody$DateTimeEnd$Month"], "12")
        self.assertEqual(payload["ctl00$ContentBody$DateTimeEnd$Day"], "31")

    def test_new_name_overrides_tbname(self):
        form = self._load_form()
        payload = _build_save_payload(form, date(2030, 3, 15), date(2031, 6, 20), "Kusterdingen 3")
        self.assertEqual(payload["ctl00$ContentBody$tbName"], "Kusterdingen 3")

    def test_no_new_name_leaves_tbname_unchanged(self):
        form = self._load_form()
        payload = _build_save_payload(form, date(2030, 3, 15), date(2031, 6, 20))
        self.assertEqual(
            payload["ctl00$ContentBody$tbName"],
            "_Testarea 30km 2000-01-01 bis 2011-05-01",
        )


# ---------------------------------------------------------------------------
# apply_split (apply_pq_date_range mocked — no live GC call)
# ---------------------------------------------------------------------------

class ApplySplitTests(TestCase):
    @patch("geocaches.pq.criteria.apply_pq_date_range")
    def test_partial_failure_does_not_stop_others(self, mock_apply):
        def side_effect(guid, date_from, date_to, new_name=None):
            if guid == "guid-bad":
                raise RuntimeError("boom")
            return (date_from or date(2000, 1, 1), date_to or date(2099, 1, 1), new_name or "unchanged")

        mock_apply.side_effect = side_effect

        items = [
            {"reference_code": "R1", "guid": "guid-ok", "pq_name": "PQ1",
             "date_from": "2020-01-01", "date_to": "2021-01-01"},
            {"reference_code": "R2", "guid": "guid-bad", "pq_name": "PQ2",
             "date_from": "2021-01-01", "date_to": None},
            {"reference_code": "R3", "guid": "guid-ok2", "pq_name": "PQ3",
             "date_from": None, "date_to": None},
        ]
        results = apply_split(items)

        self.assertEqual(len(results), 3)
        self.assertEqual(results[0], {
            "reference_code": "R1", "pq_name": "unchanged", "status": "applied",
            "date_from": "2020-01-01", "date_to": "2021-01-01",
        })
        self.assertEqual(results[1]["status"], "error")
        self.assertEqual(results[1]["error"], "boom")
        self.assertEqual(results[2]["status"], "applied")
        self.assertEqual(mock_apply.call_count, 3)

    @patch("geocaches.pq.criteria.apply_pq_date_range")
    def test_new_name_passed_through_and_reflected_in_result(self, mock_apply):
        mock_apply.return_value = (date(2020, 1, 1), date(2021, 1, 1), "Kusterdingen 1")

        items = [
            {"reference_code": "R1", "guid": "guid-1", "pq_name": "_old name",
             "date_from": "2020-01-01", "date_to": "2021-01-01", "new_name": "Kusterdingen 1"},
        ]
        results = apply_split(items)

        mock_apply.assert_called_once_with("guid-1", date(2020, 1, 1), date(2021, 1, 1), "Kusterdingen 1")
        self.assertEqual(results[0]["pq_name"], "Kusterdingen 1")

    @patch("geocaches.pq.criteria.apply_pq_date_range")
    def test_blank_new_name_is_treated_as_no_rename(self, mock_apply):
        mock_apply.return_value = (date(2020, 1, 1), date(2021, 1, 1), "_old name")

        items = [
            {"reference_code": "R1", "guid": "guid-1", "pq_name": "_old name",
             "date_from": "2020-01-01", "date_to": "2021-01-01", "new_name": ""},
        ]
        apply_split(items)

        mock_apply.assert_called_once_with("guid-1", date(2020, 1, 1), date(2021, 1, 1), None)
