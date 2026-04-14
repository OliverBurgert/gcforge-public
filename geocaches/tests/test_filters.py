"""
Tests for geocaches/filters.py — tag advanced filter and negative location filter.
"""

from datetime import date

from django.test import TestCase

from geocaches.filters import apply_country_filter, apply_tag_advanced_filter
from geocaches.models import CacheSize, CacheStatus, CacheType, Geocache, Tag


def _make_cache(gc_code, **kwargs):
    defaults = dict(
        name="Test Cache", cache_type=CacheType.TRADITIONAL,
        size=CacheSize.SMALL, status=CacheStatus.ACTIVE,
        latitude=48.0, longitude=9.0, difficulty=2.0, terrain=2.0,
        hidden_date=date(2020, 1, 1), owner="testowner",
    )
    defaults.update(kwargs)
    return Geocache.objects.create(gc_code=gc_code, **defaults)


class TagAdvancedFilterTests(TestCase):

    def setUp(self):
        self.tag_a = Tag.objects.create(name="Favorites")
        self.tag_b = Tag.objects.create(name="To-Do")
        self.tag_c = Tag.objects.create(name="Done")

        self.c1 = _make_cache("GC0001")  # tags: Favorites, To-Do
        self.c1.tags.add(self.tag_a, self.tag_b)

        self.c2 = _make_cache("GC0002")  # tags: Favorites
        self.c2.tags.add(self.tag_a)

        self.c3 = _make_cache("GC0003")  # tags: Done
        self.c3.tags.add(self.tag_c)

        self.c4 = _make_cache("GC0004")  # no tags

        self.qs = Geocache.objects.all()

    # ── Include tests ─────────────────────────────────────────────

    def test_include_single_tag(self):
        result = apply_tag_advanced_filter(self.qs, {"tags_include": "Favorites"})
        self.assertCountEqual(list(result), [self.c1, self.c2])

    def test_include_multiple_tags_and(self):
        """Include is AND — cache must have ALL listed tags."""
        result = apply_tag_advanced_filter(self.qs, {"tags_include": "Favorites,To-Do"})
        self.assertCountEqual(list(result), [self.c1])

    def test_include_none_returns_untagged(self):
        result = apply_tag_advanced_filter(self.qs, {"tags_include": "__none__"})
        self.assertCountEqual(list(result), [self.c4])

    # ── Exclude tests ─────────────────────────────────────────────

    def test_exclude_single_tag(self):
        result = apply_tag_advanced_filter(self.qs, {"tags_exclude": "Done"})
        self.assertCountEqual(list(result), [self.c1, self.c2, self.c4])

    def test_exclude_multiple_tags(self):
        result = apply_tag_advanced_filter(self.qs, {"tags_exclude": "Favorites,Done"})
        self.assertCountEqual(list(result), [self.c4])

    def test_exclude_none_excludes_untagged(self):
        result = apply_tag_advanced_filter(self.qs, {"tags_exclude": "__none__"})
        self.assertCountEqual(list(result), [self.c1, self.c2, self.c3])

    # ── Combined include + exclude ────────────────────────────────

    def test_include_and_exclude(self):
        """Include Favorites AND exclude To-Do → only c2."""
        result = apply_tag_advanced_filter(
            self.qs, {"tags_include": "Favorites", "tags_exclude": "To-Do"}
        )
        self.assertCountEqual(list(result), [self.c2])

    # ── No-op cases ───────────────────────────────────────────────

    def test_empty_params_returns_all(self):
        result = apply_tag_advanced_filter(self.qs, {})
        self.assertEqual(result.count(), 4)

    def test_blank_values_returns_all(self):
        result = apply_tag_advanced_filter(
            self.qs, {"tags_include": "", "tags_exclude": ""}
        )
        self.assertEqual(result.count(), 4)

    def test_whitespace_only_values_returns_all(self):
        result = apply_tag_advanced_filter(
            self.qs, {"tags_include": " , , ", "tags_exclude": " "}
        )
        self.assertEqual(result.count(), 4)

    def test_nonexistent_tag_returns_none(self):
        """Including a tag that no cache has returns empty qs."""
        result = apply_tag_advanced_filter(self.qs, {"tags_include": "NoSuchTag"})
        self.assertEqual(result.count(), 0)


# ---------------------------------------------------------------------------
# Negative location filter tests
# ---------------------------------------------------------------------------


class NegativeLocationFilterTests(TestCase):

    def setUp(self):
        self.de = _make_cache("GC0001", iso_country_code="DE", state="Bavaria", county="Munich")
        self.at = _make_cache("GC0002", iso_country_code="AT", state="Tyrol", county="Innsbruck")
        self.ch = _make_cache("GC0003", iso_country_code="CH", state="Zurich", county="Zurich")
        self.us = _make_cache("GC0004", iso_country_code="US", state="California", county="Los Angeles")
        self.empty = _make_cache("GC0005", iso_country_code="", state="", county="")
        self.qs = Geocache.objects.all()

    # ── Country exclude ───────────────────────────────────────────

    def test_exclude_single_country(self):
        result = apply_country_filter(self.qs, {"country_exclude": "DE"})
        self.assertCountEqual(list(result), [self.at, self.ch, self.us, self.empty])

    def test_exclude_multiple_countries(self):
        result = apply_country_filter(self.qs, {"country_exclude": "DE,AT"})
        self.assertCountEqual(list(result), [self.ch, self.us, self.empty])

    def test_exclude_country_with_whitespace(self):
        result = apply_country_filter(self.qs, {"country_exclude": " DE , AT "})
        self.assertCountEqual(list(result), [self.ch, self.us, self.empty])

    def test_exclude_country_empty_string_noop(self):
        result = apply_country_filter(self.qs, {"country_exclude": ""})
        self.assertEqual(result.count(), 5)

    # ── State exclude ─────────────────────────────────────────────

    def test_exclude_single_state(self):
        result = apply_country_filter(self.qs, {"state_exclude": "Bavaria"})
        self.assertCountEqual(list(result), [self.at, self.ch, self.us, self.empty])

    def test_exclude_multiple_states(self):
        result = apply_country_filter(self.qs, {"state_exclude": "Bavaria,Tyrol"})
        self.assertCountEqual(list(result), [self.ch, self.us, self.empty])

    # ── County exclude ────────────────────────────────────────────

    def test_exclude_single_county(self):
        result = apply_country_filter(self.qs, {"county_exclude": "Munich"})
        self.assertCountEqual(list(result), [self.at, self.ch, self.us, self.empty])

    def test_exclude_multiple_counties(self):
        result = apply_country_filter(self.qs, {"county_exclude": "Munich,Zurich"})
        self.assertCountEqual(list(result), [self.at, self.us, self.empty])

    # ── Combined positive + negative ──────────────────────────────

    def test_positive_country_and_exclude_state(self):
        """Filter to DE, then exclude Bavaria → empty (only DE cache is in Bavaria)."""
        result = apply_country_filter(self.qs, {"country": "DE", "state_exclude": "Bavaria"})
        self.assertEqual(result.count(), 0)

    def test_positive_and_negative_country(self):
        """Positive country=DE plus country_exclude=DE → empty set."""
        result = apply_country_filter(self.qs, {"country": "DE", "country_exclude": "AT"})
        self.assertCountEqual(list(result), [self.de])

    def test_exclude_all_countries(self):
        result = apply_country_filter(self.qs, {"country_exclude": "DE,AT,CH,US"})
        self.assertCountEqual(list(result), [self.empty])

    # ── No-op / edge cases ────────────────────────────────────────

    def test_no_params_returns_all(self):
        result = apply_country_filter(self.qs, {})
        self.assertEqual(result.count(), 5)

    def test_exclude_nonexistent_country_noop(self):
        result = apply_country_filter(self.qs, {"country_exclude": "XX"})
        self.assertEqual(result.count(), 5)

    def test_whitespace_only_exclude_noop(self):
        result = apply_country_filter(self.qs, {"country_exclude": " , , "})
        self.assertEqual(result.count(), 5)
