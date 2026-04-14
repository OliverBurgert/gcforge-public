"""
Tests for geocaches/query.py — reusable query helpers.

Covers:
- mine_q() with and without UserAccounts
- apply_scope() with and without active scope
- apply_filters() delegates to FILTER_CHAIN
- apply_where_clause() with valid SQL, invalid SQL, empty clause
- annotate_distance() adds distance_km and bearing_deg
- apply_radius_filter() with km and mi units
- apply_bearing_filter() with single and multiple directions
- build_filter_values() builds correct dict from params
- build_filter_chips() returns correct chips for various active filters
- apply_all() integrates all pieces together
"""

from datetime import date
from unittest.mock import patch

from django.test import TestCase

from geocaches.models import CacheSize, CacheStatus, CacheType, Geocache, SavedWhereClause
from geocaches.query import (
    BEARING_RANGES,
    annotate_distance,
    apply_all,
    apply_bearing_filter,
    apply_filters,
    apply_radius_filter,
    apply_scope,
    apply_where_clause,
    build_filter_chips,
    build_filter_values,
    mine_q,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_cache(gc_code, *, lat=48.0, lon=9.0, difficulty=2.0, terrain=2.0,
                name="Test Cache", owner="testowner", owner_gc_id=None,
                found=False, oc_code=""):
    return Geocache.objects.create(
        gc_code=gc_code,
        name=name,
        cache_type=CacheType.TRADITIONAL,
        size=CacheSize.SMALL,
        status=CacheStatus.ACTIVE,
        latitude=lat,
        longitude=lon,
        difficulty=difficulty,
        terrain=terrain,
        hidden_date=date(2020, 1, 1),
        owner=owner,
        owner_gc_id=owner_gc_id,
        found=found,
        oc_code=oc_code,
    )


class _FakeRef:
    """Minimal stand-in for a ReferencePoint with lat/lon."""
    def __init__(self, lat, lon, pk=1):
        self.latitude = lat
        self.longitude = lon
        self.pk = pk


def _params(**kwargs):
    """Return a plain dict that acts as request.GET for query helpers."""
    return kwargs


# ---------------------------------------------------------------------------
# mine_q()
# ---------------------------------------------------------------------------

class TestMineQ(TestCase):
    def test_no_accounts_returns_always_false_q(self):
        _make_cache("GC00001", owner="anyone")
        q = mine_q()
        qs = Geocache.objects.filter(q)
        self.assertEqual(qs.count(), 0)

    def test_gc_account_with_user_id_matches_by_owner_gc_id(self):
        _make_cache("GC00001", owner_gc_id=42)
        _make_cache("GC00002", owner_gc_id=99)
        from accounts.models import UserAccount
        UserAccount.objects.create(platform="gc", username="myuser", user_id="42")
        q = mine_q()
        codes = set(Geocache.objects.filter(q).values_list("gc_code", flat=True))
        self.assertIn("GC00001", codes)
        self.assertNotIn("GC00002", codes)

    def test_account_without_user_id_matches_by_username(self):
        _make_cache("GC00001", owner="myuser")
        _make_cache("GC00002", owner="other")
        from accounts.models import UserAccount
        UserAccount.objects.create(platform="gc", username="myuser", user_id="")
        q = mine_q()
        codes = set(Geocache.objects.filter(q).values_list("gc_code", flat=True))
        self.assertIn("GC00001", codes)
        self.assertNotIn("GC00002", codes)

    def test_multiple_accounts_combined(self):
        _make_cache("GC00001", owner="alice")
        _make_cache("GC00002", owner="bob")
        _make_cache("GC00003", owner="charlie")
        from accounts.models import UserAccount
        # user_id must be distinct due to unique constraint on (platform, user_id)
        UserAccount.objects.create(platform="gc", username="alice", user_id="101")
        UserAccount.objects.create(platform="oc", username="bob", user_id="102")
        q = mine_q()
        codes = set(Geocache.objects.filter(q).values_list("gc_code", flat=True))
        self.assertIn("GC00001", codes)
        self.assertIn("GC00002", codes)
        self.assertNotIn("GC00003", codes)


# ---------------------------------------------------------------------------
# apply_scope()
# ---------------------------------------------------------------------------

class TestApplyScope(TestCase):
    def setUp(self):
        self.found_cache = _make_cache("GC00001", found=True)
        self.unfound_cache = _make_cache("GC00002", found=False)

    def test_all_scope_flags_true_returns_all_caches(self):
        from preferences.models import UserPreference
        # Defaults are all True — no filtering should occur
        qs = apply_scope(Geocache.objects.all())
        self.assertEqual(qs.count(), 2)

    def test_scope_found_false_excludes_found_caches(self):
        from preferences.models import UserPreference
        UserPreference.set("scope_found", False)
        try:
            qs = apply_scope(Geocache.objects.all())
            codes = set(qs.values_list("gc_code", flat=True))
            self.assertNotIn("GC00001", codes)
        finally:
            UserPreference.objects.filter(key="scope_found").delete()

    def test_scope_unfound_false_excludes_unfound_caches(self):
        from preferences.models import UserPreference
        UserPreference.set("scope_unfound", False)
        try:
            qs = apply_scope(Geocache.objects.all())
            codes = set(qs.values_list("gc_code", flat=True))
            self.assertNotIn("GC00002", codes)
        finally:
            UserPreference.objects.filter(key="scope_unfound").delete()

    def test_platform_gc_false_excludes_gc_caches(self):
        from preferences.models import UserPreference
        UserPreference.set("scope_platform_gc", False)
        try:
            qs = apply_scope(Geocache.objects.all())
            codes = set(qs.values_list("gc_code", flat=True))
            # GC00001 and GC00002 start with GC — both should be excluded
            self.assertNotIn("GC00001", codes)
            self.assertNotIn("GC00002", codes)
        finally:
            UserPreference.objects.filter(key="scope_platform_gc").delete()


# ---------------------------------------------------------------------------
# apply_filters()
# ---------------------------------------------------------------------------

class TestApplyFilters(TestCase):
    def setUp(self):
        self.easy = _make_cache("GC00001", difficulty=1.0)
        self.hard = _make_cache("GC00002", difficulty=5.0)

    def test_delegates_to_filter_chain_type_filter(self):
        # apply_filters should call apply_type_filter via FILTER_CHAIN
        qs = apply_filters(Geocache.objects.all(), {"type": CacheType.TRADITIONAL})
        self.assertEqual(qs.count(), 2)

    def test_delegates_to_filter_chain_quick_search(self):
        qs = apply_filters(Geocache.objects.all(), {"q": "GC00001"})
        codes = set(qs.values_list("gc_code", flat=True))
        self.assertEqual(codes, {"GC00001"})

    def test_empty_params_returns_all(self):
        qs = apply_filters(Geocache.objects.all(), {})
        self.assertEqual(qs.count(), 2)

    def test_all_filter_chain_functions_called(self):
        """Verify FILTER_CHAIN is actually iterated — patch one function."""
        from geocaches import filters as _f
        original = _f.apply_type_filter
        calls = []

        def spy(qs, params):
            calls.append(params)
            return original(qs, params)

        _f.apply_type_filter = spy
        # Also patch the chain reference in query.py
        from geocaches import query as _q
        original_chain = _q.FILTER_CHAIN[:]
        _q.FILTER_CHAIN[_q.FILTER_CHAIN.index(original)] = spy
        try:
            apply_filters(Geocache.objects.all(), {"type": "Traditional"})
            self.assertTrue(len(calls) > 0)
        finally:
            _q.FILTER_CHAIN[_q.FILTER_CHAIN.index(spy)] = original
            _f.apply_type_filter = original


# ---------------------------------------------------------------------------
# apply_where_clause()
# ---------------------------------------------------------------------------

class TestApplyWhereClause(TestCase):
    def setUp(self):
        self.easy = _make_cache("GC00001", difficulty=1.5)
        self.hard = _make_cache("GC00002", difficulty=4.5)

    def test_valid_sql_filters_correctly(self):
        qs, where_sql, where_error = apply_where_clause(
            Geocache.objects.all(), {"where_sql": "difficulty > 3"}
        )
        codes = set(qs.values_list("gc_code", flat=True))
        self.assertIn("GC00002", codes)
        self.assertNotIn("GC00001", codes)
        self.assertEqual(where_error, "")
        self.assertEqual(where_sql, "difficulty > 3")

    def test_invalid_sql_sets_where_error(self):
        qs, where_sql, where_error = apply_where_clause(
            Geocache.objects.all(), {"where_sql": "this is not sql at all"}
        )
        self.assertNotEqual(where_error, "")
        self.assertEqual(qs.count(), 2)  # unfiltered

    def test_invalid_sql_is_not_saved_to_recent(self):
        apply_where_clause(
            Geocache.objects.all(), {"where_sql": "this is not sql at all"}
        )
        self.assertFalse(SavedWhereClause.objects.filter(sql="this is not sql at all").exists())

    def test_empty_clause_is_noop(self):
        qs, where_sql, where_error = apply_where_clause(
            Geocache.objects.all(), {}
        )
        self.assertEqual(qs.count(), 2)
        self.assertEqual(where_sql, "")
        self.assertEqual(where_error, "")

    def test_named_clause_resolved_and_applied(self):
        SavedWhereClause.objects.create(name="hard", sql="difficulty > 3")
        qs, where_sql, where_error = apply_where_clause(
            Geocache.objects.all(), {"where_name": "hard"}
        )
        codes = set(qs.values_list("gc_code", flat=True))
        self.assertEqual(codes, {"GC00002"})
        self.assertEqual(where_sql, "difficulty > 3")
        self.assertEqual(where_error, "")

    def test_unknown_named_clause_leaves_empty_where_sql(self):
        qs, where_sql, where_error = apply_where_clause(
            Geocache.objects.all(), {"where_name": "nonexistent"}
        )
        self.assertEqual(qs.count(), 2)
        self.assertEqual(where_sql, "")

    def test_valid_sql_saved_to_recent_history(self):
        apply_where_clause(
            Geocache.objects.all(), {"where_sql": "difficulty > 3"}
        )
        self.assertTrue(SavedWhereClause.objects.filter(name="", sql="difficulty > 3").exists())


# ---------------------------------------------------------------------------
# annotate_distance()
# ---------------------------------------------------------------------------

class TestAnnotateDistance(TestCase):
    def setUp(self):
        # Stuttgart area — well-known haversine reference
        self.cache = _make_cache("GC00001", lat=48.7758, lon=9.1829)

    def test_distance_km_annotation_added(self):
        ref = _FakeRef(48.7758, 9.1829)  # same coords → distance ~0
        qs = annotate_distance(Geocache.objects.all(), ref)
        row = qs.get(gc_code="GC00001")
        self.assertAlmostEqual(float(row.distance_km), 0.0, places=1)

    def test_bearing_deg_annotation_added(self):
        ref = _FakeRef(48.7758, 9.1829)
        qs = annotate_distance(Geocache.objects.all(), ref)
        row = qs.get(gc_code="GC00001")
        # bearing_deg should exist (value is undefined at distance=0, but field exists)
        self.assertTrue(hasattr(row, "bearing_deg"))

    def test_distant_cache_has_nonzero_distance(self):
        # Paris is ~410 km from Stuttgart
        ref = _FakeRef(48.8566, 2.3522)  # Paris
        qs = annotate_distance(Geocache.objects.all(), ref)
        row = qs.get(gc_code="GC00001")
        self.assertGreater(float(row.distance_km), 100)


# ---------------------------------------------------------------------------
# apply_radius_filter()
# ---------------------------------------------------------------------------

class TestApplyRadiusFilter(TestCase):
    def setUp(self):
        # One cache near Stuttgart, one near Munich (~200 km apart)
        self.near = _make_cache("GC_NEAR", lat=48.7758, lon=9.1829)
        self.far  = _make_cache("GC_FAR",  lat=48.1374, lon=11.5755)

    def _annotated_qs(self, ref_lat=48.7758, ref_lon=9.1829):
        ref = _FakeRef(ref_lat, ref_lon)
        return annotate_distance(Geocache.objects.all(), ref)

    def test_radius_km_includes_nearby_excludes_far(self):
        qs = self._annotated_qs()
        qs = apply_radius_filter(qs, "10", "km")
        codes = set(qs.values_list("gc_code", flat=True))
        self.assertIn("GC_NEAR", codes)
        self.assertNotIn("GC_FAR", codes)

    def test_radius_km_large_includes_both(self):
        qs = self._annotated_qs()
        qs = apply_radius_filter(qs, "500", "km")
        self.assertEqual(qs.count(), 2)

    def test_radius_mi_converts_correctly(self):
        # 10 miles ≈ 16 km — should include the near cache only
        qs = self._annotated_qs()
        qs = apply_radius_filter(qs, "10", "mi")
        codes = set(qs.values_list("gc_code", flat=True))
        self.assertIn("GC_NEAR", codes)
        self.assertNotIn("GC_FAR", codes)

    def test_invalid_radius_string_returns_unfiltered_qs(self):
        qs = self._annotated_qs()
        original_count = qs.count()
        qs = apply_radius_filter(qs, "notanumber", "km")
        self.assertEqual(qs.count(), original_count)


# ---------------------------------------------------------------------------
# apply_bearing_filter()
# ---------------------------------------------------------------------------

class TestApplyBearingFilter(TestCase):
    def setUp(self):
        # Create caches at known bearings from reference (48.7758, 9.1829)
        # North: higher latitude, same longitude
        self.north_cache = _make_cache("GC_NORTH", lat=49.0, lon=9.1829)
        # East: same latitude, higher longitude
        self.east_cache  = _make_cache("GC_EAST",  lat=48.7758, lon=10.5)

    def _annotated_qs(self):
        ref = _FakeRef(48.7758, 9.1829)
        return annotate_distance(Geocache.objects.all(), ref)

    def test_single_direction_north_filters_correctly(self):
        qs = self._annotated_qs()
        qs = apply_bearing_filter(qs, "N")
        codes = set(qs.values_list("gc_code", flat=True))
        self.assertIn("GC_NORTH", codes)
        self.assertNotIn("GC_EAST", codes)

    def test_single_direction_east_filters_correctly(self):
        qs = self._annotated_qs()
        qs = apply_bearing_filter(qs, "E")
        codes = set(qs.values_list("gc_code", flat=True))
        self.assertIn("GC_EAST", codes)
        self.assertNotIn("GC_NORTH", codes)

    def test_multiple_directions_combined(self):
        qs = self._annotated_qs()
        qs = apply_bearing_filter(qs, "N,E")
        codes = set(qs.values_list("gc_code", flat=True))
        self.assertIn("GC_NORTH", codes)
        self.assertIn("GC_EAST", codes)

    def test_empty_bearing_returns_unfiltered(self):
        qs = self._annotated_qs()
        original_count = qs.count()
        qs = apply_bearing_filter(qs, "")
        self.assertEqual(qs.count(), original_count)

    def test_bearing_ranges_cover_all_eight_directions(self):
        self.assertEqual(set(BEARING_RANGES.keys()), {"N", "NE", "E", "SE", "S", "SW", "W", "NW"})


# ---------------------------------------------------------------------------
# build_filter_values()
# ---------------------------------------------------------------------------

class TestBuildFilterValues(TestCase):
    def _base_fv(self):
        return build_filter_values({})

    def test_all_expected_keys_present(self):
        fv = self._base_fv()
        expected_keys = [
            "q", "cache_type", "status", "size", "found", "flag", "elevation",
            "tag", "country", "country_exclude",
            "state", "county", "state_exclude", "county_exclude",
            "missing",
            "fname", "fname_op", "fcode", "fcode_op",
            "fowner", "fowner_op", "fplacedby", "fplacedby_op", "ftext",
            "types", "sizes", "statuses",
            "diff_min", "diff_max", "terr_min", "terr_max",
            "fav_min", "fav_max",
            "hidden_from", "hidden_to", "lf_from", "lf_to", "fd_from", "fd_to",
            "flags", "flags_not", "attrs_yes", "attrs_no",
            "bearing", "radius",
            "where_name", "where_sql", "where_error",
        ]
        for key in expected_keys:
            self.assertIn(key, fv, f"Missing key: {key}")

    def test_params_populate_correctly(self):
        fv = build_filter_values({"q": "  test  ", "type": "Traditional", "radius": " 50 "})
        self.assertEqual(fv["q"], "test")
        self.assertEqual(fv["cache_type"], "Traditional")
        self.assertEqual(fv["radius"], "50")

    def test_default_op_values(self):
        fv = self._base_fv()
        self.assertEqual(fv["fname_op"], "contains")
        self.assertEqual(fv["fcode_op"], "contains")
        self.assertEqual(fv["fowner_op"], "contains")
        self.assertEqual(fv["fplacedby_op"], "contains")

    def test_where_params_passed_through(self):
        fv = build_filter_values({}, where_sql="difficulty > 3", where_error="err", where_name="myfilter")
        self.assertEqual(fv["where_sql"], "difficulty > 3")
        self.assertEqual(fv["where_error"], "err")
        self.assertEqual(fv["where_name"], "myfilter")

    def test_empty_params_gives_empty_strings(self):
        fv = self._base_fv()
        self.assertEqual(fv["q"], "")
        self.assertEqual(fv["cache_type"], "")
        self.assertEqual(fv["where_error"], "")


# ---------------------------------------------------------------------------
# build_filter_chips()
# ---------------------------------------------------------------------------

class TestBuildFilterChips(TestCase):
    def _empty_fv(self, **overrides):
        base = build_filter_values({})
        base.update(overrides)
        return base

    def test_no_active_filters_produces_no_chips(self):
        chips = build_filter_chips(self._empty_fv())
        self.assertEqual(chips, [])

    def test_state_filter_produces_chip(self):
        chips = build_filter_chips(self._empty_fv(state="Baden-Württemberg"))
        labels = [c[1] for c in chips]
        self.assertTrue(any("State:" in l for l in labels))

    def test_county_filter_produces_chip(self):
        chips = build_filter_chips(self._empty_fv(county="Stuttgart"))
        labels = [c[1] for c in chips]
        self.assertTrue(any("County:" in l for l in labels))

    def test_fname_filter_produces_chip_with_op_label(self):
        chips = build_filter_chips(self._empty_fv(fname="test", fname_op="contains"))
        labels = [c[1] for c in chips]
        self.assertTrue(any("Name contains: test" in l for l in labels))

    def test_diff_range_chip(self):
        chips = build_filter_chips(self._empty_fv(diff_min="2", diff_max="4"))
        labels = [c[1] for c in chips]
        self.assertTrue(any("D:" in l for l in labels))

    def test_flags_chip_uses_success_style(self):
        chips = build_filter_chips(self._empty_fv(flags="ftf"))
        flag_chips = [c for c in chips if "FTF" in c[1]]
        self.assertTrue(len(flag_chips) > 0)
        self.assertIn("bg-success", flag_chips[0][2])

    def test_flags_not_chip_uses_danger_style(self):
        chips = build_filter_chips(self._empty_fv(flags_not="dnf"))
        flag_chips = [c for c in chips if "DNF" in c[1]]
        self.assertTrue(len(flag_chips) > 0)
        self.assertIn("bg-danger", flag_chips[0][2])

    def test_where_sql_chip_uses_info_style_when_no_error(self):
        chips = build_filter_chips(self._empty_fv(where_sql="difficulty > 3"))
        where_chips = [c for c in chips if "SQL:" in c[1]]
        self.assertTrue(len(where_chips) > 0)
        self.assertIn("bg-info", where_chips[0][2])

    def test_country_exclude_chip(self):
        chips = build_filter_chips(self._empty_fv(country_exclude="DE,AT"))
        labels = [c[1] for c in chips]
        self.assertTrue(any("Not in:" in l for l in labels))
        exc_chips = [c for c in chips if "Not in:" in c[1]]
        self.assertIn("bg-danger", exc_chips[0][2])

    def test_state_exclude_chip(self):
        chips = build_filter_chips(self._empty_fv(state_exclude="Bavaria"))
        labels = [c[1] for c in chips]
        self.assertTrue(any("Not state: Bavaria" in l for l in labels))

    def test_county_exclude_chip(self):
        chips = build_filter_chips(self._empty_fv(county_exclude="Munich"))
        labels = [c[1] for c in chips]
        self.assertTrue(any("Not county: Munich" in l for l in labels))

    def test_where_sql_chip_uses_danger_style_when_error(self):
        chips = build_filter_chips(self._empty_fv(where_sql="bad", where_error="syntax error"))
        where_chips = [c for c in chips if "SQL:" in c[1]]
        self.assertTrue(len(where_chips) > 0)
        self.assertIn("bg-danger", where_chips[0][2])

    def test_bearing_chip_produced(self):
        chips = build_filter_chips(self._empty_fv(bearing="N,E"))
        labels = [c[1] for c in chips]
        self.assertTrue(any("Bearing:" in l for l in labels))

    def test_chip_is_tuple_of_three(self):
        chips = build_filter_chips(self._empty_fv(state="Bavaria"))
        for chip in chips:
            self.assertEqual(len(chip), 3)


# ---------------------------------------------------------------------------
# apply_all()
# ---------------------------------------------------------------------------

class TestApplyAll(TestCase):
    def setUp(self):
        self.c1 = _make_cache("GC00001", difficulty=1.0, lat=48.7758, lon=9.1829)
        self.c2 = _make_cache("GC00002", difficulty=5.0, lat=48.7758, lon=9.1829)
        self.c3 = _make_cache("GC00003", difficulty=3.0, lat=49.0, lon=9.1829)

    def test_returns_qs_and_fv_tuple(self):
        qs, fv = apply_all(Geocache.objects.all(), {})
        self.assertIsNotNone(qs)
        self.assertIsInstance(fv, dict)

    def test_fv_has_all_required_keys(self):
        _, fv = apply_all(Geocache.objects.all(), {})
        self.assertIn("q", fv)
        self.assertIn("where_error", fv)
        self.assertIn("radius", fv)

    def test_filter_param_applies_correctly(self):
        qs, fv = apply_all(Geocache.objects.all(), {"where_sql": "difficulty > 4"})
        codes = set(qs.values_list("gc_code", flat=True))
        self.assertIn("GC00002", codes)
        self.assertNotIn("GC00001", codes)

    def test_with_ref_annotates_distance(self):
        ref = _FakeRef(48.7758, 9.1829)
        qs, fv = apply_all(Geocache.objects.all(), {}, ref=ref)
        # Should be able to order by distance_km without error
        rows = list(qs.order_by("distance_km"))
        self.assertEqual(len(rows), 3)

    def test_with_ref_and_radius_filters_by_distance(self):
        ref = _FakeRef(48.7758, 9.1829)
        # GC_NEAR is at the reference; GC_FAR is ~200 km away
        near = _make_cache("GC_NEAR2", lat=48.7758, lon=9.1829)
        far  = _make_cache("GC_FAR2",  lat=48.1374, lon=11.5755)
        qs, fv = apply_all(Geocache.objects.all(), {"radius": "10"}, ref=ref)
        codes = set(qs.values_list("gc_code", flat=True))
        # Near caches (GC00001, GC00002 also at same coords) should be included
        self.assertIn("GC_NEAR2", codes)
        self.assertNotIn("GC_FAR2", codes)

    def test_with_ref_and_bearing_filters_by_direction(self):
        ref = _FakeRef(48.7758, 9.1829)
        # GC00003 is directly north of reference (lat=49.0, same lon)
        # GC00001 and GC00002 are at the exact same coords as ref (bearing=0 = also N range)
        # Filter for South — neither GC00001/2 nor GC00003 should be in S direction
        _make_cache("GC_SOUTH_T", lat=48.0, lon=9.1829)  # south of reference
        qs, fv = apply_all(Geocache.objects.all(), {"bearing": "S"}, ref=ref)
        codes = set(qs.values_list("gc_code", flat=True))
        self.assertIn("GC_SOUTH_T", codes)
        self.assertNotIn("GC00003", codes)  # GC00003 is north

    def test_no_ref_skips_distance_annotation(self):
        qs, fv = apply_all(Geocache.objects.all(), {}, ref=None)
        # Should not raise, and distance_km should not be annotated
        row = qs.first()
        self.assertFalse(hasattr(row, "distance_km"))

    def test_distance_unit_mi_converts_radius(self):
        ref = _FakeRef(48.7758, 9.1829)
        near = _make_cache("GC_NEAR3", lat=48.7758, lon=9.1829)
        far  = _make_cache("GC_FAR3",  lat=48.1374, lon=11.5755)
        # 10 miles ≈ 16 km — should include near, exclude far
        qs, fv = apply_all(Geocache.objects.all(), {"radius": "10"}, ref=ref, distance_unit="mi")
        codes = set(qs.values_list("gc_code", flat=True))
        self.assertIn("GC_NEAR3", codes)
        self.assertNotIn("GC_FAR3", codes)
