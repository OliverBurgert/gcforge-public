"""
Tests for geocaches.sync.gc_web.search_client.GCWebClient's plain
bbox/circle search methods (search_lite_by_bbox/search_lite_by_center/
search_by_bbox/search_by_center) — added 2026-08-16 on top of the existing
criteria-search machinery.

Confirmed live: search_criteria_lite({}, bbox=...) works as a general area
search since the `box` query param is independent of every filter param.
No live network calls in this test suite.
"""

from unittest.mock import patch

from django.test import SimpleTestCase

from geocaches.sync.gc_web.search_client import GCWebClient


class TestSearchLiteByBbox(SimpleTestCase):
    def test_delegates_to_search_criteria_lite_with_empty_criteria(self):
        client = GCWebClient()
        with patch.object(client, "search_criteria_lite", return_value=["sentinel"]) as mock_search:
            result = client.search_lite_by_bbox(48.0, 9.0, 48.1, 9.1, max_results=250)

        self.assertEqual(result, ["sentinel"])
        mock_search.assert_called_once_with(
            {}, bbox=(48.0, 9.0, 48.1, 9.1), max_results=250,
            cancel_event=None, limiter=None,
        )

    def test_builds_a_box_only_param_set(self):
        # No criteria filter keys (ct/cs/d/t/hb/...) should appear -- only
        # `box`, `sort`, and the fixed `app` marker.
        client = GCWebClient()
        params = client._build_params({}, (48.0, 9.0, 48.1, 9.1))

        self.assertEqual(set(params.keys()), {"app", "box", "sort"})
        self.assertEqual(params["box"], "48.1,9.0,48.0,9.1")  # latMax,lonMin,latMin,lonMax
        self.assertEqual(params["sort"], "distance")


class TestSearchLiteByCenter(SimpleTestCase):
    def test_converts_radius_to_a_bbox_and_delegates(self):
        import math

        client = GCWebClient()
        with patch.object(client, "search_lite_by_bbox", return_value=["sentinel"]) as mock_bbox:
            result = client.search_lite_by_center(48.0, 9.0, 5000, max_results=100)

        self.assertEqual(result, ["sentinel"])
        mock_bbox.assert_called_once()
        args, kwargs = mock_bbox.call_args
        south, west, north, east = args
        d_lat = 5.0 / 111.32
        d_lon = 5.0 / (111.32 * math.cos(math.radians(48.0)))
        self.assertAlmostEqual(south, 48.0 - d_lat, places=6)
        self.assertAlmostEqual(north, 48.0 + d_lat, places=6)
        self.assertAlmostEqual(west, 9.0 - d_lon, places=6)
        self.assertAlmostEqual(east, 9.0 + d_lon, places=6)
        self.assertEqual(kwargs["max_results"], 100)


class TestSearchByBboxAndCenter(SimpleTestCase):
    def test_search_by_bbox_returns_codes_only(self):
        client = GCWebClient()
        rows = [
            {"gc_code": "GC111", "fields": {"name": "A"}, "found": False},
            {"gc_code": "GC222", "fields": {"name": "B"}, "found": True},
        ]
        with patch.object(client, "search_lite_by_bbox", return_value=rows):
            codes = client.search_by_bbox(48.0, 9.0, 48.1, 9.1, max_results=10)

        self.assertEqual(codes, ["GC111", "GC222"])

    def test_search_by_center_returns_codes_only(self):
        client = GCWebClient()
        rows = [{"gc_code": "GC333", "fields": {}, "found": False}]
        with patch.object(client, "search_lite_by_center", return_value=rows):
            codes = client.search_by_center(48.0, 9.0, 1000, max_results=10)

        self.assertEqual(codes, ["GC333"])
