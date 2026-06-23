"""
Tests for the raw WHERE clause filter feature in geocaches/views.py.

Covers:
- Valid SQL is applied and filters correctly
- Invalid SQL sets where_error and does not filter
- Invalid SQL is not saved to recent history
- Valid SQL is saved to recent history
- Named saved clause is resolved and applied
- Named clause that doesn't exist leaves where_sql unchanged
- Empty clause is a no-op
- Filter chips use bg-danger style on error, bg-info on success
- The where_error key is always present in fv (default empty string)
"""

from datetime import date

from django.test import RequestFactory, TestCase

from geocaches.models import CacheSize, CacheStatus, CacheType, Geocache, SavedWhereClause
from geocaches.query import (
    _qualify_where_sql,
    apply_where_clause,
    apply_filters,
    build_filter_chips,
    build_filter_values,
)

TABLE = Geocache._meta.db_table


def _apply_explicit_filters(request, qs):
    """Compatibility shim: replicates old _apply_explicit_filters using query.py helpers."""
    params = request.GET
    qs = apply_filters(qs, params)
    qs, where_sql, where_error = apply_where_clause(qs, params)
    where_name = params.get("where_name", "").strip()
    fv = build_filter_values(params, where_sql, where_error, where_name)
    return qs, fv


def _build_filter_chips(fv):
    return build_filter_chips(fv)


def _make_cache(gc_code, difficulty=2.0, terrain=2.0, name="Test Cache"):
    return Geocache.objects.create(
        gc_code=gc_code,
        name=name,
        cache_type=CacheType.TRADITIONAL,
        size=CacheSize.SMALL,
        status=CacheStatus.ACTIVE,
        latitude=48.0,
        longitude=9.0,
        difficulty=difficulty,
        terrain=terrain,
        hidden_date=date(2020, 1, 1),
    )


def _get(params):
    """Return a fake GET request with the given query-string params."""
    return RequestFactory().get("/", params)


class TestWhereClauseFiltering(TestCase):
    def setUp(self):
        self.easy  = _make_cache("GC00001", difficulty=1.5, terrain=1.5)
        self.hard  = _make_cache("GC00002", difficulty=4.0, terrain=4.0)
        self.mixed = _make_cache("GC00003", difficulty=4.0, terrain=1.5)

    def _apply(self, params):
        qs = Geocache.objects.all()
        return _apply_explicit_filters(_get(params), qs)

    # ------------------------------------------------------------------
    # Happy path
    # ------------------------------------------------------------------

    def test_valid_simple_clause_filters_results(self):
        qs, fv = self._apply({"where_sql": "difficulty > 3"})
        codes = set(qs.values_list("gc_code", flat=True))
        self.assertIn("GC00002", codes)
        self.assertIn("GC00003", codes)
        self.assertNotIn("GC00001", codes)
        self.assertEqual(fv["where_error"], "")

    def test_valid_compound_clause(self):
        qs, fv = self._apply({"where_sql": "difficulty > 3 AND terrain < 3"})
        codes = set(qs.values_list("gc_code", flat=True))
        self.assertEqual(codes, {"GC00003"})
        self.assertEqual(fv["where_error"], "")

    def test_valid_in_clause(self):
        qs, fv = self._apply({"where_sql": "gc_code IN ('GC00001', 'GC00003')"})
        codes = set(qs.values_list("gc_code", flat=True))
        self.assertEqual(codes, {"GC00001", "GC00003"})
        self.assertEqual(fv["where_error"], "")

    def test_empty_where_sql_is_noop(self):
        qs, fv = self._apply({"where_sql": ""})
        self.assertEqual(qs.count(), 3)
        self.assertEqual(fv["where_error"], "")

    def test_missing_where_sql_is_noop(self):
        qs, fv = self._apply({})
        self.assertEqual(qs.count(), 3)
        self.assertEqual(fv["where_error"], "")

    # ------------------------------------------------------------------
    # Error path
    # ------------------------------------------------------------------

    def test_invalid_sql_sets_where_error(self):
        _, fv = self._apply({"where_sql": "this is not sql"})
        self.assertNotEqual(fv["where_error"], "")

    def test_invalid_sql_does_not_filter(self):
        qs, _ = self._apply({"where_sql": "this is not sql"})
        self.assertEqual(qs.count(), 3)

    def test_invalid_sql_preserves_where_sql_in_fv(self):
        _, fv = self._apply({"where_sql": "this is not sql"})
        self.assertEqual(fv["where_sql"], "this is not sql")

    def test_nonexistent_column_sets_where_error(self):
        _, fv = self._apply({"where_sql": "nonexistent_column > 5"})
        self.assertNotEqual(fv["where_error"], "")

    # ------------------------------------------------------------------
    # where_error always present in fv
    # ------------------------------------------------------------------

    def test_where_error_key_present_on_valid(self):
        _, fv = self._apply({"where_sql": "difficulty > 3"})
        self.assertIn("where_error", fv)

    def test_where_error_key_present_on_invalid(self):
        _, fv = self._apply({"where_sql": "bad sql"})
        self.assertIn("where_error", fv)

    def test_where_error_key_present_when_no_clause(self):
        _, fv = self._apply({})
        self.assertIn("where_error", fv)
        self.assertEqual(fv["where_error"], "")


class TestRecentHistory(TestCase):
    def setUp(self):
        _make_cache("GC00001", difficulty=4.0)
        _make_cache("GC00002", difficulty=1.0)

    def _apply(self, params):
        qs = Geocache.objects.all()
        return _apply_explicit_filters(_get(params), qs)

    def test_valid_sql_is_saved_to_recent(self):
        self._apply({"where_sql": "difficulty > 3"})
        self.assertTrue(
            SavedWhereClause.objects.filter(name="", sql="difficulty > 3").exists()
        )

    def test_invalid_sql_is_not_saved_to_recent(self):
        self._apply({"where_sql": "this is not sql"})
        self.assertFalse(
            SavedWhereClause.objects.filter(sql="this is not sql").exists()
        )

    def test_empty_sql_is_not_saved(self):
        self._apply({"where_sql": ""})
        self.assertEqual(SavedWhereClause.objects.count(), 0)


class TestNamedWhereClause(TestCase):
    def setUp(self):
        _make_cache("GC00001", difficulty=4.0)
        _make_cache("GC00002", difficulty=1.0)
        SavedWhereClause.objects.create(name="hard", sql="difficulty > 3")

    def _apply(self, params):
        qs = Geocache.objects.all()
        return _apply_explicit_filters(_get(params), qs)

    def test_named_clause_is_resolved_and_applied(self):
        qs, fv = self._apply({"where_name": "hard"})
        codes = set(qs.values_list("gc_code", flat=True))
        self.assertEqual(codes, {"GC00001"})
        self.assertEqual(fv["where_sql"], "difficulty > 3")
        self.assertEqual(fv["where_error"], "")

    def test_unknown_where_name_leaves_where_sql_as_is(self):
        qs, fv = self._apply({"where_name": "nosuchclause"})
        # Falls back to empty where_sql from GET params (not provided)
        self.assertEqual(fv["where_sql"], "")
        self.assertEqual(qs.count(), 2)

    def test_where_name_takes_priority_over_where_sql(self):
        # where_name resolves to "difficulty > 3"; where_sql param is ignored
        qs, fv = self._apply({"where_name": "hard", "where_sql": "terrain > 10"})
        codes = set(qs.values_list("gc_code", flat=True))
        self.assertEqual(codes, {"GC00001"})
        self.assertEqual(fv["where_sql"], "difficulty > 3")


class TestFilterChips(TestCase):
    """_build_filter_chips should reflect where_error in chip style."""

    def _chips(self, fv):
        return _build_filter_chips(fv)

    def _fv(self, **kwargs):
        base = {
            "q": "", "cache_type": "", "status": "", "size": "", "found": "",
            "flag": "", "elevation": "", "tag": "", "country": "", "state": "",
            "county": "",
            "fname": "", "fname_op": "contains", "fcode": "", "fcode_op": "contains",
            "fowner": "", "fowner_op": "contains", "fplacedby": "",
            "fplacedby_op": "contains", "ftext": "",
            "types": "", "sizes": "", "statuses": "",
            "diff_min": "", "diff_max": "", "terr_min": "", "terr_max": "",
            "fav_min": "", "fav_max": "",
            "hidden_from": "", "hidden_to": "", "lf_from": "", "lf_to": "",
            "fd_from": "", "fd_to": "",
            "flags": "", "flags_not": "", "attrs_yes": "", "attrs_no": "",
            "bearing": "", "radius": "",
            "where_name": "", "where_sql": "", "where_error": "",
        }
        base.update(kwargs)
        return base

    def test_valid_sql_chip_uses_info_style(self):
        chips = self._chips(self._fv(where_sql="difficulty > 3"))
        # chips are (params, label, cls) tuples
        where_chip = next((c for c in chips if "SQL:" in c[1]), None)
        self.assertIsNotNone(where_chip)
        self.assertIn("bg-info", where_chip[2])
        self.assertNotIn("bg-danger", where_chip[2])

    def test_invalid_sql_chip_uses_danger_style(self):
        chips = self._chips(self._fv(
            where_sql="bad sql",
            where_error="near 'sql': syntax error",
        ))
        where_chip = next((c for c in chips if "SQL:" in c[1]), None)
        self.assertIsNotNone(where_chip)
        self.assertIn("bg-danger", where_chip[2])
        self.assertNotIn("bg-info", where_chip[2])

    def test_named_clause_chip_uses_info_style(self):
        chips = self._chips(self._fv(where_name="hard", where_sql="difficulty > 3"))
        where_chip = next((c for c in chips if "Where:" in c[1]), None)
        self.assertIsNotNone(where_chip)
        self.assertIn("bg-info", where_chip[2])

    def test_named_clause_error_chip_uses_danger_style(self):
        chips = self._chips(self._fv(
            where_name="broken",
            where_sql="bad sql",
            where_error="syntax error",
        ))
        where_chip = next((c for c in chips if "Where:" in c[1]), None)
        self.assertIsNotNone(where_chip)
        self.assertIn("bg-danger", where_chip[2])

    def test_no_where_produces_no_where_chip(self):
        chips = self._chips(self._fv())
        where_chip = next(
            (c for c in chips if "SQL:" in c[1] or "Where:" in c[1]), None
        )
        self.assertIsNone(where_chip)


class TestQualifyWhereSql(TestCase):
    """Direct unit tests for ``_qualify_where_sql``.

    The qualifier prefixes bare known-column identifiers with the table name to
    avoid ambiguity, but it must NEVER rewrite text inside SQL string literals
    or double-quoted identifiers.
    """

    def q(self, sql):
        return _qualify_where_sql(sql, TABLE)

    # ------------------------------------------------------------------
    # Bare columns ARE qualified
    # ------------------------------------------------------------------

    def test_bare_column_is_qualified(self):
        self.assertEqual(self.q("county = 'x'"), f"{TABLE}.county = 'x'")

    def test_two_bare_columns_qualified(self):
        self.assertEqual(
            self.q("difficulty > 3 AND terrain < 2"),
            f"{TABLE}.difficulty > 3 AND {TABLE}.terrain < 2",
        )

    def test_already_dotted_name_left_alone(self):
        # The existing dot guard: a name already prefixed isn't touched.
        self.assertEqual(
            self.q(f"{TABLE}.county = 'x'"),
            f"{TABLE}.county = 'x'",
        )

    def test_other_table_dotted_name_left_alone(self):
        self.assertEqual(self.q("cc.latitude > 5"), "cc.latitude > 5")

    # ------------------------------------------------------------------
    # Column-name words inside single-quoted literals are NOT qualified
    # (these FAIL against the pre-fix regex implementation).
    # ------------------------------------------------------------------

    def test_column_word_inside_literal_not_touched(self):
        self.assertEqual(
            self.q("name LIKE '%county%'"),
            f"{TABLE}.name LIKE '%county%'",
        )

    def test_column_word_as_whole_literal_not_touched(self):
        self.assertEqual(
            self.q("owner = 'state road 7'"),
            f"{TABLE}.owner = 'state road 7'",
        )

    def test_multiple_column_words_in_literal_not_touched(self):
        self.assertEqual(
            self.q("hint = 'difficulty and terrain'"),
            f"{TABLE}.hint = 'difficulty and terrain'",
        )

    # ------------------------------------------------------------------
    # Escaped single quotes ('') inside literals must not break detection.
    # ------------------------------------------------------------------

    def test_escaped_quote_inside_literal(self):
        self.assertEqual(
            self.q("owner = 'O''Brien county'"),
            f"{TABLE}.owner = 'O''Brien county'",
        )

    def test_escaped_quote_then_real_column_after_literal(self):
        # The '' escape must not prematurely close the literal, which would
        # cause the trailing "county" (real column) detection to flip.
        self.assertEqual(
            self.q("owner = 'a''b' AND county = 'z'"),
            f"{TABLE}.owner = 'a''b' AND {TABLE}.county = 'z'",
        )

    # ------------------------------------------------------------------
    # Double-quoted identifiers are left alone (SQLite identifier quoting).
    # ------------------------------------------------------------------

    def test_double_quoted_identifier_not_qualified(self):
        self.assertEqual(self.q('"county" = 1'), '"county" = 1')

    def test_double_quoted_word_with_real_column_after(self):
        self.assertEqual(
            self.q('"county" = county'),
            f'"county" = {TABLE}.county',
        )

    # ------------------------------------------------------------------
    # Realistic mixed clause.
    # ------------------------------------------------------------------

    def test_realistic_mixed_clause(self):
        self.assertEqual(
            self.q("county = 'Foo' AND name LIKE '%county%' AND difficulty >= 3"),
            f"{TABLE}.county = 'Foo' AND {TABLE}.name LIKE '%county%' "
            f"AND {TABLE}.difficulty >= 3",
        )

    # ------------------------------------------------------------------
    # Non-column words (keywords / functions / unknown identifiers) untouched.
    # ------------------------------------------------------------------

    def test_keywords_and_unknown_words_untouched(self):
        # LIKE/AND are not Geocache columns; nonexistent_column is unknown.
        self.assertEqual(
            self.q("nonexistent_column LIKE 'x' AND difficulty > 1"),
            f"nonexistent_column LIKE 'x' AND {TABLE}.difficulty > 1",
        )

    def test_function_name_not_a_column(self):
        # CAST/INTEGER aren't columns; bearing_deg isn't a Geocache column.
        sql = "CAST(bearing_deg AS INTEGER) IN (1, 2)"
        self.assertEqual(self.q(sql), sql)

    # ------------------------------------------------------------------
    # The 360-stats subquery shape: real columns qualified, subquery column
    # names (not Geocache columns) left alone, no literals to corrupt.
    # ------------------------------------------------------------------

    def test_360_subquery_shape(self):
        sql = (
            "id IN (SELECT geocache_id FROM geocaches_distancecache "
            "WHERE ref_point_id = 1 AND distance_km <= 5.0) "
            "AND found = 0 AND completed = 0"
        )
        expected = (
            f"{TABLE}.id IN (SELECT geocache_id FROM geocaches_distancecache "
            "WHERE ref_point_id = 1 AND distance_km <= 5.0) "
            f"AND {TABLE}.found = 0 AND {TABLE}.completed = 0"
        )
        self.assertEqual(self.q(sql), expected)
