"""
Tests for the "Fetch by List Generation" bulk import
(geocaches.views.import_by_list_generation) — the web bookmark-list + GPX
mechanism, mocked here. No live calls, no gcprivate dependency at all (the
whole point of this feature is not needing the partner API).
"""

from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse

from geocaches.importers.gpx_common import ImportStats
from geocaches.services.ignore_list import add_internal


class ImportByListGenerationViewTests(TestCase):
    def test_get_renders_form(self):
        response = self.client.get(reverse("geocaches:import_by_list_generation"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "codes_file")

    def test_post_with_no_codes_shows_error(self):
        response = self.client.post(reverse("geocaches:import_by_list_generation"), {"codes": ""})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "No fetchable GC codes")

    def test_post_with_only_oc_codes_shows_error(self):
        # GC-only tool -- an OC-only list has nothing left to fetch.
        response = self.client.post(
            reverse("geocaches:import_by_list_generation"), {"codes": "OCDEA1B"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "No fetchable GC codes")

    def test_post_valid_codes_starts_task_and_calls_fetch_and_import(self):
        stats = ImportStats(created=2, updated=0, locked=0, errors=[])
        with patch(
            "geocaches.sync.gc_web.cache_fetch.fetch_and_import_caches", return_value=stats,
        ) as mock_fetch:
            response = self.client.post(
                reverse("geocaches:import_by_list_generation"),
                {"codes": "GC1111, GC2222", "tags": ""},
            )
        self.assertEqual(response.status_code, 200)
        mock_fetch.assert_called_once()
        called_codes = mock_fetch.call_args[0][0]
        self.assertEqual(called_codes, ["GC1111", "GC2222"])

        # The initial POST response only shows the "Starting..." polling
        # state (no `task` in that render context, same as the existing
        # "Fetch by Code List" page) -- tasks run inline under the test
        # runner, so by now the result is already there; fetch it the same
        # way the browser's HTMX poll would, via the status endpoint.
        task_id = response.context["task_id"]
        status_response = self.client.get(
            reverse("geocaches:import_by_list_generation_status", args=[task_id]),
        )
        self.assertContains(status_response, "2 created")

    def test_post_skips_oc_codes_and_only_fetches_gc(self):
        stats = ImportStats(created=1)
        with patch(
            "geocaches.sync.gc_web.cache_fetch.fetch_and_import_caches", return_value=stats,
        ) as mock_fetch:
            response = self.client.post(
                reverse("geocaches:import_by_list_generation"),
                {"codes": "GC1111, OCDEA1B", "tags": ""},
            )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "OC")  # skipped-OC notice
        called_codes = mock_fetch.call_args[0][0]
        self.assertEqual(called_codes, ["GC1111"])

    def test_post_skips_internally_ignored_codes(self):
        add_internal("GC9999")
        stats = ImportStats(created=1)
        with patch(
            "geocaches.sync.gc_web.cache_fetch.fetch_and_import_caches", return_value=stats,
        ) as mock_fetch:
            response = self.client.post(
                reverse("geocaches:import_by_list_generation"),
                {"codes": "GC9999, GC1111", "tags": ""},
            )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "ignore list")
        called_codes = mock_fetch.call_args[0][0]
        self.assertEqual(called_codes, ["GC1111"])

    def test_post_with_uploaded_file(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        upload = SimpleUploadedFile("codes.txt", b"GC1111\nGC2222", content_type="text/plain")
        stats = ImportStats(created=2)
        with patch(
            "geocaches.sync.gc_web.cache_fetch.fetch_and_import_caches", return_value=stats,
        ) as mock_fetch:
            response = self.client.post(
                reverse("geocaches:import_by_list_generation"),
                {"codes": "", "tags": "", "codes_file": upload},
            )
        self.assertEqual(response.status_code, 200)
        called_codes = mock_fetch.call_args[0][0]
        self.assertEqual(called_codes, ["GC1111", "GC2222"])

    @patch("time.sleep")  # skip the real pause between chunks
    def test_chunks_at_the_confirmed_list_max(self, _sleep):
        # 1500 codes -> 2 chunks (1000 + 500), per cache_fetch.LIST_MAX.
        codes = [f"GC{i:05d}" for i in range(1500)]
        stats = ImportStats(created=1)
        with patch(
            "geocaches.sync.gc_web.cache_fetch.fetch_and_import_caches", return_value=stats,
        ) as mock_fetch:
            response = self.client.post(
                reverse("geocaches:import_by_list_generation"),
                {"codes": ", ".join(codes), "tags": ""},
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(mock_fetch.call_count, 2)
        first_chunk = mock_fetch.call_args_list[0][0][0]
        second_chunk = mock_fetch.call_args_list[1][0][0]
        self.assertEqual(len(first_chunk), 1000)
        self.assertEqual(len(second_chunk), 500)

    def test_error_from_fetch_surfaces_without_crashing(self):
        with patch(
            "geocaches.sync.gc_web.cache_fetch.fetch_and_import_caches",
            side_effect=RuntimeError("No GC account configured"),
        ):
            response = self.client.post(
                reverse("geocaches:import_by_list_generation"),
                {"codes": "GC1111", "tags": ""},
            )
        self.assertEqual(response.status_code, 200)
        task_id = response.context["task_id"]
        status_response = self.client.get(
            reverse("geocaches:import_by_list_generation_status", args=[task_id]),
        )
        self.assertContains(status_response, "No GC account configured")
