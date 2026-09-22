"""
Tests for geocaches.views.map._make_preview_client()'s backend routing —
extended 2026-08-16 to route plain bbox/circle preview search through
GCWebClient (not just "criteria" search) per gc_access_mode/API
availability, now that GCWebClient.search_lite_by_bbox()/
search_lite_by_center() are confirmed to work as general area search.
"""

from unittest.mock import patch

from django.test import TestCase

from geocaches.views.map import _make_preview_client


class TestMakePreviewClient(TestCase):
    def test_criteria_always_uses_web_client(self):
        with patch("geocaches.sync.gc_access.get_gc_client") as MockClient:
            client = _make_preview_client("gc", "criteria")

        self.assertEqual(client.__class__.__name__, "GCWebClient")
        MockClient.assert_not_called()

    @patch("geocaches.feature_flags.gc_api_available", return_value=True)
    def test_rect_uses_official_api_by_default(self, _avail):
        from preferences.models import UserPreference
        UserPreference.set("gc_access_mode", "api_preferred")

        with patch("geocaches.sync.gc_access.get_gc_client") as MockClient:
            client = _make_preview_client("gc", "rect")

        self.assertIs(client, MockClient.return_value)

    @patch("geocaches.feature_flags.gc_api_available", return_value=True)
    def test_circle_uses_web_client_when_web_preferred(self, _avail):
        from preferences.models import UserPreference
        UserPreference.set("gc_access_mode", "web_preferred")

        with patch("geocaches.sync.gc_access.get_gc_client") as MockClient:
            client = _make_preview_client("gc", "circle")

        self.assertEqual(client.__class__.__name__, "GCWebClient")
        MockClient.assert_not_called()

    @patch("geocaches.feature_flags.gc_api_available", return_value=False)
    def test_rect_uses_web_client_when_api_unavailable(self, _avail):
        from preferences.models import UserPreference
        UserPreference.set("gc_access_mode", "api_preferred")

        with patch("geocaches.sync.gc_access.get_gc_client") as MockClient:
            client = _make_preview_client("gc", "rect")

        self.assertEqual(client.__class__.__name__, "GCWebClient")
        MockClient.assert_not_called()

    def test_corridor_and_polygon_follow_the_same_rule_as_rect(self):
        from preferences.models import UserPreference
        UserPreference.set("gc_access_mode", "web_preferred")

        for region_type in ("corridor", "polygon"):
            with patch("geocaches.sync.gc_access.get_gc_client") as MockClient:
                client = _make_preview_client("gc", region_type)
            self.assertEqual(client.__class__.__name__, "GCWebClient")
            MockClient.assert_not_called()

    def test_non_gc_platform_is_unaffected(self):
        with patch("geocaches.sync.oc_client.OCClient") as MockOc:
            client = _make_preview_client("oc_de", "rect")

        self.assertIs(client, MockOc.return_value)
