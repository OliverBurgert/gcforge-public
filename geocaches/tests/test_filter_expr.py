"""
Tests for geocaches/filter_expr.py — phase 1 of the v2 filter system.

Covers:
  * Tree round-trip (dict + URL encoding)
  * Depth validation (2-level cap)
  * AND / OR / NOT compile semantics, including nested
  * Each condition category compiles to the expected result set
  * M2M negation safety (the qs.exclude vs ~Q gotcha)
  * Legacy URL params → tree shim returns the same result set as the
    existing apply_* chain for representative param dicts
"""

from datetime import date, timedelta

from django.db.models import Q
from django.test import TestCase

from geocaches.filter_expr import (
    MAX_DEPTH,
    OP_AND,
    OP_NOT,
    OP_OR,
    Condition,
    FilterExprError,
    Group,
    compile_tree,
    count_conditions,
    from_url_param,
    to_url_param,
    validate_depth,
)
from geocaches.models import Attribute, CacheSize, CacheStatus, CacheType, Geocache, Tag
from geocaches.query import apply_all, apply_filter_expr


def _make_cache(gc_code, **kwargs):
    defaults = dict(
        name="Test Cache",
        cache_type=CacheType.TRADITIONAL,
        size=CacheSize.SMALL,
        status=CacheStatus.ACTIVE,
        latitude=48.0,
        longitude=9.0,
        difficulty=2.0,
        terrain=2.0,
        hidden_date=date(2020, 1, 1),
        owner="testowner",
    )
    defaults.update(kwargs)
    return Geocache.objects.create(gc_code=gc_code, **defaults)


# ---------------------------------------------------------------------------
# Tree structure
# ---------------------------------------------------------------------------

class TreeStructureTests(TestCase):
    def test_to_from_dict_roundtrip(self):
        tree = Group(OP_AND, [
            Condition("name", "contains", "abc"),
            Group(OP_OR, [
                Condition("difficulty", "gte", 3.0),
                Condition("terrain", "gte", 4.0),
            ]),
        ])
        rebuilt = Group.from_dict(tree.to_dict())
        self.assertEqual(rebuilt.to_dict(), tree.to_dict())

    def test_url_param_roundtrip_preserves_unicode_and_dict_values(self):
        tree = Group(OP_AND, [
            Condition("name", "contains", "Bärwurz Höhe"),
            Condition("difficulty", "between", {"gte": 2.0, "lte": 4.5}),
            Condition("hidden_date", "between", {"gte": "2020-01-01", "lte": "2024-12-31"}),
        ])
        decoded = from_url_param(to_url_param(tree))
        self.assertEqual(decoded.to_dict(), tree.to_dict())

    def test_unknown_group_op_raises(self):
        with self.assertRaises(FilterExprError):
            Group("xor", [])

    def test_from_dict_rejects_unknown_child(self):
        with self.assertRaises(FilterExprError):
            Group.from_dict({"g": "and", "c": [{"weird": "thing"}]})

    def test_from_url_param_rejects_malformed(self):
        with self.assertRaises(FilterExprError):
            from_url_param("this-is-not-base64-zlib-json")


class DepthValidationTests(TestCase):
    def test_root_group_is_depth_1(self):
        tree = Group(OP_AND, [Condition("name", "contains", "x")])
        validate_depth(tree)  # no raise

    def test_nested_group_is_depth_2(self):
        tree = Group(OP_AND, [
            Condition("name", "contains", "x"),
            Group(OP_OR, [
                Condition("difficulty", "gte", 3),
                Condition("difficulty", "lte", 1),
            ]),
        ])
        validate_depth(tree)  # no raise

    def test_depth_3_raises(self):
        tree = Group(OP_AND, [
            Group(OP_OR, [
                Group(OP_AND, [Condition("name", "contains", "x")]),
            ]),
        ])
        with self.assertRaises(FilterExprError):
            validate_depth(tree)

    def test_max_depth_is_2(self):
        self.assertEqual(MAX_DEPTH, 2)


# ---------------------------------------------------------------------------
# AND / OR / NOT
# ---------------------------------------------------------------------------

class CompileSemanticsTests(TestCase):
    def setUp(self):
        self.c1 = _make_cache("GC0001", name="Alpha Cache", difficulty=1.0)
        self.c2 = _make_cache("GC0002", name="Beta Cache", difficulty=3.0)
        self.c3 = _make_cache("GC0003", name="Gamma Beta", difficulty=5.0)

    def _ids(self, tree):
        q = compile_tree(tree)
        return set(Geocache.objects.filter(q).values_list("gc_code", flat=True))

    def test_and(self):
        tree = Group(OP_AND, [
            Condition("name", "contains", "Beta"),
            Condition("difficulty", "gte", 4.0),
        ])
        self.assertEqual(self._ids(tree), {"GC0003"})

    def test_or(self):
        tree = Group(OP_OR, [
            Condition("name", "contains", "Alpha"),
            Condition("difficulty", "gte", 4.0),
        ])
        self.assertEqual(self._ids(tree), {"GC0001", "GC0003"})

    def test_not_scalar(self):
        tree = Group(OP_AND, [
            Group(OP_NOT, [Condition("name", "contains", "Beta")]),
        ])
        self.assertEqual(self._ids(tree), {"GC0001"})

    def test_nested_and_or(self):
        # name contains "Beta" AND (difficulty <= 1.5 OR difficulty >= 5)
        tree = Group(OP_AND, [
            Condition("name", "contains", "Beta"),
            Group(OP_OR, [
                Condition("difficulty", "lte", 1.5),
                Condition("difficulty", "gte", 5.0),
            ]),
        ])
        self.assertEqual(self._ids(tree), {"GC0003"})

    def test_empty_group_is_noop(self):
        # An empty AND group must not filter anything out.
        tree = Group(OP_AND, [])
        self.assertEqual(self._ids(tree), {"GC0001", "GC0002", "GC0003"})

    def test_not_requires_single_child(self):
        with self.assertRaises(FilterExprError):
            compile_tree(Group(OP_NOT, [
                Condition("name", "contains", "a"),
                Condition("name", "contains", "b"),
            ]))

    def test_unknown_condition_raises(self):
        with self.assertRaises(FilterExprError):
            compile_tree(Condition("not_a_real_field", "contains", "x"))


# ---------------------------------------------------------------------------
# Condition compilers — covering each category at least once
# ---------------------------------------------------------------------------

class ConditionCompilerTests(TestCase):
    def setUp(self):
        self.tag_a = Tag.objects.create(name="alpha-tag")
        self.tag_b = Tag.objects.create(name="beta-tag")

        self.c1 = _make_cache(
            "GC0001", name="Hidden in Germany",
            iso_country_code="DE", state="Bavaria",
        )
        self.c1.tags.add(self.tag_a)

        self.c2 = _make_cache(
            "GC0002", name="Found in Germany",
            iso_country_code="DE", state="Baden-Württemberg",
            found=True, hidden_date=date(2020, 6, 15),
        )
        self.c2.tags.add(self.tag_b)

        self.c3 = _make_cache(
            "GC0003", name="Across the Border",
            iso_country_code="FR", state="Alsace",
            hidden_date=date(2024, 1, 1), cache_type=CacheType.MULTI,
        )

    def _ids(self, condition_or_tree):
        node = (
            condition_or_tree
            if isinstance(condition_or_tree, (Group, Condition))
            else None
        )
        if node is None:
            raise AssertionError("expected Group or Condition")
        return set(
            Geocache.objects.filter(compile_tree(node)).values_list("gc_code", flat=True)
        )

    def test_text_contains(self):
        self.assertEqual(
            self._ids(Condition("name", "contains", "Germany")),
            {"GC0001", "GC0002"},
        )

    def test_text_in_list_semicolon(self):
        self.assertEqual(
            self._ids(Condition("name", "in_list", "Hidden in Germany;Across the Border")),
            {"GC0001", "GC0003"},
        )

    def test_enum_in_cache_type(self):
        self.assertEqual(
            self._ids(Condition("cache_type", "in", [CacheType.MULTI])),
            {"GC0003"},
        )

    def test_enum_not_in_cache_type(self):
        self.assertEqual(
            self._ids(Condition("cache_type", "not_in", [CacheType.MULTI])),
            {"GC0001", "GC0002"},
        )

    def test_size_respects_size_override(self):
        # GC0002 has size=SMALL but size_override=REGULAR — querying SMALL
        # should NOT return it; querying REGULAR should.
        self.c2.size_override = CacheSize.REGULAR
        self.c2.save(update_fields=["size_override"])
        self.assertEqual(
            self._ids(Condition("size", "in", [CacheSize.SMALL])),
            {"GC0001", "GC0003"},
        )
        self.assertEqual(
            self._ids(Condition("size", "in", [CacheSize.REGULAR])),
            {"GC0002"},
        )

    def test_country_alias_resolves_to_iso_country_code(self):
        # Public name "country" → model field iso_country_code
        self.assertEqual(
            self._ids(Condition("country", "in", ["DE"])),
            {"GC0001", "GC0002"},
        )

    def test_state_or_across_two_values(self):
        # The real-world case from the design discussion:
        # state = Bavaria OR state = Baden-Württemberg
        tree = Group(OP_OR, [
            Condition("state", "in", ["Bavaria"]),
            Condition("state", "in", ["Baden-Württemberg"]),
        ])
        self.assertEqual(self._ids(tree), {"GC0001", "GC0002"})

    def test_bool_is_true(self):
        self.assertEqual(
            self._ids(Condition("found", "is_true", True)),
            {"GC0002"},
        )

    def test_range_between_with_dict_bounds(self):
        self.assertEqual(
            self._ids(Condition("difficulty", "between", {"gte": 1.5, "lte": 2.5})),
            {"GC0001", "GC0002", "GC0003"},  # all default difficulty=2.0
        )

    def test_date_between(self):
        self.assertEqual(
            self._ids(Condition(
                "hidden_date", "between",
                {"gte": "2020-01-01", "lte": "2020-12-31"},
            )),
            {"GC0001", "GC0002"},
        )

    def test_date_in_past_and_in_future(self):
        # Add one cache with a future hidden_date for the in_future test.
        c4 = _make_cache("GC0004", hidden_date=date.today() + timedelta(days=30))

        past_ids = self._ids(Condition("hidden_date", "in_past", True))
        self.assertEqual(past_ids, {"GC0001", "GC0002", "GC0003"})

        future_ids = self._ids(Condition("hidden_date", "in_future", True))
        self.assertEqual(future_ids, {c4.gc_code})

    def test_date_relative_days_range(self):
        # Caches placed 3, 5, and 100 days ago.
        c4 = _make_cache("GC0004", hidden_date=date.today() - timedelta(days=3))
        c5 = _make_cache("GC0005", hidden_date=date.today() - timedelta(days=5))
        c6 = _make_cache("GC0006", hidden_date=date.today() - timedelta(days=100))

        # hidden_date BETWEEN (today-10) AND (today-1)
        ids = self._ids(Condition("hidden_date", "relative_days", {"gte": -10, "lte": -1}))
        self.assertIn(c4.gc_code, ids)
        self.assertIn(c5.gc_code, ids)
        self.assertNotIn(c6.gc_code, ids)
        # Pre-existing 2020-dated caches are not in the window either
        self.assertNotIn("GC0001", ids)

    def test_date_last_n_days(self):
        c4 = _make_cache("GC0004", hidden_date=date.today() - timedelta(days=10))
        ids = self._ids(Condition("hidden_date", "last_n_days", 30))
        self.assertEqual(ids, {c4.gc_code})

    # --- The M2M negation gotcha: this is the load-bearing test --------------

    def test_tags_in_m2m(self):
        self.assertEqual(
            self._ids(Condition("tags", "in", ["alpha-tag"])),
            {"GC0001"},
        )

    def test_tags_not_in_m2m_uses_subquery_form(self):
        # Naive ~Q(tags__name__in=...) on an M2M join double-counts via the
        # outer join.  The pk-in-subquery form is the safe one — assert that
        # negation returns the correct set.
        self.assertEqual(
            self._ids(Condition("tags", "not_in", ["alpha-tag"])),
            {"GC0002", "GC0003"},
        )

    def test_tags_not_in_inside_not_group_is_double_negation(self):
        # NOT (tags not_in [alpha-tag]) == tags in [alpha-tag]
        tree = Group(OP_AND, [
            Group(OP_NOT, [Condition("tags", "not_in", ["alpha-tag"])]),
        ])
        self.assertEqual(self._ids(tree), {"GC0001"})

    def test_tags_is_none(self):
        # c3 has no tags
        self.assertEqual(
            self._ids(Condition("tags", "is_none", True)),
            {"GC0003"},
        )


# LegacyShimEquivalenceTests removed in v2 phase 4d-ii cleanup — those
# tests compared the new compiler's results against legacy apply_* chain
# functions, but the legacy chain has been retired down to the intentional
# residuals that can't be pure-Q (only apply_quick_search, apply_flag_filter,
# apply_elevation_filter, and apply_area_filter remain).  The shim's
# correctness is still covered by ConditionCompilerTests +
# NormalizeToolbarLegacyParamsTests, which test each condition's compiled Q
# directly.


# ---------------------------------------------------------------------------
# Orchestrator integration — phase 2 wiring
# ---------------------------------------------------------------------------

class OrchestratorIntegrationTests(TestCase):
    """Exercise apply_filter_expr and apply_all with realistic param dicts."""

    def setUp(self):
        self.c1 = _make_cache("GC0001", name="Alpha",
                              cache_type=CacheType.TRADITIONAL,
                              iso_country_code="DE", state="Bavaria")
        self.c2 = _make_cache("GC0002", name="Beta",
                              cache_type=CacheType.TRADITIONAL,
                              iso_country_code="DE", state="Saxony")
        self.c3 = _make_cache("GC0003", name="Gamma",
                              cache_type=CacheType.MULTI,
                              iso_country_code="FR")

    @staticmethod
    def _encode(tree):
        return to_url_param(tree)

    def test_fx_alone_filters(self):
        tree = Group(OP_OR, [
            Condition("state", "in", ["Bavaria"]),
            Condition("state", "in", ["Saxony"]),
        ])
        qs, fx, err, count = apply_filter_expr(
            Geocache.objects.all(), {"fx": self._encode(tree)}
        )
        self.assertEqual(err, "")
        self.assertEqual(count, 2)
        self.assertEqual(
            set(qs.values_list("gc_code", flat=True)),
            {"GC0001", "GC0002"},
        )

    def test_fx_composes_with_quick_search_via_and(self):
        # apply_all runs after the view normalises legacy params into fx.  The
        # only "legacy" URL params that still reach apply_all directly are the
        # intentional residuals that don't fit the pure-Q compiler: q,
        # elevation, geo, exotic flag.  Use ``?q=`` here for the
        # AND-composition check.
        tree = Group(OP_AND, [Condition("cache_type", "in", [CacheType.TRADITIONAL])])
        params = {
            "q": "a",            # quick search (matches Alpha + Gamma via name)
            "fx": self._encode(tree),
        }
        qs, fv = apply_all(Geocache.objects.all(), params)
        # Traditional caches (GC0001=Alpha, GC0002=Beta) intersected with name~'a'
        # → only GC0001 (Beta doesn't contain 'a'... wait it does).  Both match.
        self.assertEqual(
            set(qs.values_list("gc_code", flat=True)),
            {"GC0001", "GC0002"},  # Alpha, Beta — both Traditional, both contain 'a'
        )
        self.assertEqual(fv["fx"], params["fx"])
        self.assertEqual(fv["fx_error"], "")

    def test_fx_missing_is_noop(self):
        qs, fx, err, count = apply_filter_expr(Geocache.objects.all(), {})
        self.assertEqual(qs.count(), 3)
        self.assertEqual(fx, "")
        self.assertEqual(err, "")
        self.assertEqual(count, 0)

    def test_fx_malformed_leaves_qs_unchanged_and_records_error(self):
        # Garbage param should not crash and should not narrow the result.
        params = {"fx": "definitely-not-valid-base64-zlib-json"}
        qs, fv = apply_all(Geocache.objects.all(), params)
        self.assertEqual(qs.count(), 3)  # nothing filtered out
        self.assertEqual(fv["fx"], params["fx"])
        self.assertNotEqual(fv["fx_error"], "")

    def test_fx_chip_appears_in_filter_values(self):
        # The fv dict carries fx + fx_error keys so chip rendering can pick
        # them up.  Phase 2 ships a minimal "Custom filter" chip; this test
        # asserts the wiring rather than the chip label itself.
        tree = Group(OP_AND, [Condition("name", "contains", "Alpha")])
        params = {"fx": self._encode(tree)}
        _qs, fv = apply_all(Geocache.objects.all(), params)
        self.assertIn("fx", fv)
        self.assertIn("fx_error", fv)
        self.assertTrue(fv["fx"])
        self.assertFalse(fv["fx_error"])

    def test_fx_count_reflects_leaf_count(self):
        # Three leaves spread across an outer AND with a nested OR — the chip
        # should report 3 conditions, not 2 (groups don't count).
        tree = Group(OP_AND, [
            Condition("name", "contains", "Alpha"),
            Group(OP_OR, [
                Condition("state", "in", ["Bavaria"]),
                Condition("state", "in", ["Saxony"]),
            ]),
        ])
        _qs, fv = apply_all(Geocache.objects.all(), {"fx": self._encode(tree)})
        self.assertEqual(fv["fx_count"], 3)

    def test_fx_count_zero_when_missing_or_invalid(self):
        _qs, fv = apply_all(Geocache.objects.all(), {})
        self.assertEqual(fv["fx_count"], 0)
        _qs, fv = apply_all(Geocache.objects.all(), {"fx": "garbage"})
        self.assertEqual(fv["fx_count"], 0)

    def test_f_param_resolves_saved_filter_tree(self):
        from geocaches.models import SavedFilter
        tree = Group(OP_AND, [Condition("state", "in", ["Bavaria"])])
        SavedFilter.objects.create(
            name="bavaria-only", tree=tree.to_dict(),
        )
        # state=Bavaria is c1 only.
        _qs, fv = apply_all(Geocache.objects.all(), {"f": "bavaria-only"})
        # the queryset itself
        qs, _ = apply_all(Geocache.objects.all(), {"f": "bavaria-only"})
        self.assertEqual(
            set(qs.values_list("gc_code", flat=True)),
            {"GC0001"},
        )
        self.assertEqual(fv["f_name"], "bavaria-only")
        self.assertEqual(fv["fx_error"], "")

    def test_f_param_unknown_name_surfaces_error(self):
        _qs, fv = apply_all(Geocache.objects.all(), {"f": "no-such-filter"})
        self.assertEqual(fv["f_name"], "no-such-filter")
        self.assertIn("not found", fv["fx_error"])

    def test_distance_condition_without_ref_does_not_crash(self):
        # Regression: a tree referencing distance_km used to FieldError because
        # apply_all annotated distance only when ref was set, and after
        # apply_filter_expr at that.  Now distance_km is annotated as NULL
        # when no ref is active, so the filter compiles cleanly and matches
        # nothing.
        tree = Group(OP_AND, [Condition("distance", "lte", 10.0)])
        qs, fv = apply_all(Geocache.objects.all(), {"fx": self._encode(tree)})
        self.assertEqual(qs.count(), 0)
        self.assertEqual(fv["fx_error"], "")

    def test_bearing_condition_without_ref_does_not_crash(self):
        tree = Group(OP_AND, [Condition("bearing", "direction_in", ["N"])])
        qs, fv = apply_all(Geocache.objects.all(), {"fx": self._encode(tree)})
        self.assertEqual(qs.count(), 0)
        self.assertEqual(fv["fx_error"], "")

    def test_f_and_fx_compose_with_and(self):
        from geocaches.models import SavedFilter
        # Saved filter: state in Bavaria
        SavedFilter.objects.create(
            name="bav", tree=Group(OP_AND, [Condition("state", "in", ["Bavaria"])]).to_dict(),
        )
        # fx: cache_type = Traditional
        fx = self._encode(Group(OP_AND, [
            Condition("cache_type", "in", [CacheType.TRADITIONAL]),
        ]))
        # All three caches are Traditional by default; only c1 is in Bavaria.
        qs, fv = apply_all(Geocache.objects.all(), {"f": "bav", "fx": fx})
        self.assertEqual(set(qs.values_list("gc_code", flat=True)), {"GC0001"})
        # fx_count = 1 (fx) + 1 (saved tree) = 2
        self.assertEqual(fv["fx_count"], 2)


# ---------------------------------------------------------------------------
# Distance / bearing (annotation-dependent) and attributes (M2M)
# ---------------------------------------------------------------------------

class AnnotationDependentTests(TestCase):
    """Distance and bearing conditions assume the qs has been annotated
    with ``distance_km`` / ``bearing_deg``.  Tests synthesize those
    annotations directly so they don't depend on a ReferencePoint fixture."""

    def setUp(self):
        self.c1 = _make_cache("GC0001")
        self.c2 = _make_cache("GC0002")
        self.c3 = _make_cache("GC0003")
        self.c4 = _make_cache("GC0004")

    def _annotated(self, distances, bearings):
        from django.db.models import Case, FloatField, Value, When
        d_when = [
            When(gc_code=code, then=Value(km))
            for code, km in distances.items()
        ]
        b_when = [
            When(gc_code=code, then=Value(deg))
            for code, deg in bearings.items()
        ]
        return Geocache.objects.annotate(
            distance_km=Case(*d_when, output_field=FloatField()),
            bearing_deg=Case(*b_when, output_field=FloatField()),
        )

    def _ids(self, condition, distances, bearings):
        qs = self._annotated(distances, bearings)
        return set(qs.filter(compile_tree(condition)).values_list("gc_code", flat=True))

    def test_distance_lte(self):
        dists = {"GC0001": 1.0, "GC0002": 5.0, "GC0003": 20.0, "GC0004": 100.0}
        bears = {c: 0.0 for c in dists}
        self.assertEqual(
            self._ids(Condition("distance", "lte", 10.0), dists, bears),
            {"GC0001", "GC0002"},
        )

    def test_distance_between(self):
        dists = {"GC0001": 1.0, "GC0002": 5.0, "GC0003": 20.0, "GC0004": 100.0}
        bears = {c: 0.0 for c in dists}
        self.assertEqual(
            self._ids(
                Condition("distance", "between", {"gte": 4.0, "lte": 30.0}),
                dists, bears,
            ),
            {"GC0002", "GC0003"},
        )

    def test_bearing_direction_in_north_wraps(self):
        # North wraps: 338–360 and 0–23.  GC0001 at 5° and GC0002 at 350°
        # should both match "N".  GC0003 at 90° (E) should not.
        dists = {c: 1.0 for c in ("GC0001", "GC0002", "GC0003", "GC0004")}
        bears = {"GC0001": 5.0, "GC0002": 350.0, "GC0003": 90.0, "GC0004": 180.0}
        self.assertEqual(
            self._ids(Condition("bearing", "direction_in", ["N"]), dists, bears),
            {"GC0001", "GC0002"},
        )

    def test_bearing_direction_in_multiple(self):
        dists = {c: 1.0 for c in ("GC0001", "GC0002", "GC0003", "GC0004")}
        bears = {"GC0001": 5.0, "GC0002": 90.0, "GC0003": 180.0, "GC0004": 270.0}
        self.assertEqual(
            self._ids(
                Condition("bearing", "direction_in", ["E", "W"]),
                dists, bears,
            ),
            {"GC0002", "GC0004"},
        )

    def test_bearing_direction_in_unknown_matches_nothing(self):
        dists = {c: 1.0 for c in ("GC0001",)}
        bears = {"GC0001": 0.0}
        self.assertEqual(
            self._ids(Condition("bearing", "direction_in", ["XYZ"]), dists, bears),
            set(),
        )

    def test_bearing_degrees_between_simple(self):
        dists = {c: 1.0 for c in ("GC0001", "GC0002", "GC0003", "GC0004")}
        bears = {"GC0001": 30.0, "GC0002": 100.0, "GC0003": 200.0, "GC0004": 350.0}
        self.assertEqual(
            self._ids(
                Condition("bearing", "degrees_between", {"gte": 60, "lte": 250}),
                dists, bears,
            ),
            {"GC0002", "GC0003"},
        )

    def test_bearing_degrees_between_wraps_through_north(self):
        # gte=300, lte=60 means "300–360 OR 0–60" — i.e. the slice through
        # north.  GC0001 at 30° and GC0004 at 350° both qualify; GC0002 at
        # 100° and GC0003 at 200° do not.
        dists = {c: 1.0 for c in ("GC0001", "GC0002", "GC0003", "GC0004")}
        bears = {"GC0001": 30.0, "GC0002": 100.0, "GC0003": 200.0, "GC0004": 350.0}
        self.assertEqual(
            self._ids(
                Condition("bearing", "degrees_between", {"gte": 300, "lte": 60}),
                dists, bears,
            ),
            {"GC0001", "GC0004"},
        )


class AttributesConditionTests(TestCase):
    def setUp(self):
        self.attr_dogs = Attribute.objects.create(
            source=Attribute.Source.GC, attribute_id=1, name="Dogs", is_positive=True,
        )
        self.attr_kid = Attribute.objects.create(
            source=Attribute.Source.GC, attribute_id=2, name="Kid friendly", is_positive=True,
        )
        self.attr_bike = Attribute.objects.create(
            source=Attribute.Source.GC, attribute_id=3, name="Bicycle", is_positive=True,
        )

        self.c1 = _make_cache("GC0001")
        self.c1.attributes.add(self.attr_dogs, self.attr_kid)

        self.c2 = _make_cache("GC0002")
        self.c2.attributes.add(self.attr_kid)

        self.c3 = _make_cache("GC0003")
        self.c3.attributes.add(self.attr_bike)

        self.c4 = _make_cache("GC0004")  # no attributes

    def _ids(self, condition):
        return set(
            Geocache.objects.filter(compile_tree(condition)).values_list("gc_code", flat=True)
        )

    def test_has_all(self):
        # Must have both Dogs AND Kid → only GC0001
        self.assertEqual(
            self._ids(Condition("attributes", "has_all", [self.attr_dogs.id, self.attr_kid.id])),
            {"GC0001"},
        )

    def test_has_any(self):
        # Has at least one of {Dogs, Bicycle} → GC0001 and GC0003
        self.assertEqual(
            self._ids(Condition("attributes", "has_any", [self.attr_dogs.id, self.attr_bike.id])),
            {"GC0001", "GC0003"},
        )

    def test_has_none(self):
        # Must not have any of {Dogs, Bicycle} → GC0002 and GC0004
        self.assertEqual(
            self._ids(Condition("attributes", "has_none", [self.attr_dogs.id, self.attr_bike.id])),
            {"GC0002", "GC0004"},
        )

    def test_has_none_inside_not_group_is_double_negation(self):
        # NOT (attrs has_none [Dogs]) == attrs has_any [Dogs]
        tree = Group(OP_AND, [
            Group(OP_NOT, [Condition("attributes", "has_none", [self.attr_dogs.id])]),
        ])
        self.assertEqual(self._ids(tree), {"GC0001"})

    def test_csv_string_input(self):
        # JSON usually carries a list, but the compiler also accepts CSV
        # strings for tolerance — handy when crafting URLs by hand.
        self.assertEqual(
            self._ids(Condition(
                "attributes", "has_any", f"{self.attr_dogs.id},{self.attr_bike.id}",
            )),
            {"GC0001", "GC0003"},
        )

    def test_empty_list_is_noop(self):
        # No IDs supplied — should match every cache, not silently match none.
        self.assertEqual(
            self._ids(Condition("attributes", "has_any", [])),
            {"GC0001", "GC0002", "GC0003", "GC0004"},
        )


class AlcConditionsTests(TestCase):
    """Tests for the ALC condition pack (phase 3).

    Fixture layout:
      adv_alice — Adventure by "alice", stage_count=3, is_highly_recommended=True
        stage_a1 — ALStageDetail(geofencing_radius=500, is_final=False), completed=False, found=False
        stage_a2 — ALStageDetail(geofencing_radius=200, is_final=False), completed=True, found=False
        stage_a3 — ALStageDetail(geofencing_radius=0,   is_final=True),  completed=False, found=False
      adv_bob — Adventure by "bob", stage_count=2, is_highly_recommended=False, key_image_url set
        stage_b1 — ALStageDetail(geofencing_radius=1000, is_final=False), completed=False, found=False
        stage_b2 — ALStageDetail(geofencing_radius=300,  is_final=True),  completed=False, found=True
      cache_plain — ordinary Traditional cache (negative control, no ALC linkage)
      cache_adv_parent — cache_type="Adventure Lab" (no al_detail — it's the parent record proxy)
    """

    def setUp(self):
        from geocaches.models.adventure import Adventure, ALStageDetail

        self.adv_alice = Adventure.objects.create(
            code="LC0001",
            title="Alice Adventure",
            owner="alice",
            stage_count=3,
            is_highly_recommended=True,
            latitude=48.0,
            longitude=9.0,
        )
        self.adv_bob = Adventure.objects.create(
            code="LC0002",
            title="Bob Adventure",
            owner="bob",
            stage_count=2,
            is_highly_recommended=False,
            key_image_url="https://example.com/bob-banner.jpg",
            latitude=47.0,
            longitude=8.0,
        )

        # Alice's stages — placed near (48.0, 9.0) so geofence tests are predictable
        self.stage_a1 = _make_cache(
            "ALA001",
            cache_type="Adventure Lab",
            adventure=self.adv_alice,
            latitude=48.0,
            longitude=9.0,
        )
        ALStageDetail.objects.create(
            geocache=self.stage_a1,
            geofencing_radius=500,
            is_final=False,
        )

        self.stage_a2 = _make_cache(
            "ALA002",
            cache_type="Adventure Lab",
            adventure=self.adv_alice,
            latitude=48.001,
            longitude=9.001,
            completed=True,
        )
        ALStageDetail.objects.create(
            geocache=self.stage_a2,
            geofencing_radius=200,
            is_final=False,
        )

        self.stage_a3 = _make_cache(
            "ALA003",
            cache_type="Adventure Lab",
            adventure=self.adv_alice,
            latitude=48.1,
            longitude=9.1,
        )
        ALStageDetail.objects.create(
            geocache=self.stage_a3,
            geofencing_radius=0,
            is_final=True,
        )

        # Bob's stages
        self.stage_b1 = _make_cache(
            "ALB001",
            cache_type="Adventure Lab",
            adventure=self.adv_bob,
            latitude=47.0,
            longitude=8.0,
        )
        ALStageDetail.objects.create(
            geocache=self.stage_b1,
            geofencing_radius=1000,
            is_final=False,
        )

        self.stage_b2 = _make_cache(
            "ALB002",
            cache_type="Adventure Lab",
            adventure=self.adv_bob,
            latitude=47.001,
            longitude=8.001,
            found=True,
        )
        ALStageDetail.objects.create(
            geocache=self.stage_b2,
            geofencing_radius=300,
            is_final=True,
            key_image_url="https://example.com/stage-img.jpg",
        )

        # Non-ALC negative control
        self.cache_plain = _make_cache("GC9999", cache_type="Traditional")

        # Adventure Lab parent-type cache (no al_detail)
        self.cache_adv_parent = _make_cache(
            "GCALP1",
            cache_type="Adventure Lab",
        )

    def _ids(self, condition):
        return set(
            Geocache.objects.filter(compile_tree(condition)).values_list("gc_code", flat=True)
        )

    # 1. is_adventure — cache_type = "Adventure Lab"
    def test_is_adventure(self):
        result = self._ids(Condition("alc", "is_adventure", True))
        self.assertIn("ALA001", result)
        self.assertIn("ALA002", result)
        self.assertIn("GCALP1", result)
        self.assertNotIn("GC9999", result)

    # 2. is_stage — has an ALStageDetail record
    def test_is_stage(self):
        result = self._ids(Condition("alc", "is_stage", True))
        self.assertIn("ALA001", result)
        self.assertIn("ALA002", result)
        self.assertIn("ALB001", result)
        self.assertNotIn("GCALP1", result)   # no al_detail
        self.assertNotIn("GC9999", result)

    # 3. stages_total_between
    def test_stages_total_between(self):
        # adv_alice has stage_count=3, adv_bob has stage_count=2
        result = self._ids(Condition("alc", "stages_total_between", {"gte": 3, "lte": 5}))
        self.assertIn("ALA001", result)
        self.assertIn("ALA002", result)
        self.assertIn("ALA003", result)
        self.assertNotIn("ALB001", result)
        self.assertNotIn("ALB002", result)
        self.assertNotIn("GC9999", result)

    def test_stages_total_gte(self):
        result = self._ids(Condition("alc", "stages_total_gte", 3))
        self.assertIn("ALA001", result)
        self.assertNotIn("ALB001", result)

    def test_stages_total_lte(self):
        result = self._ids(Condition("alc", "stages_total_lte", 2))
        self.assertIn("ALB001", result)
        self.assertNotIn("ALA001", result)

    # 4. stages_completed_gte — counts stages where completed=True OR found=True
    def test_stages_completed_gte(self):
        # adv_alice: stage_a2 has completed=True → 1 completed stage
        # adv_bob: stage_b2 has found=True → 1 completed stage
        result = self._ids(Condition("alc", "stages_completed_gte", 1))
        self.assertIn("ALA001", result)   # part of adv_alice which has 1 completed
        self.assertIn("ALA002", result)
        self.assertIn("ALA003", result)
        self.assertIn("ALB001", result)   # part of adv_bob which has 1 completed
        self.assertIn("ALB002", result)
        self.assertNotIn("GC9999", result)

    def test_stages_completed_lte_zero(self):
        # Adventures with 0 completed stages — neither alice nor bob qualify
        # since each has exactly one completed stage.
        result = self._ids(Condition("alc", "stages_completed_lte", 0))
        self.assertNotIn("ALA001", result)
        self.assertNotIn("ALB001", result)

    # 5. in_progress — matches apply_alc_in_progress_filter directly
    def test_in_progress_matches_legacy_filter(self):
        from geocaches.filters import apply_alc_in_progress_filter
        legacy_ids = set(
            apply_alc_in_progress_filter(Geocache.objects.all())
            .values_list("gc_code", flat=True)
        )
        expr_ids = self._ids(Condition("alc", "in_progress", True))
        self.assertEqual(expr_ids, legacy_ids)

    def test_in_progress_semantics(self):
        # adv_alice: stage_a2 completed, stage_a1 and stage_a3 not → in progress
        # adv_bob: stage_b2 found, stage_b1 not → in progress
        result = self._ids(Condition("alc", "in_progress", True))
        self.assertIn("ALA001", result)
        self.assertIn("ALB001", result)
        self.assertNotIn("GC9999", result)

    # 6. geofence_contains_point
    def test_geofence_contains_point_hit(self):
        # (48.001, 9.001) is ~130m from stage_a1 at (48.0, 9.0); radius=500m → hit
        result = self._ids(Condition("alc", "geofence_contains_point", {"lat": 48.001, "lon": 9.001}))
        self.assertIn("ALA001", result)   # radius 500m, ~130m away — inside

    def test_geofence_contains_point_miss(self):
        # (48.1, 9.1) is ~13km from stage_a1; radius=500m → miss
        result = self._ids(Condition("alc", "geofence_contains_point", {"lat": 48.1, "lon": 9.1}))
        self.assertNotIn("ALA001", result)
        # stage_a3 has geofencing_radius=0 — never matches any point
        self.assertNotIn("ALA003", result)

    def test_geofence_contains_point_zero_radius_never_matches(self):
        # stage_a3 has radius=0 (not > 0) — should never appear
        result = self._ids(Condition("alc", "geofence_contains_point", {"lat": 48.1, "lon": 9.1}))
        self.assertNotIn("ALA003", result)

    def test_geofence_contains_point_missing_coords_is_noop(self):
        # Malformed value → empty Q → matches everything
        q = compile_tree(Condition("alc", "geofence_contains_point", {}))
        self.assertEqual(q, Q())

    # 7. adventure_owner_in / _not_in — pk-in-subquery so NOT grouping works
    def test_adventure_owner_in(self):
        result = self._ids(Condition("alc", "adventure_owner_in", ["alice"]))
        self.assertIn("ALA001", result)
        self.assertIn("ALA002", result)
        self.assertIn("ALA003", result)
        self.assertNotIn("ALB001", result)
        self.assertNotIn("ALB002", result)
        self.assertNotIn("GC9999", result)

    def test_adventure_owner_not_in_complement(self):
        # _not_in uses ~Q(pk__in=...) — check that the complement is correct
        result = self._ids(Condition("alc", "adventure_owner_not_in", ["alice"]))
        self.assertNotIn("ALA001", result)
        self.assertNotIn("ALA002", result)
        self.assertIn("ALB001", result)
        self.assertIn("ALB002", result)
        # cache_plain (no adventure) is also not in alice's set → included in NOT result
        self.assertIn("GC9999", result)

    def test_adventure_owner_not_in_inside_not_group_is_double_negation(self):
        # NOT (adventure_owner_not_in ["alice"]) == adventure_owner_in ["alice"]
        tree = Group(OP_AND, [
            Group(OP_NOT, [Condition("alc", "adventure_owner_not_in", ["alice"])]),
        ])
        result = set(Geocache.objects.filter(compile_tree(tree)).values_list("gc_code", flat=True))
        self.assertIn("ALA001", result)
        self.assertNotIn("ALB001", result)

    # 8. is_final / is_not_final
    def test_is_final(self):
        result = self._ids(Condition("alc", "is_final", True))
        self.assertIn("ALA003", result)   # is_final=True
        self.assertIn("ALB002", result)   # is_final=True
        self.assertNotIn("ALA001", result)
        self.assertNotIn("ALA002", result)
        self.assertNotIn("GC9999", result)

    def test_is_not_final(self):
        result = self._ids(Condition("alc", "is_not_final", True))
        self.assertIn("ALA001", result)
        self.assertIn("ALA002", result)
        self.assertIn("ALB001", result)
        self.assertNotIn("ALA003", result)
        self.assertNotIn("ALB002", result)

    # 9. geofencing_radius_between
    def test_geofencing_radius_between(self):
        # Radii: ALA001=500, ALA002=200, ALA003=0, ALB001=1000, ALB002=300
        result = self._ids(Condition("alc", "geofencing_radius_between", {"gte": 100, "lte": 500}))
        self.assertIn("ALA001", result)    # 500 — within [100, 500]
        self.assertIn("ALA002", result)    # 200
        self.assertIn("ALB002", result)    # 300
        self.assertNotIn("ALA003", result)  # 0
        self.assertNotIn("ALB001", result)  # 1000

    # 10. is_highly_recommended
    def test_is_highly_recommended(self):
        result = self._ids(Condition("alc", "is_highly_recommended", True))
        self.assertIn("ALA001", result)
        self.assertIn("ALA002", result)
        self.assertIn("ALA003", result)
        self.assertNotIn("ALB001", result)
        self.assertNotIn("GC9999", result)

    # 11. has_theme_image — key_image_url set on adv_bob or ALB002's al_detail
    def test_has_theme_image(self):
        result = self._ids(Condition("alc", "has_theme_image", True))
        # ALB001 and ALB002 share adv_bob which has key_image_url set
        self.assertIn("ALB001", result)
        self.assertIn("ALB002", result)
        # ALB002 also has al_detail.key_image_url set
        # ALA* adventures have no key_image_url
        self.assertNotIn("ALA001", result)
        self.assertNotIn("GC9999", result)

    # 12. stages_remaining_between
    def test_stages_remaining_between(self):
        # adv_alice: 3 stages total, 1 completed → 2 remaining
        # adv_bob: 2 stages total, 1 found → 1 remaining
        result = self._ids(Condition("alc", "stages_remaining_between", {"gte": 2, "lte": 5}))
        self.assertIn("ALA001", result)
        self.assertIn("ALA002", result)
        self.assertIn("ALA003", result)
        self.assertNotIn("ALB001", result)  # only 1 remaining
        self.assertNotIn("GC9999", result)

    def test_stages_remaining_gte(self):
        result = self._ids(Condition("alc", "stages_remaining_gte", 2))
        self.assertIn("ALA001", result)
        self.assertNotIn("ALB001", result)

    def test_loggable_from_ref_uses_default_ref(self):
        """alc.loggable_from_ref with no value → uses the user's default ref."""
        from preferences.models import ReferencePoint

        # Set up a default ref at the parent ALC's coordinates so the stage
        # at (48.0, 9.0) with geofence radius 1000m matches.
        ref = ReferencePoint.objects.create(
            name="home", latitude=48.0, longitude=9.0, is_default=True,
        )

        # Build a stage with a geofence radius covering the ref point.
        from geocaches.models import ALStageDetail, Adventure
        adv = Adventure.objects.create(code="LCREF1", title="ref-test", stage_count=1)
        stage = _make_cache(
            "STG001", al_code="ST0001", adventure=adv,
            latitude=48.0, longitude=9.0,  # exactly at the ref
        )
        ALStageDetail.objects.create(geocache=stage, geofencing_radius=500)

        # A second stage 100 km away with radius 1000m won't be reached.
        stage_far = _make_cache(
            "STG002", al_code="ST0002", adventure=adv,
            latitude=49.0, longitude=10.0,
        )
        ALStageDetail.objects.create(geocache=stage_far, geofencing_radius=1000)

        result = self._ids(Condition("alc", "loggable_from_ref", None))
        self.assertIn("STG001", result)
        self.assertNotIn("STG002", result)

        # Explicit ref-pk form: same answer for the default's pk.
        result_by_pk = self._ids(Condition("alc", "loggable_from_ref", ref.pk))
        self.assertEqual(result, result_by_pk)

    def test_loggable_from_ref_no_ref_matches_nothing(self):
        """No ReferencePoint at all → empty result."""
        # Set up an ALC stage that WOULD match if a ref existed.
        from geocaches.models import ALStageDetail, Adventure
        adv = Adventure.objects.create(code="LCN1", title="n", stage_count=1)
        stage = _make_cache(
            "STGN1", al_code="STN1", adventure=adv,
            latitude=48.0, longitude=9.0,
        )
        ALStageDetail.objects.create(geocache=stage, geofencing_radius=500)

        result = self._ids(Condition("alc", "loggable_from_ref", None))
        self.assertEqual(result, set())

    def test_stages_remaining_lte(self):
        result = self._ids(Condition("alc", "stages_remaining_lte", 1))
        self.assertIn("ALB001", result)
        self.assertNotIn("ALA001", result)


# ---------------------------------------------------------------------------
# Logs condition pack — phase 4b
# ---------------------------------------------------------------------------

class LogsConditionsTests(TestCase):
    def setUp(self):
        from geocaches.models import Log

        self.c1 = _make_cache("GC0001")  # last log: Found by Alice
        self.c2 = _make_cache("GC0002")  # last 3 logs all DNF
        self.c3 = _make_cache("GC0003")  # mixed log history, last is Note
        self.c4 = _make_cache("GC0004")  # no logs

        # c1 — Alice found it (2024-06-01), then Bob wrote a note (2024-07-01)
        Log.objects.create(
            geocache=self.c1, log_type="Found it", user_name="Alice",
            logged_date=date(2024, 6, 1),
        )
        Log.objects.create(
            geocache=self.c1, log_type="Write note", user_name="Bob",
            logged_date=date(2024, 7, 1),
        )

        # c2 — three DNFs in a row
        for i, d in enumerate([date(2024, 1, 1), date(2024, 2, 1), date(2024, 3, 1)]):
            Log.objects.create(
                geocache=self.c2, log_type="Didn't find it",
                user_name=f"User{i}", logged_date=d,
            )

        # c3 — DNF, then Found, then a Note (most recent)
        Log.objects.create(
            geocache=self.c3, log_type="Didn't find it", user_name="Carol",
            logged_date=date(2024, 1, 1),
        )
        Log.objects.create(
            geocache=self.c3, log_type="Found it", user_name="Dave",
            logged_date=date(2024, 5, 1),
        )
        Log.objects.create(
            geocache=self.c3, log_type="Write note", user_name="Eve",
            logged_date=date(2024, 8, 1),
        )

    def _ids(self, condition):
        return set(
            Geocache.objects.filter(compile_tree(condition))
            .values_list("gc_code", flat=True)
        )

    def test_last_log_type_in_note(self):
        # c1's last log is a Note (Bob's); c3's last log is also a Note (Eve's).
        # c2 ends with a DNF, c4 has no logs.
        self.assertEqual(
            self._ids(Condition("logs", "last_log_type_in", ["Write note"])),
            {"GC0001", "GC0003"},
        )

    def test_last_log_type_in_dnf(self):
        # Only c2 has a DNF as the most recent log.
        self.assertEqual(
            self._ids(Condition("logs", "last_log_type_in", ["Didn't find it"])),
            {"GC0002"},
        )

    def test_found_by_user_case_insensitive_substring(self):
        # Alice found c1.  "alice" should match.  Dave found c3.
        self.assertEqual(
            self._ids(Condition("logs", "found_by_user", "alice")),
            {"GC0001"},
        )
        self.assertEqual(
            self._ids(Condition("logs", "found_by_user", "DAV")),
            {"GC0003"},
        )

    def test_found_by_user_ignores_dnf_and_note(self):
        # Carol DNF'd c3 and Eve noted it — neither is a "find"; only Dave's
        # Found-it on c3 counts.
        self.assertEqual(
            self._ids(Condition("logs", "found_by_user", "Carol")),
            set(),
        )
        self.assertEqual(
            self._ids(Condition("logs", "found_by_user", "Eve")),
            set(),
        )

    def test_log_count_gte(self):
        # c1: 2 logs, c2: 3, c3: 3, c4: 0
        self.assertEqual(
            self._ids(Condition("logs", "log_count_gte", 3)),
            {"GC0002", "GC0003"},
        )

    def test_last_n_are_dnf_matches_consecutive_streak(self):
        # c2's last 3 finder logs are all DNF.
        self.assertEqual(
            self._ids(Condition("logs", "last_n_are_dnf", 3)),
            {"GC0002"},
        )

    def test_last_n_are_dnf_ignores_intervening_notes(self):
        # c3's logs in order: DNF, Found, Note.  Notes are non-finder, so
        # "last 1 finder log" = Found, not DNF.  c3 should NOT match.
        self.assertEqual(
            self._ids(Condition("logs", "last_n_are_dnf", 1)),
            {"GC0002"},  # only c2 — c1 has a Found, c3 has a Found, c4 has nothing
        )

    def test_last_n_are_dnf_requires_n_logs(self):
        # n=5 — no cache has 5 DNFs.
        self.assertEqual(
            self._ids(Condition("logs", "last_n_are_dnf", 5)),
            set(),
        )

    def test_last_n_are_dnf_invalid_value_noop(self):
        # Matches the convention used by distance/range conditions:
        # unparseable input compiles to Q() (no-op) rather than match-nothing.
        self.assertEqual(
            self._ids(Condition("logs", "last_n_are_dnf", "not a number")),
            {"GC0001", "GC0002", "GC0003", "GC0004"},
        )

    def test_logs_inside_not_group(self):
        # NOT (last_log_type_in [Note]) == caches whose last log is NOT a Note
        # (or which have no logs).
        tree = Group(OP_AND, [
            Group(OP_NOT, [Condition("logs", "last_log_type_in", ["Write note"])]),
        ])
        self.assertEqual(self._ids(tree), {"GC0002", "GC0004"})


# ---------------------------------------------------------------------------
# Area condition pack — phase 4c
# ---------------------------------------------------------------------------

class AreaConditionsTests(TestCase):
    def setUp(self):
        # Caches at known coordinates.
        self.c1 = _make_cache("GC0001", latitude=48.0, longitude=11.5)   # Munich-ish
        self.c2 = _make_cache("GC0002", latitude=48.78, longitude=9.18)  # Stuttgart-ish
        self.c3 = _make_cache("GC0003", latitude=52.52, longitude=13.4)  # Berlin-ish
        self.c4 = _make_cache("GC0004", latitude=40.7, longitude=-74.0)  # NYC

    def _ids(self, condition):
        return set(
            Geocache.objects.filter(compile_tree(condition))
            .values_list("gc_code", flat=True)
        )

    def test_area_inside_rect_southern_germany(self):
        # bbox = south, west, north, east — covers Munich + Stuttgart but
        # not Berlin or NYC.
        regions = [{"type": "rect", "bbox": [47.0, 8.0, 50.0, 13.0]}]
        self.assertEqual(
            self._ids(Condition("area", "inside", regions)),
            {"GC0001", "GC0002"},
        )

    def test_area_inside_circle_around_munich(self):
        # 50 km radius around Munich — Munich in, Stuttgart out (200+ km).
        regions = [{"type": "circle", "center": [48.0, 11.5], "radius_m": 50000}]
        self.assertEqual(
            self._ids(Condition("area", "inside", regions)),
            {"GC0001"},
        )

    def test_area_inside_polygon_germany_box(self):
        # Polygon spanning Germany — closed ring (lng, lat order in the
        # GeoJSON-ish format used by _parse_geo_param).
        regions = [{
            "type": "polygon",
            "coordinates": [
                [6.0, 47.0], [15.5, 47.0],
                [15.5, 55.0], [6.0, 55.0],
                [6.0, 47.0],
            ],
        }]
        self.assertEqual(
            self._ids(Condition("area", "inside", regions)),
            {"GC0001", "GC0002", "GC0003"},
        )

    def test_area_inside_union_of_two_regions(self):
        regions = [
            {"type": "rect", "bbox": [47.5, 8.5, 49.0, 12.0]},  # SW Germany
            {"type": "rect", "bbox": [52.0, 13.0, 53.0, 14.0]},  # Berlin area
        ]
        self.assertEqual(
            self._ids(Condition("area", "inside", regions)),
            {"GC0001", "GC0002", "GC0003"},
        )

    def test_area_outside_excludes_matches(self):
        # Everything outside southern-Germany box → Berlin + NYC.
        regions = [{"type": "rect", "bbox": [47.0, 8.0, 50.0, 13.0]}]
        self.assertEqual(
            self._ids(Condition("area", "outside", regions)),
            {"GC0003", "GC0004"},
        )

    def test_area_outside_inside_or_composes(self):
        # (area inside box1) OR (area outside box2) should be expressible
        # within the depth-2 cap.
        box1 = [{"type": "rect", "bbox": [47.0, 8.0, 50.0, 13.0]}]    # incl GC0001+GC0002
        box2 = [{"type": "rect", "bbox": [40.0, -75.0, 41.0, -73.0]}]  # excl NYC
        tree = Group(OP_OR, [
            Condition("area", "inside", box1),
            Condition("area", "outside", box2),
        ])
        # inside(box1) = {1, 2}; outside(box2) = {1, 2, 3}; union = {1, 2, 3}.
        self.assertEqual(self._ids(tree), {"GC0001", "GC0002", "GC0003"})

    def test_area_inside_empty_list_is_noop(self):
        self.assertEqual(
            self._ids(Condition("area", "inside", [])),
            {"GC0001", "GC0002", "GC0003", "GC0004"},
        )

    def test_area_inside_invalid_value_is_noop(self):
        self.assertEqual(
            self._ids(Condition("area", "inside", "not-a-list")),
            {"GC0001", "GC0002", "GC0003", "GC0004"},
        )


# ---------------------------------------------------------------------------
# Waypoint condition pack — phase 5
# ---------------------------------------------------------------------------

class WaypointConditionsTests(TestCase):
    def setUp(self):
        from geocaches.models import Waypoint
        from geocaches.models.enums import WaypointType

        # GC0001: one Parking waypoint named "Park here"
        self.c1 = _make_cache("GC0001")
        Waypoint.objects.create(
            geocache=self.c1, waypoint_type=WaypointType.PARKING, name="Park here",
        )
        # GC0002: one Final waypoint named "The end", plus a completed Stage
        self.c2 = _make_cache("GC0002")
        Waypoint.objects.create(
            geocache=self.c2, waypoint_type=WaypointType.FINAL, name="The end",
        )
        Waypoint.objects.create(
            geocache=self.c2, waypoint_type=WaypointType.STAGE,
            name="Halfway", is_completed=True,
        )
        # GC0003: three Stage waypoints (none completed), one user-created
        self.c3 = _make_cache("GC0003")
        for i in range(3):
            Waypoint.objects.create(
                geocache=self.c3, waypoint_type=WaypointType.STAGE,
                name=f"Stage {i + 1}",
                is_user_created=(i == 0),
            )
        # GC0004: no waypoints
        self.c4 = _make_cache("GC0004")

    def _ids(self, condition):
        return set(
            Geocache.objects.filter(compile_tree(condition))
            .values_list("gc_code", flat=True)
        )

    def test_has_type_single(self):
        from geocaches.models.enums import WaypointType
        self.assertEqual(
            self._ids(Condition("waypoint", "has_type", [WaypointType.FINAL])),
            {"GC0002"},
        )

    def test_has_type_multiple(self):
        from geocaches.models.enums import WaypointType
        self.assertEqual(
            self._ids(Condition("waypoint", "has_type",
                                [WaypointType.PARKING, WaypointType.FINAL])),
            {"GC0001", "GC0002"},
        )

    def test_not_has_type_m2m_safe(self):
        # Caches that do NOT have a Stage waypoint = GC0001 (Parking) + GC0004 (none)
        from geocaches.models.enums import WaypointType
        self.assertEqual(
            self._ids(Condition("waypoint", "not_has_type", [WaypointType.STAGE])),
            {"GC0001", "GC0004"},
        )

    def test_not_has_type_inside_not_group_is_double_negation(self):
        # NOT (not_has_type [Stage]) == has_type [Stage]
        from geocaches.models.enums import WaypointType
        tree = Group(OP_AND, [
            Group(OP_NOT, [
                Condition("waypoint", "not_has_type", [WaypointType.STAGE]),
            ]),
        ])
        self.assertEqual(self._ids(tree), {"GC0002", "GC0003"})

    def test_count_gte(self):
        # ≥2 waypoints: GC0002 (2) and GC0003 (3); not GC0001 (1) or GC0004 (0)
        self.assertEqual(
            self._ids(Condition("waypoint", "count_gte", 2)),
            {"GC0002", "GC0003"},
        )

    def test_count_gte_zero_matches_everything(self):
        self.assertEqual(
            self._ids(Condition("waypoint", "count_gte", 0)),
            {"GC0001", "GC0002", "GC0003", "GC0004"},
        )

    def test_name_contains_case_insensitive(self):
        # "halfway" matches "Halfway" on GC0002
        self.assertEqual(
            self._ids(Condition("waypoint", "name_contains", "halfway")),
            {"GC0002"},
        )

    def test_name_contains_substring(self):
        # "Stage" matches three waypoints on GC0003
        self.assertEqual(
            self._ids(Condition("waypoint", "name_contains", "Stage")),
            {"GC0003"},
        )

    def test_has_completed(self):
        # Only GC0002 has a completed waypoint
        self.assertEqual(
            self._ids(Condition("waypoint", "has_completed", True)),
            {"GC0002"},
        )

    def test_has_user_created(self):
        # Only GC0003 has a user-created waypoint
        self.assertEqual(
            self._ids(Condition("waypoint", "has_user_created", True)),
            {"GC0003"},
        )

    def test_empty_value_is_noop(self):
        # has_type with empty list → matches everything (no constraint added)
        self.assertEqual(
            self._ids(Condition("waypoint", "has_type", [])),
            {"GC0001", "GC0002", "GC0003", "GC0004"},
        )


class CountConditionsTests(TestCase):
    def test_single_leaf(self):
        self.assertEqual(count_conditions(Condition("name", "contains", "a")), 1)

    def test_flat_group(self):
        tree = Group(OP_AND, [
            Condition("name", "contains", "a"),
            Condition("state", "in", ["BY"]),
        ])
        self.assertEqual(count_conditions(tree), 2)

    def test_nested_group(self):
        tree = Group(OP_AND, [
            Condition("name", "contains", "a"),
            Group(OP_OR, [
                Condition("state", "in", ["BY"]),
                Condition("state", "in", ["BW"]),
                Condition("state", "in", ["SN"]),
            ]),
        ])
        # 1 (name) + 3 (states) = 4
        self.assertEqual(count_conditions(tree), 4)

    def test_empty_group_is_zero(self):
        self.assertEqual(count_conditions(Group(OP_AND, [])), 0)


# ---------------------------------------------------------------------------
# Chip labels (4d-ii-A)
# ---------------------------------------------------------------------------

class ConditionToLabelTests(TestCase):
    """Covers every condition category at least once.  Labels are short and
    user-facing; tests assert the structure rather than the exact wording."""

    def _check(self, field, op, value, expected_substr):
        from geocaches.filter_expr import condition_to_label
        label = condition_to_label(field, op, value)
        self.assertIn(expected_substr, label,
                      f"expected {expected_substr!r} in {label!r} for ({field}, {op}, {value})")

    def test_text(self):
        self._check("name", "contains", "germany", "germany")
        self._check("name", "contains", "germany", "Name")
        self._check("owner", "starts_with", "ali", "starts with")
        self._check("name", "is_empty", None, "is empty")

    def test_enum(self):
        self._check("cache_type", "in", ["Traditional", "Multi"], "Traditional")
        self._check("cache_type", "in", ["Traditional"], "Type:")
        self._check("status", "not_in", ["Archived"], "∉")

    def test_range(self):
        self._check("difficulty", "between", {"gte": 2.0, "lte": 4.0}, "2.0–4.0")
        self._check("terrain", "gte", 3, "≥ 3")
        self._check("fav_points", "lte", 100, "≤ 100")

    def test_bool(self):
        self._check("found", "is_true", True, "✓ Found")
        self._check("ftf", "is_false", True, "✗")

    def test_date_absolute_and_relative(self):
        self._check("hidden_date", "between",
                    {"gte": "2020-01-01", "lte": "2022-12-31"}, "2020-01-01")
        self._check("hidden_date", "last_n_days", 30, "last 30 days")
        self._check("hidden_date", "in_past", True, "in past")
        self._check("hidden_date", "this_year", True, "this year")

    def test_geo(self):
        self._check("country", "in", ["DE"], "Country: DE")
        self._check("state", "not_in", ["Bavaria"], "∉")

    def test_tags(self):
        self._check("tags", "in", ["favs"], "Tags: favs")
        self._check("tags", "is_none", True, "untagged")

    def test_attributes(self):
        self._check("attributes", "has_all", [1, 2], "Attr (all)")
        self._check("attributes", "has_none", [3], "Attr (none)")

    def test_distance_bearing(self):
        self._check("distance", "between", {"gte": 1.0, "lte": 5.0}, "km")
        self._check("bearing", "direction_in", ["N", "NE"], "N, NE")

    def test_logs(self):
        self._check("logs", "last_n_are_dnf", 3, "Last 3 are DNF")
        self._check("logs", "found_by_user", "alice", "Found by: alice")
        self._check("logs", "log_count_gte", 20, "Log count ≥ 20")

    def test_alc(self):
        self._check("alc", "is_adventure", True, "Is adventure")
        self._check("alc", "stages_remaining_gte", 1, "Stages remaining ≥ 1")
        self._check("alc", "geofence_contains_point",
                    {"lat": 48.0, "lon": 11.5}, "(48.0, 11.5)")
        self._check("alc", "adventure_owner_in", ["alice"], "alice")

    def test_area(self):
        self._check("area", "inside", [{"type": "rect", "bbox": [0, 0, 1, 1]}],
                    "Inside area (rect)")

    def test_waypoint(self):
        self._check("waypoint", "has_type", ["Parking"], "Has waypoint: Parking")
        self._check("waypoint", "not_has_type", ["Stage"], "No waypoint: Stage")
        self._check("waypoint", "count_gte", 3, "Waypoint count ≥ 3")
        self._check("waypoint", "name_contains", "trailhead", "trailhead")
        self._check("waypoint", "has_completed", True, "Has completed waypoint")

    def test_unknown_falls_back_safely(self):
        from geocaches.filter_expr import condition_to_label
        label = condition_to_label("mystery_field", "some_op", "x")
        self.assertIn("mystery_field", label)
        self.assertIn("some_op", label)


class TreeChipRenderingTests(TestCase):
    """build_filter_chips should emit one chip per leaf condition, each with
    a precomputed href that drops only that leaf from the URL."""

    def setUp(self):
        from django.test import RequestFactory
        self.factory = RequestFactory()

    def _fv_with_tree(self, tree):
        from geocaches.filter_expr import to_url_param
        return {"fx": to_url_param(tree), "fx_count": 0, "fx_error": "", "f_name": ""}

    def test_two_leaves_render_as_two_chips(self):
        from geocaches.filter_expr import Condition, Group, OP_AND
        from geocaches.query import build_filter_chips
        tree = Group(OP_AND, [
            Condition("name", "contains", "germany"),
            Condition("cache_type", "in", ["Traditional"]),
        ])
        fv = self._fv_with_tree(tree)
        request = self.factory.get("/", {"fx": fv["fx"]})
        chips = build_filter_chips(fv, request=request)
        # Both leaves rendered + no single "Custom filter (…)" chip
        labels = [c[1] for c in chips]
        self.assertEqual(len(chips), 2)
        self.assertTrue(any("germany" in lbl for lbl in labels))
        self.assertTrue(any("Traditional" in lbl for lbl in labels))
        self.assertFalse(any("Custom filter" in lbl for lbl in labels))

    def test_chip_href_drops_only_that_leaf(self):
        from geocaches.filter_expr import (
            Condition, Group, OP_AND, from_url_param,
        )
        from geocaches.query import build_filter_chips
        tree = Group(OP_AND, [
            Condition("name", "contains", "germany"),
            Condition("cache_type", "in", ["Traditional"]),
        ])
        fv = self._fv_with_tree(tree)
        request = self.factory.get("/", {"fx": fv["fx"], "tag": "favs"})
        chips = build_filter_chips(fv, request=request)
        # Each chip's action is "@<href>".  Pull the href, parse, decode the
        # remaining fx and check it has the OTHER leaf only.
        for action, label, _cls in chips:
            self.assertTrue(action.startswith("@"))
            href = action[1:]
            self.assertIn("tag=favs", href, "non-fx params must be preserved")
            # Find the fx= portion
            for kv in href.lstrip("?").split("&"):
                if kv.startswith("fx="):
                    remaining = from_url_param(kv[3:])
                    self.assertEqual(len(remaining.children), 1)
                    kept = remaining.children[0]
                    if "germany" in label:
                        # Removing the germany chip leaves the Traditional one
                        self.assertEqual(kept.field, "cache_type")
                    else:
                        self.assertEqual(kept.field, "name")
                    break

    def test_single_leaf_chip_href_clears_fx_entirely(self):
        from geocaches.filter_expr import Condition, Group, OP_AND
        from geocaches.query import build_filter_chips
        tree = Group(OP_AND, [Condition("name", "contains", "germany")])
        fv = self._fv_with_tree(tree)
        request = self.factory.get("/", {"fx": fv["fx"], "tag": "favs"})
        chips = build_filter_chips(fv, request=request)
        self.assertEqual(len(chips), 1)
        action = chips[0][0]
        href = action[1:]
        self.assertNotIn("fx=", href)
        self.assertIn("tag=favs", href)

    def test_saved_filter_chip_plus_per_leaf_chips(self):
        from geocaches.filter_expr import Condition, Group, OP_AND
        from geocaches.models import SavedFilter
        from geocaches.query import build_filter_chips
        tree = Group(OP_AND, [
            Condition("name", "contains", "alpha"),
            Condition("name", "contains", "beta"),
        ])
        SavedFilter.objects.create(name="combo", tree=tree.to_dict())
        fv = {"fx": "", "fx_count": 2, "fx_error": "", "f_name": "combo"}
        request = self.factory.get("/", {"f": "combo"})
        chips = build_filter_chips(fv, request=request)
        # Expect: 1 "Saved filter: combo" header chip + 2 leaf chips
        labels = [c[1] for c in chips]
        self.assertEqual(len(chips), 3)
        self.assertIn("Saved filter: combo", labels)
        # Leaf chip hrefs must drop the ?f= and write modified ?fx=
        for action, label, _cls in chips:
            if "Saved filter" in label:
                continue
            href = action[1:]
            self.assertNotIn("f=combo", href)
            self.assertIn("fx=", href)

    def test_nested_group_renders_as_summary_chip(self):
        from geocaches.filter_expr import Condition, Group, OP_AND, OP_OR
        from geocaches.query import build_filter_chips
        tree = Group(OP_AND, [
            Condition("name", "contains", "alpha"),
            Group(OP_OR, [
                Condition("state", "in", ["BY"]),
                Condition("state", "in", ["SN"]),
            ]),
        ])
        fv = self._fv_with_tree(tree)
        request = self.factory.get("/", {"fx": fv["fx"]})
        chips = build_filter_chips(fv, request=request)
        labels = [c[1] for c in chips]
        # Two top-level children → two chips; the nested group is summarised
        # in one chip rather than expanded.
        self.assertEqual(len(chips), 2)
        self.assertTrue(any("OR (2 conditions)" in lbl for lbl in labels))

    def test_invalid_fx_falls_back_to_red_single_chip(self):
        from geocaches.query import build_filter_chips
        fv = {"fx": "garbage", "fx_count": 0, "fx_error": "bad", "f_name": ""}
        request = self.factory.get("/", {"fx": "garbage"})
        chips = build_filter_chips(fv, request=request)
        labels = [c[1] for c in chips]
        self.assertIn("Custom filter (invalid)", labels)


# ---------------------------------------------------------------------------
# 4d-ii-B: toolbar legacy → fx normalisation
# ---------------------------------------------------------------------------

class TreeToToolbarStateTests(TestCase):
    def test_extracts_single_value_dropdowns(self):
        from geocaches.filter_expr import Condition, Group, OP_AND, to_url_param
        from geocaches.query import tree_to_toolbar_state
        tree = Group(OP_AND, [
            Condition("cache_type", "in", ["Multi"]),
            Condition("status",     "in", ["Active"]),
            Condition("size",       "in", ["Small"]),
            Condition("country",    "in", ["DE"]),
            Condition("found",      "is_true", True),
        ])
        state = tree_to_toolbar_state(to_url_param(tree))
        self.assertEqual(state["f_type"],    "Multi")
        self.assertEqual(state["f_status"],  "Active")
        self.assertEqual(state["f_size"],    "Small")
        self.assertEqual(state["f_country"], "DE")
        self.assertEqual(state["f_found"],   "1")

    def test_multi_value_condition_does_not_pick_one(self):
        # If the tree's cache_type condition has two values, the single-
        # value toolbar dropdown can't represent it, so f_type stays empty.
        from geocaches.filter_expr import Condition, Group, OP_AND, to_url_param
        from geocaches.query import tree_to_toolbar_state
        tree = Group(OP_AND, [
            Condition("cache_type", "in", ["Multi", "Traditional"]),
        ])
        state = tree_to_toolbar_state(to_url_param(tree))
        self.assertEqual(state["f_type"], "")

    def test_flag_extraction_for_premium(self):
        from geocaches.filter_expr import Condition, Group, OP_AND, to_url_param
        from geocaches.query import tree_to_toolbar_state
        tree = Group(OP_AND, [Condition("is_premium", "is_true", True)])
        state = tree_to_toolbar_state(to_url_param(tree))
        self.assertEqual(state["f_flag"], "is_premium")

    def test_empty_input_returns_empty_state(self):
        from geocaches.query import tree_to_toolbar_state
        state = tree_to_toolbar_state("")
        self.assertEqual(state["f_type"],    "")
        self.assertEqual(state["f_status"],  "")
        self.assertEqual(state["f_found"],   "")
