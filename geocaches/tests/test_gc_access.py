"""
Tests for geocaches.sync.gc_access — the resolver picking between the
official partner API and the public web/tRPC fallback for log submission.
No live network calls: GCClient / log_client.submit_log are patched out.
"""

from unittest.mock import patch

from django.test import TestCase

from geocaches.sync import gc_access
from preferences.models import UserPreference


class TestGcAccessMode(TestCase):
    def test_defaults_to_api_preferred(self):
        self.assertEqual(gc_access.gc_access_mode(), "api_preferred")

    def test_reads_stored_preference(self):
        UserPreference.set("gc_access_mode", "web_preferred")
        self.assertEqual(gc_access.gc_access_mode(), "web_preferred")


class TestSubmitGcLog(TestCase):
    @patch("geocaches.sync.gc_web.log_client.submit_log")
    @patch("geocaches.feature_flags.gc_api_available", return_value=True)
    def test_api_preferred_uses_official_api_when_available(self, _avail, web_submit):
        with patch("geocaches.sync.gc_access.get_gc_client") as MockClient:
            MockClient.return_value.submit_log.return_value = {"referenceCode": "GL_API"}

            backend, resp = gc_access.submit_gc_log("GC123", "Found it", "2026-08-12T12:00:00.000Z", "nice")

        self.assertEqual(backend, "gc_api")
        self.assertEqual(resp, {"referenceCode": "GL_API"})
        web_submit.assert_not_called()

    @patch("geocaches.sync.gc_web.log_client.submit_log", return_value={"referenceCode": "GL_WEB"})
    @patch("geocaches.feature_flags.gc_api_available", return_value=False)
    def test_api_preferred_falls_back_to_web_when_api_unavailable(self, _avail, web_submit):
        backend, resp = gc_access.submit_gc_log("GC123", "Found it", "2026-08-12T12:00:00.000Z", "nice")

        self.assertEqual(backend, "gc_web")
        self.assertEqual(resp, {"referenceCode": "GL_WEB"})
        web_submit.assert_called_once_with(
            "GC123", "Found it", "2026-08-12T12:00:00.000Z", "nice",
            use_favourite_point=False, session=None,
        )

    @patch("geocaches.sync.gc_web.log_client.submit_log", return_value={"referenceCode": "GL_WEB"})
    @patch("geocaches.feature_flags.gc_api_available", return_value=True)
    def test_api_preferred_falls_back_to_web_on_api_exception(self, _avail, web_submit):
        with patch("geocaches.sync.gc_access.get_gc_client") as MockClient:
            MockClient.return_value.submit_log.side_effect = RuntimeError("token expired")

            backend, resp = gc_access.submit_gc_log("GC123", "Found it", "2026-08-12T12:00:00.000Z", "nice")

        self.assertEqual(backend, "gc_web")
        self.assertEqual(resp, {"referenceCode": "GL_WEB"})

    @patch("geocaches.sync.gc_web.log_client.submit_log", return_value={"referenceCode": "GL_WEB"})
    @patch("geocaches.feature_flags.gc_api_available", return_value=True)
    def test_web_preferred_skips_api_even_when_available(self, _avail, web_submit):
        UserPreference.set("gc_access_mode", "web_preferred")

        with patch("geocaches.sync.gc_access.get_gc_client") as MockClient:
            backend, resp = gc_access.submit_gc_log("GC123", "Found it", "2026-08-12T12:00:00.000Z", "nice")

        self.assertEqual(backend, "gc_web")
        MockClient.assert_not_called()

    @patch("geocaches.sync.gc_web.log_client.submit_log", side_effect=RuntimeError("web also broken"))
    @patch("geocaches.feature_flags.gc_api_available", return_value=True)
    def test_web_preferred_does_not_retry_api_on_failure(self, _avail, web_submit):
        """web_preferred must not silently fall back to the API — testing stays honest."""
        UserPreference.set("gc_access_mode", "web_preferred")

        with patch("geocaches.sync.gc_access.get_gc_client") as MockClient:
            with self.assertRaises(RuntimeError):
                gc_access.submit_gc_log("GC123", "Found it", "2026-08-12T12:00:00.000Z", "nice")
            MockClient.assert_not_called()

    @patch("geocaches.sync.gc_web.log_client.submit_log", return_value={"referenceCode": "GL_API"})
    @patch("geocaches.feature_flags.gc_api_available", return_value=True)
    def test_use_favourite_point_is_forwarded(self, _avail, web_submit):
        with patch("geocaches.sync.gc_access.get_gc_client") as MockClient:
            MockClient.return_value.submit_log.return_value = {"referenceCode": "GL_API"}

            gc_access.submit_gc_log(
                "GC123", "Found it", "2026-08-12T12:00:00.000Z", "nice", use_favourite_point=True,
            )

        MockClient.return_value.submit_log.assert_called_once_with(
            "GC123", "Found it", "2026-08-12T12:00:00.000Z", "nice", use_favourite_point=True,
        )


class TestFetchRecentGcLogs(TestCase):
    @patch("geocaches.sync.gc_web.log_fetch.fetch_recent_gc_logs")
    @patch("geocaches.feature_flags.gc_api_available", return_value=True)
    def test_api_preferred_uses_official_api_when_available(self, _avail, web_fetch):
        with patch("geocaches.sync.gc_access.get_gc_client") as MockClient, \
                patch("geocaches.sync.log_fetch.fetch_recent_gc_logs", return_value=(2, 50)) as api_fetch:
            saved, count = gc_access.fetch_recent_gc_logs("GC123", count=50)

        self.assertEqual((saved, count), (2, 50))
        api_fetch.assert_called_once_with(MockClient.return_value, "GC123", count=50)
        web_fetch.assert_not_called()

    @patch("geocaches.sync.gc_web.log_fetch.fetch_recent_gc_logs", return_value=(3, 50))
    @patch("geocaches.feature_flags.gc_api_available", return_value=True)
    def test_falls_back_to_web_on_api_exception(self, _avail, web_fetch):
        with patch("geocaches.sync.gc_access.get_gc_client") as MockClient, \
                patch("geocaches.sync.log_fetch.fetch_recent_gc_logs", side_effect=RuntimeError("broken")):
            saved, count = gc_access.fetch_recent_gc_logs("GC123", count=50)

        self.assertEqual((saved, count), (3, 50))
        web_fetch.assert_called_once_with("GC123", count=50)

    @patch("geocaches.sync.gc_web.log_fetch.fetch_recent_gc_logs", return_value=(1, 10))
    def test_web_preferred_skips_api_even_when_available(self, web_fetch):
        UserPreference.set("gc_access_mode", "web_preferred")

        with patch("geocaches.sync.gc_access.get_gc_client") as MockClient:
            saved, count = gc_access.fetch_recent_gc_logs("GC123", count=10)

        self.assertEqual((saved, count), (1, 10))
        MockClient.assert_not_called()


class TestFetchMoreGcLogs(TestCase):
    @patch("geocaches.sync.gc_web.log_fetch.fetch_more_gc_logs")
    @patch("geocaches.feature_flags.gc_api_available", return_value=True)
    def test_forwards_skip_and_count_to_the_official_api(self, _avail, web_fetch):
        with patch("geocaches.sync.gc_access.get_gc_client") as MockClient, \
                patch("geocaches.sync.log_fetch.fetch_more_gc_logs", return_value=(1, 50)) as api_fetch:
            gc_access.fetch_more_gc_logs("GC123", skip=100, count=50)

        api_fetch.assert_called_once_with(MockClient.return_value, "GC123", skip=100, count=50)
        web_fetch.assert_not_called()


class TestFetchAllGcLogs(TestCase):
    @patch("geocaches.sync.gc_web.log_fetch.fetch_all_gc_logs", return_value=5)
    @patch("geocaches.feature_flags.gc_api_available", return_value=False)
    def test_falls_back_to_web_when_api_unavailable(self, _avail, web_fetch):
        total = gc_access.fetch_all_gc_logs("GC123")

        self.assertEqual(total, 5)
        web_fetch.assert_called_once_with("GC123")


class TestEnsureMyGcLogs(TestCase):
    @patch("geocaches.sync.gc_web.log_fetch.ensure_my_gc_logs", return_value=7)
    def test_web_preferred_uses_the_show_owner_only_web_path(self, web_ensure):
        UserPreference.set("gc_access_mode", "web_preferred")

        with patch("geocaches.sync.gc_access.get_gc_client") as MockClient:
            total = gc_access.ensure_my_gc_logs("GC123")

        self.assertEqual(total, 7)
        MockClient.assert_not_called()
        web_ensure.assert_called_once_with("GC123")

    @patch("geocaches.sync.gc_web.log_fetch.ensure_my_gc_logs", return_value=2)
    @patch("geocaches.feature_flags.gc_api_available", return_value=True)
    def test_falls_back_to_web_on_api_exception(self, _avail, web_ensure):
        with patch("geocaches.sync.gc_access.get_gc_client") as MockClient, \
                patch("geocaches.sync.log_fetch.ensure_my_gc_logs", side_effect=RuntimeError("broken")):
            total = gc_access.ensure_my_gc_logs("GC123")

        self.assertEqual(total, 2)
        web_ensure.assert_called_once_with("GC123")
