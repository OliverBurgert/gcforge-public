"""
Unit tests for Phase M2 geographic area filter functionality.

Covers:
- _parse_geo_param helper
- _haversine_km helper
- apply_area_filter with various param dicts
- SavedAreaFilter CRUD views
- URL reversals for area endpoints
"""

import json
import math

from django.test import TestCase, Client
from django.urls import reverse

from geocaches.filters import _parse_geo_param, _haversine_km, apply_area_filter
from geocaches.models import Geocache, CacheType, CacheStatus, SavedAreaFilter


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_cache(gc_code, lat, lon, name=None):
    return Geocache.objects.create(
        gc_code=gc_code,
        name=name or gc_code,
        latitude=lat,
        longitude=lon,
        cache_type=CacheType.TRADITIONAL,
        status=CacheStatus.ACTIVE,
    )


# ---------------------------------------------------------------------------
# _parse_geo_param
# ---------------------------------------------------------------------------

class ParseGeoParamTests(TestCase):

    def test_valid_rect(self):
        regions = _parse_geo_param("rect:48.0,11.0,49.0,12.0")
        self.assertEqual(len(regions), 1)
        self.assertEqual(regions[0]["type"], "rect")
        self.assertEqual(regions[0]["bbox"], [48.0, 11.0, 49.0, 12.0])

    def test_valid_circle(self):
        regions = _parse_geo_param("circle:51.5,-0.1,5000")
        self.assertEqual(len(regions), 1)
        self.assertEqual(regions[0]["type"], "circle")
        self.assertEqual(regions[0]["center"], [51.5, -0.1])
        self.assertEqual(regions[0]["radius_m"], 5000.0)

    def test_mixed_rect_and_circle(self):
        regions = _parse_geo_param("rect:48.0,11.0,49.0,12.0|circle:51.5,-0.1,5000")
        self.assertEqual(len(regions), 2)
        types = {r["type"] for r in regions}
        self.assertEqual(types, {"rect", "circle"})

    def test_malformed_rect_ignored(self):
        # Only 3 coords — not enough for a rect
        regions = _parse_geo_param("rect:48.0,11.0,49.0")
        self.assertEqual(regions, [])

    def test_malformed_circle_ignored(self):
        # Only 2 coords — not enough for a circle
        regions = _parse_geo_param("circle:51.5,-0.1")
        self.assertEqual(regions, [])

    def test_non_numeric_ignored(self):
        regions = _parse_geo_param("rect:a,b,c,d")
        self.assertEqual(regions, [])

    def test_empty_string_returns_empty(self):
        regions = _parse_geo_param("")
        self.assertEqual(regions, [])

    def test_multiple_malformed_pipes(self):
        regions = _parse_geo_param("|||")
        self.assertEqual(regions, [])

    def test_valid_mixed_with_malformed(self):
        regions = _parse_geo_param("rect:48.0,11.0,49.0,12.0|rect:bad")
        self.assertEqual(len(regions), 1)
        self.assertEqual(regions[0]["type"], "rect")


# ---------------------------------------------------------------------------
# _haversine_km
# ---------------------------------------------------------------------------

class HaversineTests(TestCase):

    def test_same_point_is_zero(self):
        self.assertAlmostEqual(_haversine_km(51.5, -0.1, 51.5, -0.1), 0.0, places=5)

    def test_known_distance_london_paris(self):
        # London (51.5074, -0.1278) to Paris (48.8566, 2.3522) ≈ 343 km
        dist = _haversine_km(51.5074, -0.1278, 48.8566, 2.3522)
        self.assertAlmostEqual(dist, 343.5, delta=2.0)

    def test_symmetry(self):
        d1 = _haversine_km(48.0, 11.0, 49.0, 12.0)
        d2 = _haversine_km(49.0, 12.0, 48.0, 11.0)
        self.assertAlmostEqual(d1, d2, places=10)


# ---------------------------------------------------------------------------
# apply_area_filter
# ---------------------------------------------------------------------------

class ApplyAreaFilterTests(TestCase):

    def setUp(self):
        # Grid of caches in Germany (Munich area)
        self.inside = make_cache("GC0001", lat=48.1, lon=11.6)
        self.outside = make_cache("GC0002", lat=52.0, lon=13.4)  # Berlin
        self.edge = make_cache("GC0003", lat=48.5, lon=11.0)     # on/near boundary

    def _qs(self):
        return Geocache.objects.all()

    def test_rect_keeps_inside(self):
        params = {"geo": "rect:47.0,10.0,49.0,13.0"}
        result = apply_area_filter(self._qs(), params)
        pks = set(result.values_list("pk", flat=True))
        self.assertIn(self.inside.pk, pks)
        self.assertNotIn(self.outside.pk, pks)

    def test_rect_rejects_outside(self):
        params = {"geo": "rect:47.0,10.0,49.0,13.0"}
        result = apply_area_filter(self._qs(), params)
        pks = set(result.values_list("pk", flat=True))
        self.assertNotIn(self.outside.pk, pks)

    def test_circle_keeps_inside(self):
        # Circle centred on Munich (48.1, 11.6), radius 10 km
        params = {"geo": "circle:48.1,11.6,10000"}
        result = apply_area_filter(self._qs(), params)
        pks = set(result.values_list("pk", flat=True))
        self.assertIn(self.inside.pk, pks)

    def test_circle_rejects_outside(self):
        params = {"geo": "circle:48.1,11.6,10000"}
        result = apply_area_filter(self._qs(), params)
        pks = set(result.values_list("pk", flat=True))
        self.assertNotIn(self.outside.pk, pks)

    def test_circle_boundary_included(self):
        # Cache at exactly 0 km from centre must be included
        params = {"geo": "circle:48.1,11.6,1000"}
        cache_at_centre = make_cache("GC0010", lat=48.1, lon=11.6)
        result = apply_area_filter(self._qs(), params)
        pks = set(result.values_list("pk", flat=True))
        self.assertIn(cache_at_centre.pk, pks)

    def test_multi_region_or_union(self):
        # One rect covers Munich, another covers Berlin — both should appear
        params = {"geo": "rect:47.0,10.0,49.0,13.0|rect:51.0,12.0,53.0,14.5"}
        result = apply_area_filter(self._qs(), params)
        pks = set(result.values_list("pk", flat=True))
        self.assertIn(self.inside.pk, pks)
        self.assertIn(self.outside.pk, pks)

    def test_geo_param_absent_no_filter(self):
        params = {}
        result = apply_area_filter(self._qs(), params)
        self.assertEqual(result.count(), self._qs().count())

    def test_geo_param_empty_no_filter(self):
        params = {"geo": ""}
        result = apply_area_filter(self._qs(), params)
        self.assertEqual(result.count(), self._qs().count())

    def test_geo_param_malformed_no_filter(self):
        params = {"geo": "rect:bad,data"}
        result = apply_area_filter(self._qs(), params)
        self.assertEqual(result.count(), self._qs().count())

    def test_rect_and_circle_union(self):
        # Rect covers Munich; circle covers Berlin — both should appear
        params = {"geo": "rect:47.0,10.0,49.0,13.0|circle:52.0,13.4,5000"}
        result = apply_area_filter(self._qs(), params)
        pks = set(result.values_list("pk", flat=True))
        self.assertIn(self.inside.pk, pks)
        self.assertIn(self.outside.pk, pks)


# ---------------------------------------------------------------------------
# SavedAreaFilter CRUD views
# ---------------------------------------------------------------------------

class SavedAreaFilterViewTests(TestCase):

    def setUp(self):
        self.client = Client()
        self.list_url = reverse("geocaches:saved_areas_list")
        self.save_url = reverse("geocaches:saved_area_save")

    def _delete_url(self, pk):
        return reverse("geocaches:saved_area_delete", kwargs={"pk": pk})

    # GET /map/areas/
    def test_list_empty(self):
        resp = self.client.get(self.list_url)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("areas", data)
        self.assertEqual(data["areas"], [])

    def test_list_returns_saved_areas(self):
        SavedAreaFilter.objects.create(
            name="Munich", regions=[{"type": "rect", "bbox": [47, 10, 49, 13]}]
        )
        resp = self.client.get(self.list_url)
        data = resp.json()
        self.assertEqual(len(data["areas"]), 1)
        self.assertEqual(data["areas"][0]["name"], "Munich")

    # POST /map/areas/save/
    def test_save_creates_new(self):
        payload = {
            "name": "Berlin",
            "regions": [{"type": "circle", "center": [52.0, 13.4], "radius_m": 10000}],
        }
        resp = self.client.post(
            self.save_url,
            data=json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["name"], "Berlin")
        self.assertTrue(data["created"])
        self.assertTrue(SavedAreaFilter.objects.filter(name="Berlin").exists())

    def test_save_updates_existing(self):
        SavedAreaFilter.objects.create(
            name="Berlin", regions=[{"type": "rect", "bbox": [51, 12, 53, 14]}]
        )
        new_regions = [{"type": "circle", "center": [52.0, 13.4], "radius_m": 5000}]
        payload = {"name": "Berlin", "regions": new_regions}
        resp = self.client.post(
            self.save_url,
            data=json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertFalse(data["created"])
        obj = SavedAreaFilter.objects.get(name="Berlin")
        self.assertEqual(obj.regions, new_regions)

    def test_save_missing_name_returns_400(self):
        payload = {"regions": [{"type": "rect", "bbox": [47, 10, 49, 13]}]}
        resp = self.client.post(
            self.save_url,
            data=json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 400)

    def test_save_missing_regions_returns_400(self):
        payload = {"name": "Nowhere"}
        resp = self.client.post(
            self.save_url,
            data=json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 400)

    def test_save_empty_regions_list_returns_400(self):
        payload = {"name": "Nowhere", "regions": []}
        resp = self.client.post(
            self.save_url,
            data=json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 400)

    def test_save_invalid_json_returns_400(self):
        resp = self.client.post(
            self.save_url,
            data="not json",
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 400)

    # DELETE /map/areas/<pk>/delete/
    def test_delete_existing(self):
        obj = SavedAreaFilter.objects.create(
            name="ToDelete", regions=[{"type": "rect", "bbox": [47, 10, 49, 13]}]
        )
        resp = self.client.delete(self._delete_url(obj.pk))
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["ok"])
        self.assertFalse(SavedAreaFilter.objects.filter(pk=obj.pk).exists())

    def test_delete_nonexistent_returns_404(self):
        resp = self.client.delete(self._delete_url(99999))
        self.assertEqual(resp.status_code, 404)

    def test_get_on_save_endpoint_not_allowed(self):
        resp = self.client.get(self.save_url)
        self.assertEqual(resp.status_code, 405)

    def test_post_on_list_endpoint_not_allowed(self):
        resp = self.client.post(self.list_url)
        self.assertEqual(resp.status_code, 405)


# ---------------------------------------------------------------------------
# URL reversal
# ---------------------------------------------------------------------------

class AreaFilterURLTests(TestCase):

    def test_reverse_saved_areas_list(self):
        url = reverse("geocaches:saved_areas_list")
        self.assertEqual(url, "/map/areas/")

    def test_reverse_saved_area_save(self):
        url = reverse("geocaches:saved_area_save")
        self.assertEqual(url, "/map/areas/save/")

    def test_reverse_saved_area_delete(self):
        url = reverse("geocaches:saved_area_delete", kwargs={"pk": 42})
        self.assertEqual(url, "/map/areas/42/delete/")
