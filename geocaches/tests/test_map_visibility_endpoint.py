"""Tests for the map_markers endpoint visibility filter.

Confirms the asymmetric design in docs/map-visibility.md §6 / §11:
hidden caches are filtered from /map/markers/ but NOT from the list view.
"""

import json
from datetime import date

from django.test import TestCase
from django.urls import reverse

from geocaches.models import CacheSize, CacheStatus, CacheType, Geocache
from geocaches.services.map_visibility import SESSION_KEY


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


def _marker_codes(response) -> set[str]:
    payload = json.loads(response.content)
    return {m["c"] for m in payload["markers"]}


class MapMarkersVisibilityFilterTest(TestCase):
    def setUp(self):
        # Three visible caches inside a bbox we'll use later
        self.visible_a = Geocache.objects.create(gc_code="GCVIS1", **_fields(latitude=48.50, longitude=9.10))
        self.visible_b = Geocache.objects.create(gc_code="GCVIS2", **_fields(latitude=48.51, longitude=9.11))
        # Persistently hidden
        self.always_hidden = Geocache.objects.create(
            gc_code="GCHIDE1", **_fields(latitude=48.52, longitude=9.12),
        )
        self.always_hidden.map_hidden_always = True
        self.always_hidden.save()
        # Session-hidden via GC code
        self.session_hidden = Geocache.objects.create(
            gc_code="GCHIDE2", **_fields(latitude=48.53, longitude=9.13),
        )

    def _set_session_hidden(self, codes):
        session = self.client.session
        session[SESSION_KEY] = list(codes)
        session.save()

    def test_always_hidden_absent_from_markers(self):
        response = self.client.get(reverse("geocaches:map_markers"))
        self.assertEqual(response.status_code, 200)
        codes = _marker_codes(response)
        self.assertIn("GCVIS1", codes)
        self.assertIn("GCVIS2", codes)
        self.assertNotIn("GCHIDE1", codes)

    def test_session_hidden_absent_from_markers(self):
        self._set_session_hidden(["GCHIDE2"])
        response = self.client.get(reverse("geocaches:map_markers"))
        self.assertEqual(response.status_code, 200)
        codes = _marker_codes(response)
        self.assertIn("GCVIS1", codes)
        self.assertNotIn("GCHIDE2", codes)

    def test_session_hidden_works_for_oc_code(self):
        oc_cache = Geocache.objects.create(
            oc_code="OCXYZ", **_fields(latitude=48.6, longitude=9.2, primary_source="oc"),
        )
        self._set_session_hidden(["OCXYZ"])
        response = self.client.get(reverse("geocaches:map_markers"))
        codes = _marker_codes(response)
        self.assertNotIn("OCXYZ", codes)
        self.assertIn("GCVIS1", codes)
        # Cleanup not needed (TestCase rollback), but document intent
        oc_cache.refresh_from_db()

    def test_session_hidden_works_for_al_code(self):
        Geocache.objects.create(
            al_code="LCABC", **_fields(latitude=48.7, longitude=9.3, primary_source="al", cache_type=CacheType.LAB),
        )
        self._set_session_hidden(["LCABC"])
        response = self.client.get(reverse("geocaches:map_markers"))
        codes = _marker_codes(response)
        self.assertNotIn("LCABC", codes)

    def test_hidden_cache_inside_bbox_still_excluded_from_map(self):
        # bbox covers all four caches
        bbox = "48.0,9.0,49.0,10.0"
        response = self.client.get(reverse("geocaches:map_markers") + f"?bbox={bbox}")
        codes = _marker_codes(response)
        self.assertIn("GCVIS1", codes)
        self.assertIn("GCVIS2", codes)
        self.assertNotIn("GCHIDE1", codes)  # always-hidden, in bbox, still gone


class ListEndpointAsymmetryRegressionTest(TestCase):
    """The list view MUST show map-hidden caches (filters never see visibility).

    This is the critical regression guard — without it, a future contributor
    might 'fix' the asymmetry and break the user-visible contract.
    """

    def setUp(self):
        # Cache inside a bbox, marked always-hidden
        self.cache = Geocache.objects.create(gc_code="GCBBOX1", **_fields(latitude=48.5, longitude=9.1))
        self.cache.map_hidden_always = True
        self.cache.save()

    def test_list_endpoint_still_returns_persistently_hidden_cache(self):
        response = self.client.get(reverse("geocaches:list"))
        self.assertEqual(response.status_code, 200)
        # The list should include the hidden cache — only the map suppresses it.
        self.assertContains(response, "GCBBOX1")

    def test_list_endpoint_returns_session_hidden_cache(self):
        session = self.client.session
        session[SESSION_KEY] = ["GCBBOX1"]
        session.save()
        response = self.client.get(reverse("geocaches:list"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "GCBBOX1")

    def test_map_excludes_but_list_includes_same_cache(self):
        # The asymmetry: same cache, two endpoints, different visibility.
        list_response = self.client.get(reverse("geocaches:list"))
        map_response = self.client.get(reverse("geocaches:map_markers"))
        self.assertContains(list_response, "GCBBOX1")
        self.assertNotIn("GCBBOX1", _marker_codes(map_response))


# ---------------------------------------------------------------------------
# Cache-detail context + set_map_visibility view (step 4)
# ---------------------------------------------------------------------------

class CacheDetailMapVisibilityContextTest(TestCase):
    def setUp(self):
        self.cache = Geocache.objects.create(gc_code="GCDET1", **_fields())

    def test_context_visible_when_unset(self):
        response = self.client.get(reverse("geocaches:detail", args=["GCDET1"]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["map_visibility_state"], "visible")

    def test_context_session_when_in_session_list(self):
        session = self.client.session
        session[SESSION_KEY] = ["GCDET1"]
        session.save()
        response = self.client.get(reverse("geocaches:detail", args=["GCDET1"]))
        self.assertEqual(response.context["map_visibility_state"], "session")

    def test_context_always_when_db_flag_set(self):
        self.cache.map_hidden_always = True
        self.cache.save()
        response = self.client.get(reverse("geocaches:detail", args=["GCDET1"]))
        self.assertEqual(response.context["map_visibility_state"], "always")


class SetMapVisibilityViewTest(TestCase):
    def setUp(self):
        self.cache = Geocache.objects.create(gc_code="GCPOST1", **_fields())

    def test_post_visible_to_session_updates_db_and_session(self):
        response = self.client.post(
            reverse("geocaches:cache_map_visibility", args=["GCPOST1"]),
            {"state": "session"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("GCPOST1", self.client.session.get(SESSION_KEY, []))

    def test_post_always_sets_db_flag(self):
        self.client.post(
            reverse("geocaches:cache_map_visibility", args=["GCPOST1"]),
            {"state": "always"},
        )
        self.cache.refresh_from_db()
        self.assertTrue(self.cache.map_hidden_always)

    def test_post_visible_clears_both_stores(self):
        # set up always state, then switch to visible
        self.cache.map_hidden_always = True
        self.cache.save()
        session = self.client.session
        session[SESSION_KEY] = ["GCPOST1"]
        session.save()

        self.client.post(
            reverse("geocaches:cache_map_visibility", args=["GCPOST1"]),
            {"state": "visible"},
        )
        self.cache.refresh_from_db()
        self.assertFalse(self.cache.map_hidden_always)
        self.assertNotIn("GCPOST1", self.client.session.get(SESSION_KEY, []))

    def test_invalid_state_returns_400(self):
        response = self.client.post(
            reverse("geocaches:cache_map_visibility", args=["GCPOST1"]),
            {"state": "bogus"},
        )
        self.assertEqual(response.status_code, 400)

    def test_get_returns_405(self):
        response = self.client.get(
            reverse("geocaches:cache_map_visibility", args=["GCPOST1"]),
        )
        self.assertEqual(response.status_code, 405)

    def test_htmx_post_returns_partial(self):
        response = self.client.post(
            reverse("geocaches:cache_map_visibility", args=["GCPOST1"]),
            {"state": "session"},
            HTTP_HX_REQUEST="true",
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "map-visibility-control")

    def test_non_htmx_post_returns_json(self):
        response = self.client.post(
            reverse("geocaches:cache_map_visibility", args=["GCPOST1"]),
            {"state": "session"},
        )
        self.assertEqual(response.status_code, 200)
        payload = json.loads(response.content)
        self.assertEqual(payload["state"], "session")


# ---------------------------------------------------------------------------
# List view per-row map_visibility_state annotation (step 5)
# ---------------------------------------------------------------------------

class ListRowMapVisibilityStateTest(TestCase):
    def setUp(self):
        self.visible = Geocache.objects.create(gc_code="GCROWV", **_fields())
        self.always = Geocache.objects.create(gc_code="GCROWA", **_fields())
        self.always.map_hidden_always = True
        self.always.save()
        self.session_cache = Geocache.objects.create(gc_code="GCROWS", **_fields())

    def _states(self, page_obj):
        return {c.display_code: c.map_visibility_state for c in page_obj.object_list}

    def test_visible_row_has_visible_state(self):
        response = self.client.get(reverse("geocaches:list"))
        states = self._states(response.context["page_obj"])
        self.assertEqual(states["GCROWV"], "visible")

    def test_always_hidden_row_has_always_state(self):
        response = self.client.get(reverse("geocaches:list"))
        states = self._states(response.context["page_obj"])
        self.assertEqual(states["GCROWA"], "always")

    def test_session_hidden_row_has_session_state(self):
        session = self.client.session
        session[SESSION_KEY] = ["GCROWS"]
        session.save()
        response = self.client.get(reverse("geocaches:list"))
        states = self._states(response.context["page_obj"])
        self.assertEqual(states["GCROWS"], "session")

    def test_row_indicator_rendered_for_hidden_only(self):
        response = self.client.get(reverse("geocaches:list"))
        # The "always" cache renders a danger-coloured badge.
        self.assertContains(response, 'title="Hidden on map (always)"')
        # No session-hidden cache by default → the session-state badge must not appear.
        self.assertNotContains(response, 'title="Hidden on map (this session)"')


# ---------------------------------------------------------------------------
# bulk_map_visibility (step 6)
# ---------------------------------------------------------------------------

class BulkMapVisibilityViewTest(TestCase):
    def setUp(self):
        # Three caches with name "in_subset" via state="BW"
        self.in1 = Geocache.objects.create(gc_code="GCBK1", **_fields(state="BW"))
        self.in2 = Geocache.objects.create(gc_code="GCBK2", **_fields(state="BW"))
        self.in3 = Geocache.objects.create(gc_code="GCBK3", **_fields(state="BW"))
        # An outside cache (different state to be excluded by the filter)
        self.outside = Geocache.objects.create(
            gc_code="GCBKOUT", **_fields(state="NRW"),
        )

    def _post(self, state, **extra_params):
        # Filter by state=BW.  After the v2 cutover, the filter is encoded
        # in ?fx= (the toolbar's State dropdown now emits fx via JS), so the
        # bulk view sees the filter through apply_filter_expr.
        from geocaches.filter_expr import Condition, Group, OP_AND, to_url_param
        fx = to_url_param(Group(OP_AND, [Condition("state", "in", ["BW"])]))
        url = reverse("geocaches:bulk_map_visibility") + "?fx=" + fx
        return self.client.post(url, {"state": state, **extra_params})

    def test_always_applies_to_filtered_subset_only(self):
        response = self._post("always")
        self.assertEqual(response.status_code, 302)
        for c in (self.in1, self.in2, self.in3):
            c.refresh_from_db()
            self.assertTrue(c.map_hidden_always)
        self.outside.refresh_from_db()
        self.assertFalse(self.outside.map_hidden_always)

    def test_session_adds_codes_to_session_list(self):
        self._post("session")
        codes = self.client.session.get(SESSION_KEY, [])
        self.assertIn("GCBK1", codes)
        self.assertIn("GCBK2", codes)
        self.assertNotIn("GCBKOUT", codes)

    def test_visible_clears_both_stores_for_subset(self):
        # Pre-hide everything
        for c in (self.in1, self.in2, self.in3):
            c.map_hidden_always = True
            c.save()
        session = self.client.session
        session[SESSION_KEY] = ["GCBK1"]
        session.save()

        self._post("visible")
        for c in (self.in1, self.in2, self.in3):
            c.refresh_from_db()
            self.assertFalse(c.map_hidden_always)
        self.assertNotIn("GCBK1", self.client.session.get(SESSION_KEY, []))

    def test_reset_session_clears_entire_session_list(self):
        session = self.client.session
        session[SESSION_KEY] = ["GCBK1", "GCBKOUT", "ANYTHING"]
        session.save()
        response = self.client.post(
            reverse("geocaches:bulk_map_visibility"),
            {"state": "reset_session"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.client.session.get(SESSION_KEY, []), [])

    def test_parent_in_filter_cascades_to_stages(self):
        from geocaches.models import ALStageDetail, Adventure, CacheType
        adv = Adventure.objects.create(code="LCBULK", title="Bulk", latitude=48.5, longitude=9.1)
        parent = Geocache.objects.create(
            al_code="LCBULK", **_fields(
                state="HE", cache_type=CacheType.LAB, primary_source="al",
            ),
        )
        parent.adventure = adv
        parent.save()
        stage = Geocache.objects.create(
            al_code="LCBULK-1", **_fields(
                state="XX", cache_type=CacheType.LAB, primary_source="al",
            ),
        )
        stage.adventure = adv
        stage.save()
        ALStageDetail.objects.create(geocache=stage, stage_number=1)

        # Filter to ONLY the parent (by state=HE)
        url = reverse("geocaches:bulk_map_visibility") + "?state=HE"
        response = self.client.post(url, {"state": "always"})
        self.assertEqual(response.status_code, 302)

        parent.refresh_from_db()
        stage.refresh_from_db()
        self.assertTrue(parent.map_hidden_always)
        self.assertTrue(stage.map_hidden_always)

    def test_invalid_state_returns_400(self):
        response = self.client.post(
            reverse("geocaches:bulk_map_visibility"),
            {"state": "bogus"},
        )
        self.assertEqual(response.status_code, 400)

    def test_get_returns_400(self):
        response = self.client.get(reverse("geocaches:bulk_map_visibility"))
        self.assertEqual(response.status_code, 400)
