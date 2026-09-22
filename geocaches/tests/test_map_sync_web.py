"""
Tests for the map's full-sync-via-website path (no partner-API token),
added 2026-08-16: geocaches.services.map_sync.run_sync_task_web() composes
GCWebClient's bbox/circle search (codes already known by the time this
runs — the frontend sends them from the preview step) with
cache_fetch.fetch_and_import_caches() (bookmark-list + GPX), same mechanism
"Fetch by List Generation" uses. No live network calls.
"""

from unittest.mock import MagicMock, patch

from django.test import TestCase
from django.urls import reverse

from geocaches.importers.gpx_common import ImportStats
from geocaches.services.map_sync import run_sync_task_web


class TestRunSyncTaskWeb(TestCase):
    @patch("geocaches.sync.gc_web.cache_fetch.fetch_and_import_caches")
    def test_single_chunk_returns_summary(self, mock_fetch):
        mock_fetch.return_value = ImportStats(created=3, updated=1, locked=2, errors=["oops"])

        result = run_sync_task_web(["GC1111", "GC2222"], tag_names=["t1"])

        self.assertEqual(result, {
            "created": 3, "updated": 1, "skipped": 2, "failed": 1, "errors": ["oops"],
        })
        mock_fetch.assert_called_once_with(["GC1111", "GC2222"], tag_names=["t1"])

    @patch("time.sleep")  # skip the real pause between chunks
    @patch("geocaches.sync.gc_web.cache_fetch.fetch_and_import_caches")
    def test_chunks_at_list_max(self, mock_fetch, _sleep):
        mock_fetch.return_value = ImportStats(created=1)
        codes = [f"GC{i:05d}" for i in range(1500)]

        run_sync_task_web(codes, tag_names=None)

        self.assertEqual(mock_fetch.call_count, 2)
        first_chunk = mock_fetch.call_args_list[0][0][0]
        second_chunk = mock_fetch.call_args_list[1][0][0]
        self.assertEqual(len(first_chunk), 1000)
        self.assertEqual(len(second_chunk), 500)

    @patch("geocaches.sync.gc_web.cache_fetch.fetch_and_import_caches")
    def test_updates_task_info_progress(self, mock_fetch):
        mock_fetch.return_value = ImportStats(created=1)
        task_info = MagicMock()
        task_info.cancel_event = None

        run_sync_task_web(["GC1111"], tag_names=None, task_info=task_info)

        self.assertEqual(task_info.total, 1)
        self.assertEqual(task_info.completed, 1)
        self.assertEqual(task_info.result["created"], 1)

    @patch("geocaches.sync.gc_web.cache_fetch.fetch_and_import_caches", side_effect=RuntimeError("boom"))
    def test_chunk_failure_recorded_not_raised(self, _mock_fetch):
        result = run_sync_task_web(["GC1111"], tag_names=None)

        self.assertEqual(result["created"], 0)
        self.assertEqual(result["failed"], 1)
        self.assertIn("boom", result["errors"][0])


class TestMapSyncRoutesToWebPath(TestCase):
    @patch("geocaches.views.map._use_web_gc_client", return_value=True)
    @patch("geocaches.views.map._run_sync_task_web")
    def test_gc_codes_route_to_web_task_when_preferred(self, mock_web_task, _use_web):
        # submit_task runs inline under the test runner (TASKS_RUN_SYNC),
        # so patch the task fn itself rather than inspecting a background result.
        mock_web_task.return_value = {"created": 0, "updated": 0, "skipped": 0, "failed": 0, "errors": []}

        response = self.client.post(
            reverse("geocaches:map_sync"),
            data='{"platforms": {"gc": ["GC1111", "GC2222"]}}',
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        args, kwargs = mock_web_task.call_args
        self.assertEqual(args, (["GC1111", "GC2222"], None))
        self.assertIn("task_info", kwargs)

    @patch("geocaches.views.map._use_web_gc_client", return_value=False)
    def test_gc_codes_use_official_client_by_default(self, _use_web):
        with patch("geocaches.views.map._run_sync_task") as mock_task, \
                patch("geocaches.sync.gc_access.get_gc_client") as MockClient:
            response = self.client.post(
                reverse("geocaches:map_sync"),
                data='{"platforms": {"gc": ["GC1111"]}}',
                content_type="application/json",
            )

        self.assertEqual(response.status_code, 200)
        mock_task.assert_called_once()
        MockClient.assert_called_once()
