"""Tests for the BRouter routing client and the map_route view."""
import json
from unittest.mock import patch

from django.test import Client, TestCase, override_settings
from django.urls import reverse

from geocaches.models import SavedRoute
from geocaches.sync import brouter_client as bc
from geocaches.sync.brouter_client import BRouterError


class BuildUrlTest(TestCase):
    def test_encodes_lonlats_and_params(self):
        url = bc.build_url([(9.0, 48.0), (9.5, 48.5)], "trekking", "geojson")
        self.assertIn("profile=trekking", url)
        self.assertIn("format=geojson", url)
        self.assertIn("alternativeidx=0", url)
        self.assertIn("9.000000", url)
        self.assertIn("48.500000", url)

    @override_settings(BROUTER_URL="http://localhost:17777/brouter/")
    def test_base_url_from_settings_strips_trailing_slash(self):
        url = bc.build_url([(0, 0), (1, 1)], "trekking", "gpx")
        self.assertTrue(url.startswith("http://localhost:17777/brouter?"))


class ThinTest(TestCase):
    def test_preserves_endpoints_and_reduces(self):
        path = [[9.0, 48.0 + i * 0.0001] for i in range(20)]  # ~11 m apart
        thinned = bc._thin(path, 200.0, 600)
        self.assertEqual(thinned[0], path[0])
        self.assertEqual(thinned[-1], path[-1])
        self.assertLess(len(thinned), len(path))

    def test_short_path_unchanged(self):
        path = [[9.0, 48.0], [9.1, 48.1]]
        self.assertEqual(bc._thin(path, 200.0, 600), path)

    def test_caps_max_points(self):
        path = [[9.0, 48.0 + i * 0.001] for i in range(1000)]
        thinned = bc._thin(path, 1.0, 50)
        self.assertEqual(len(thinned), 50)
        self.assertEqual(thinned[-1], path[-1])


class RouteSummaryTest(TestCase):
    GEOJSON = {
        "type": "FeatureCollection",
        "features": [{
            "type": "Feature",
            "properties": {
                "track-length": "2963",
                "total-time": "1070",
                "filtered ascend": "84",
            },
            "geometry": {
                "type": "LineString",
                "coordinates": [[9.0, 48.0, 400.0], [9.1, 48.1, 410.0]],
            },
        }],
    }

    def test_parses_summary_and_strips_elevation(self):
        with patch.object(bc, "fetch_route", return_value=json.dumps(self.GEOJSON).encode()):
            summary = bc.route_summary([(9.0, 48.0), (9.1, 48.1)], "trekking")
        self.assertEqual(summary["distance_m"], 2963)
        self.assertEqual(summary["duration_s"], 1070)
        self.assertEqual(summary["ascend_m"], 84)
        self.assertEqual(summary["path"], [[9.0, 48.0], [9.1, 48.1]])

    def test_missing_properties_become_none(self):
        gj = {"type": "FeatureCollection", "features": [{
            "properties": {}, "geometry": {"type": "LineString", "coordinates": [[9, 48], [9.1, 48.1]]},
        }]}
        with patch.object(bc, "fetch_route", return_value=json.dumps(gj).encode()):
            summary = bc.route_summary([(9, 48), (9.1, 48.1)])
        self.assertIsNone(summary["distance_m"])


class FetchRouteTest(TestCase):
    def test_requires_two_points(self):
        with self.assertRaises(BRouterError):
            bc.fetch_route([(9, 48)], "trekking")

    def test_non_json_geojson_body_is_an_error(self):
        class FakeResp:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return b"operation killed by thread-priority-watchdog"

        with patch("urllib.request.urlopen", return_value=FakeResp()):
            with self.assertRaises(BRouterError):
                bc.fetch_route([(9, 48), (9.1, 48.1)], "trekking", "geojson")


class MapRouteViewTest(TestCase):
    def setUp(self):
        self.client = Client()
        self.url = reverse("geocaches:map_route")

    def test_post_returns_summary(self):
        summary = {"path": [[9, 48], [9.1, 48.1]], "distance_m": 2963,
                   "duration_s": 1070, "ascend_m": 84}
        with patch("geocaches.sync.brouter_client.route_summary", return_value=summary):
            resp = self.client.post(
                self.url,
                data=json.dumps({"lonlats": [[9, 48], [9.1, 48.1]], "profile": "trekking"}),
                content_type="application/json",
            )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["distance_m"], 2963)

    def test_post_too_few_points(self):
        resp = self.client.post(
            self.url, data=json.dumps({"lonlats": [[9, 48]]}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 400)

    def test_post_routing_error_returns_502(self):
        with patch("geocaches.sync.brouter_client.route_summary",
                   side_effect=BRouterError("no route found")):
            resp = self.client.post(
                self.url,
                data=json.dumps({"lonlats": [[9, 48], [9.1, 48.1]]}),
                content_type="application/json",
            )
        self.assertEqual(resp.status_code, 502)
        self.assertIn("no route", resp.json()["error"])

    def test_get_gpx_download(self):
        with patch("geocaches.sync.brouter_client.fetch_route", return_value=b"<gpx></gpx>"):
            resp = self.client.get(
                self.url,
                {"lonlats": "9,48|9.1,48.1", "profile": "trekking", "format": "gpx"},
            )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp["Content-Type"], "application/gpx+xml")
        self.assertIn("attachment", resp["Content-Disposition"])

    def test_get_too_few_points(self):
        resp = self.client.get(self.url, {"lonlats": "9,48", "format": "gpx"})
        self.assertEqual(resp.status_code, 400)


class SavedRouteViewTest(TestCase):
    def setUp(self):
        self.client = Client()
        self.list_url = reverse("geocaches:saved_routes_list")
        self.save_url = reverse("geocaches:saved_route_save")

    def _payload(self, **over):
        data = {
            "name": "Day trip",
            "waypoints": [
                {"lat": 48.0, "lon": 9.0, "label": "Home", "kind": "location", "code": None},
                {"lat": 48.5, "lon": 9.5, "label": "GC123", "kind": "cache", "code": "GC123"},
            ],
            "profile": "trekking",
            "width_m": 500,
            "path": [[9.0, 48.0], [9.5, 48.5]],
        }
        data.update(over)
        return data

    def _save(self, **over):
        return self.client.post(
            self.save_url, data=json.dumps(self._payload(**over)),
            content_type="application/json",
        )

    def test_save_then_list(self):
        resp = self._save()
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["created"])

        listed = self.client.get(self.list_url).json()["routes"]
        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0]["name"], "Day trip")
        self.assertEqual(listed[0]["profile"], "trekking")
        self.assertEqual(listed[0]["width_m"], 500)
        self.assertEqual(len(listed[0]["waypoints"]), 2)

    def test_save_requires_name_and_waypoints(self):
        self.assertEqual(self._save(name="").status_code, 400)
        self.assertEqual(self._save(waypoints=[]).status_code, 400)

    def test_save_updates_existing_by_name(self):
        self._save()
        resp = self._save(profile="car-fast", width_m=1000)
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.json()["created"])
        self.assertEqual(SavedRoute.objects.count(), 1)
        self.assertEqual(SavedRoute.objects.get().profile, "car-fast")

    def test_delete(self):
        self._save()
        route = SavedRoute.objects.get()
        url = reverse("geocaches:saved_route_delete", args=[route.pk])
        resp = self.client.delete(url)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(SavedRoute.objects.count(), 0)

    def test_delete_missing_returns_404(self):
        url = reverse("geocaches:saved_route_delete", args=[999])
        self.assertEqual(self.client.delete(url).status_code, 404)
