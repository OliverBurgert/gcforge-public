"""Tests for geocaches.services.map_visibility — tri-state map-hide service."""

from datetime import date

from django.test import TestCase

from geocaches.models import (
    ALStageDetail,
    Adventure,
    CacheSize,
    CacheStatus,
    CacheType,
    Geocache,
)
from geocaches.services.map_visibility import (
    SESSION_KEY,
    MapVisibility,
    bulk_set,
    get_state,
    hidden_codes_in_session,
    reset_all_session,
    set_state,
)


def _fields(**overrides):
    defaults = {
        "name": "Test Cache",
        "owner": "Owner",
        "cache_type": CacheType.TRADITIONAL,
        "size": CacheSize.SMALL,
        "status": CacheStatus.ACTIVE,
        "latitude": 48.5,
        "longitude": 9.1,
        "difficulty": 2.0,
        "terrain": 1.5,
        "short_description": "",
        "long_description": "",
        "hint": "",
        "hidden_date": date(2020, 1, 1),
        "country": "Germany",
        "state": "BW",
        "fav_points": 0,
        "has_trackable": False,
        "primary_source": "gc",
    }
    defaults.update(overrides)
    return defaults


def _make_session(initial=None):
    """Return a dict that mimics request.session enough for the service.

    The service only needs ``.get()``, ``__setitem__``, and
    ``.modified = True`` — a plain dict subclass works.
    """
    session = _DictSession()
    if initial is not None:
        session[SESSION_KEY] = list(initial)
        session.modified = False
    return session


class _DictSession(dict):
    """Bare-minimum stand-in for Django's SessionBase used by the service."""

    modified = False

    def get(self, key, default=None):
        return super().get(key, default)


# ---------------------------------------------------------------------------
# get_state — all four storage combinations
# ---------------------------------------------------------------------------

class GetStateTest(TestCase):
    def setUp(self):
        self.cache = Geocache.objects.create(gc_code="GC0001", **_fields())

    def test_visible_when_neither_store_set(self):
        session = _make_session()
        self.assertEqual(get_state(self.cache, session), MapVisibility.VISIBLE)

    def test_session_when_only_in_session_list(self):
        session = _make_session(["GC0001"])
        self.assertEqual(get_state(self.cache, session), MapVisibility.SESSION)

    def test_always_when_only_db_flag_set(self):
        self.cache.map_hidden_always = True
        self.cache.save()
        session = _make_session()
        self.assertEqual(get_state(self.cache, session), MapVisibility.ALWAYS)

    def test_always_wins_when_both_stores_set(self):
        # Defensive: set_state's invariant prevents this, but get_state
        # should prefer ALWAYS if both happen to be set.
        self.cache.map_hidden_always = True
        self.cache.save()
        session = _make_session(["GC0001"])
        self.assertEqual(get_state(self.cache, session), MapVisibility.ALWAYS)


# ---------------------------------------------------------------------------
# set_state transitions — mutual exclusivity invariant
# ---------------------------------------------------------------------------

class SetStateTransitionsTest(TestCase):
    def setUp(self):
        self.cache = Geocache.objects.create(gc_code="GC0002", **_fields())
        self.session = _make_session()

    def _refresh(self):
        self.cache.refresh_from_db()

    def test_visible_to_session_sets_only_session(self):
        set_state(self.cache, MapVisibility.SESSION, self.session)
        self._refresh()
        self.assertFalse(self.cache.map_hidden_always)
        self.assertIn("GC0002", self.session.get(SESSION_KEY, []))

    def test_session_to_always_clears_session_sets_db(self):
        set_state(self.cache, MapVisibility.SESSION, self.session)
        set_state(self.cache, MapVisibility.ALWAYS, self.session)
        self._refresh()
        self.assertTrue(self.cache.map_hidden_always)
        self.assertNotIn("GC0002", self.session.get(SESSION_KEY, []))

    def test_always_to_visible_clears_both_stores(self):
        set_state(self.cache, MapVisibility.ALWAYS, self.session)
        set_state(self.cache, MapVisibility.VISIBLE, self.session)
        self._refresh()
        self.assertFalse(self.cache.map_hidden_always)
        self.assertNotIn("GC0002", self.session.get(SESSION_KEY, []))

    def test_full_cycle_visible_session_always_visible(self):
        # visible (default)
        self.assertEqual(get_state(self.cache, self.session), MapVisibility.VISIBLE)
        # -> session
        set_state(self.cache, MapVisibility.SESSION, self.session)
        self._refresh()
        self.assertEqual(get_state(self.cache, self.session), MapVisibility.SESSION)
        self.assertFalse(self.cache.map_hidden_always)
        # -> always (session list cleared)
        set_state(self.cache, MapVisibility.ALWAYS, self.session)
        self._refresh()
        self.assertEqual(get_state(self.cache, self.session), MapVisibility.ALWAYS)
        self.assertNotIn("GC0002", self.session.get(SESSION_KEY, []))
        # -> visible (both cleared)
        set_state(self.cache, MapVisibility.VISIBLE, self.session)
        self._refresh()
        self.assertEqual(get_state(self.cache, self.session), MapVisibility.VISIBLE)
        self.assertFalse(self.cache.map_hidden_always)
        self.assertNotIn("GC0002", self.session.get(SESSION_KEY, []))

    def test_session_marked_modified_after_write(self):
        set_state(self.cache, MapVisibility.SESSION, self.session)
        self.assertTrue(self.session.modified)

    def test_invalid_state_raises(self):
        with self.assertRaises(ValueError):
            set_state(self.cache, "bogus", self.session)


# ---------------------------------------------------------------------------
# AL parent → stage cascade (set_state)
# ---------------------------------------------------------------------------

class CascadeTest(TestCase):
    def setUp(self):
        self.adv = Adventure.objects.create(
            code="LCTEST", title="Test Adventure",
            latitude=52.52, longitude=13.405,
        )
        self.parent = Geocache.objects.create(
            al_code="LCTEST", name="Test Adventure",
            cache_type=CacheType.LAB,
            latitude=52.52, longitude=13.405,
            adventure=self.adv,
            primary_source="al",
        )
        self.stages = []
        for i in range(1, 4):
            stage = Geocache.objects.create(
                al_code=f"LCTEST-{i}", name=f"Stage {i}",
                cache_type=CacheType.LAB,
                latitude=52.52 + i * 0.001, longitude=13.405 + i * 0.001,
                adventure=self.adv,
                primary_source="al",
            )
            ALStageDetail.objects.create(geocache=stage, stage_number=i)
            self.stages.append(stage)
        self.session = _make_session()

    def test_parent_always_cascades_to_all_stages(self):
        set_state(self.parent, MapVisibility.ALWAYS, self.session)
        self.parent.refresh_from_db()
        self.assertTrue(self.parent.map_hidden_always)
        for stage in self.stages:
            stage.refresh_from_db()
            self.assertTrue(stage.map_hidden_always)

    def test_parent_session_cascades_codes_to_session_list(self):
        set_state(self.parent, MapVisibility.SESSION, self.session)
        codes = hidden_codes_in_session(self.session)
        self.assertIn("LCTEST", codes)
        for stage in self.stages:
            self.assertIn(stage.display_code, codes)

    def test_parent_visible_unhides_all_stages(self):
        # Hide parent (cascades), then unhide
        set_state(self.parent, MapVisibility.ALWAYS, self.session)
        set_state(self.parent, MapVisibility.VISIBLE, self.session)
        self.parent.refresh_from_db()
        self.assertFalse(self.parent.map_hidden_always)
        for stage in self.stages:
            stage.refresh_from_db()
            self.assertFalse(stage.map_hidden_always)

    def test_stage_does_not_cascade_up_to_parent(self):
        stage = self.stages[0]
        set_state(stage, MapVisibility.ALWAYS, self.session)
        stage.refresh_from_db()
        self.assertTrue(stage.map_hidden_always)
        # Parent and siblings untouched
        self.parent.refresh_from_db()
        self.assertFalse(self.parent.map_hidden_always)
        for other in self.stages[1:]:
            other.refresh_from_db()
            self.assertFalse(other.map_hidden_always)


# ---------------------------------------------------------------------------
# bulk_set — counts, scope, cascade
# ---------------------------------------------------------------------------

class BulkSetTest(TestCase):
    def setUp(self):
        self.in_qs = [
            Geocache.objects.create(gc_code="GCIN1", **_fields()),
            Geocache.objects.create(gc_code="GCIN2", **_fields()),
            Geocache.objects.create(gc_code="GCIN3", **_fields()),
        ]
        # An outside cache that should never be touched
        self.outside = Geocache.objects.create(gc_code="GCOUT1", **_fields())
        self.session = _make_session()

    def _qs(self):
        return Geocache.objects.filter(gc_code__in=["GCIN1", "GCIN2", "GCIN3"])

    def test_returns_changed_and_unchanged_counts(self):
        # Pre-set GCIN1 to ALWAYS so it's "unchanged" when we apply ALWAYS
        self.in_qs[0].map_hidden_always = True
        self.in_qs[0].save()
        result = bulk_set(self._qs(), MapVisibility.ALWAYS, self.session)
        self.assertEqual(result["changed"] + result["unchanged"], 3)
        self.assertEqual(result["unchanged"], 1)
        self.assertEqual(result["changed"], 2)

    def test_does_not_touch_caches_outside_queryset(self):
        bulk_set(self._qs(), MapVisibility.ALWAYS, self.session)
        self.outside.refresh_from_db()
        self.assertFalse(self.outside.map_hidden_always)
        self.assertNotIn("GCOUT1", self.session.get(SESSION_KEY, []))

    def test_session_state_adds_all_codes(self):
        bulk_set(self._qs(), MapVisibility.SESSION, self.session)
        codes = hidden_codes_in_session(self.session)
        for c in self.in_qs:
            self.assertIn(c.display_code, codes)
        self.assertNotIn("GCOUT1", codes)

    def test_empty_qs_returns_zero_counts(self):
        result = bulk_set(Geocache.objects.none(), MapVisibility.ALWAYS, self.session)
        self.assertEqual(result, {"changed": 0, "unchanged": 0})

    def test_invalid_state_raises(self):
        with self.assertRaises(ValueError):
            bulk_set(self._qs(), "bogus", self.session)

    def test_parent_in_qs_cascades_to_stages(self):
        adv = Adventure.objects.create(
            code="LCBULK", title="Bulk Adventure",
            latitude=52.0, longitude=13.0,
        )
        parent = Geocache.objects.create(
            al_code="LCBULK", name="Bulk parent",
            cache_type=CacheType.LAB,
            latitude=52.0, longitude=13.0,
            adventure=adv,
            primary_source="al",
        )
        stages = []
        for i in range(1, 3):
            stage = Geocache.objects.create(
                al_code=f"LCBULK-{i}", name=f"Stage {i}",
                cache_type=CacheType.LAB,
                latitude=52.0 + i * 0.001, longitude=13.0,
                adventure=adv,
                primary_source="al",
            )
            ALStageDetail.objects.create(geocache=stage, stage_number=i)
            stages.append(stage)

        qs = Geocache.objects.filter(pk=parent.pk)
        bulk_set(qs, MapVisibility.ALWAYS, self.session)
        parent.refresh_from_db()
        self.assertTrue(parent.map_hidden_always)
        for stage in stages:
            stage.refresh_from_db()
            self.assertTrue(stage.map_hidden_always)


# ---------------------------------------------------------------------------
# reset_all_session
# ---------------------------------------------------------------------------

class ResetAllSessionTest(TestCase):
    def test_empties_list_and_returns_count(self):
        session = _make_session(["GC1", "GC2", "GC3"])
        n = reset_all_session(session)
        self.assertEqual(n, 3)
        self.assertEqual(session.get(SESSION_KEY, []), [])
        self.assertTrue(session.modified)

    def test_returns_zero_for_empty_session(self):
        session = _make_session()
        n = reset_all_session(session)
        self.assertEqual(n, 0)

    def test_returns_zero_for_missing_key(self):
        session = _DictSession()
        n = reset_all_session(session)
        self.assertEqual(n, 0)
