"""Tests for geocaches.templatetags.icon_tags.cache_type_icon.

Covers the Adventure Lab stage variant: a stage (has an adventure, not the
flagged parent) renders inverted (white fill, colored ring, CSS-masked
glyph) instead of the parent's solid-colored disc + plain <img> glyph.
"""
from django.test import TestCase

from geocaches.models import Adventure, CacheType, Geocache
from geocaches.templatetags.icon_tags import cache_type_icon


def _adventure(code="LCICON"):
    return Adventure.objects.create(
        code=code, title="Icon Adventure", latitude=48.5, longitude=9.1,
    )


def _cache(al_code, adv, **kwargs):
    defaults = dict(
        al_code=al_code, name=al_code, cache_type=CacheType.LAB,
        latitude=48.5, longitude=9.1, adventure=adv,
    )
    defaults.update(kwargs)
    return Geocache.objects.create(**defaults)


class CacheTypeIconStageVariantTests(TestCase):
    def test_stage_renders_inverted(self):
        adv = _adventure()
        parent = _cache("LCICON", adv, is_al_parent=True)
        stage = _cache("LCICON-1", adv)

        html = str(cache_type_icon({"icon_set": "cgeo"}, CacheType.LAB, cache=stage))

        self.assertIn("gcf-type-icon-stage", html)
        self.assertIn("background:#fff", html)
        self.assertIn("gcf-icon-type-mask", html)
        # sanity: the parent flag on the passed cache does not leak in
        self.assertIsNot(parent, stage)

    def test_parent_renders_unchanged(self):
        adv = _adventure()
        parent = _cache("LCICON", adv, is_al_parent=True)

        html = str(cache_type_icon({"icon_set": "cgeo"}, CacheType.LAB, cache=parent))

        self.assertNotIn("gcf-type-icon-stage", html)
        self.assertNotIn("gcf-icon-type-mask", html)
        self.assertIn("background:#7b1fa2", html)

    def test_plain_cache_renders_unchanged(self):
        cache = Geocache.objects.create(
            gc_code="GCICON1", name="Plain", cache_type=CacheType.TRADITIONAL,
            latitude=48.0, longitude=9.0,
        )

        html = str(cache_type_icon({"icon_set": "cgeo"}, CacheType.TRADITIONAL, cache=cache))

        self.assertNotIn("gcf-type-icon-stage", html)
        self.assertNotIn("gcf-icon-type-mask", html)

    def test_no_cache_argument_renders_unchanged(self):
        # Every other call site (dashboards, filter dialog) passes no cache
        # object at all — must not error and must not go the stage path.
        html = str(cache_type_icon({"icon_set": "cgeo"}, CacheType.LAB))

        self.assertNotIn("gcf-type-icon-stage", html)

    def test_text_icon_set_returns_empty_for_a_stage(self):
        adv = _adventure()
        stage = _cache("LCICON-1", adv)

        html = str(cache_type_icon({"icon_set": "text"}, CacheType.LAB, cache=stage))

        self.assertEqual(html, "")
