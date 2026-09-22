"""
Tests for the official-API Pocket Query management page (geocaches/pq.py's
pq_management view + geocaches.pq.service's download queue) — specifically
the "Imported" badge fix.

2026-09-06 report: a recurring PQ ("_Kusterdingen 30km 2011-05-01 bis
2014-08-01") that was successfully imported weeks earlier, then regenerated
and never actually re-downloaded, still showed "Imported". Root cause:
referenceCode is *stable* per saved PQ (identifies the query slot, not a
particular run) — it does NOT change when the PQ regenerates, confirmed live
against the real account (the API returned the exact same referenceCode for
the same PQ on both 2026-08-12 and 2026-09-06). Keying the ``pq_imported``
tracking dict on the bare referenceCode meant the badge reflected "ever
imported", not "this generation imported".
"""

from datetime import date
from unittest.mock import patch

from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from geocaches.views.pq import _pq_generation_pst_date, _pq_is_imported


class PqGenerationPstDateTest(SimpleTestCase):
    def test_parses_utc_into_pst_date(self):
        pq = {"lastUpdatedDateUtc": "2026-09-06T14:13:10.143"}
        self.assertEqual(_pq_generation_pst_date(pq), date(2026, 9, 6))

    def test_utc_near_midnight_rolls_back_a_pst_day(self):
        # 2026-08-18T02:00:00Z is still 2026-08-17 in PDT (UTC-7).
        pq = {"lastUpdatedDateUtc": "2026-08-18T02:00:00Z"}
        self.assertEqual(_pq_generation_pst_date(pq), date(2026, 8, 17))

    def test_missing_timestamp_returns_none(self):
        self.assertIsNone(_pq_generation_pst_date({}))

    def test_unparseable_timestamp_returns_none(self):
        self.assertIsNone(_pq_generation_pst_date({"lastUpdatedDateUtc": "not-a-date"}))


class PqIsImportedTest(SimpleTestCase):
    def test_stale_referenceCode_hit_from_a_different_generation_is_not_imported(self):
        """The exact 2026-09-06 regression: same referenceCode, but the
        recorded import is from a much earlier generation."""
        pq = {"referenceCode": "PQ7HY70", "lastUpdatedDateUtc": "2026-09-06T14:13:10.143"}
        imported_pqs = {"PQ7HY70@2026-08-12": "2026-08-12T15:39:07+00:00"}

        self.assertFalse(_pq_is_imported(pq, imported_pqs))

    def test_matching_generation_date_is_imported(self):
        pq = {"referenceCode": "PQND4TX", "lastUpdatedDateUtc": "2026-09-06T14:14:17.330719"}
        imported_pqs = {"PQND4TX@2026-09-06": "2026-09-06T14:14:21.000000+00:00"}

        self.assertTrue(_pq_is_imported(pq, imported_pqs))

    def test_no_generation_date_is_not_imported(self):
        pq = {"referenceCode": "PQND4TX"}
        self.assertFalse(_pq_is_imported(pq, {"PQND4TX@2026-09-06": "x"}))


class QueueWorkerMarksGenerationDateTest(SimpleTestCase):
    """geocaches.pq.service._queue_worker (shared by bulk_trigger_and_download,
    trigger_and_download_by_pattern, download_all_fresh, and the manual
    checkbox download action) must key the import record per generation."""

    def setUp(self):
        from geocaches.pq import service as pq_service
        pq_service._download_queue.clear()

    tearDown = setUp

    @patch("geocaches.pq.service._start_batch_enrich")
    @patch("geocaches.pq.service._do_download_and_import")
    def test_explicit_generation_date_is_used(self, mock_download, _mock_enrich):
        from geocaches.pq import service as pq_service

        mock_download.return_value = {"created": 1, "updated": 0, "locked": 0, "errors": []}
        pq_service._download_queue.append(("PQOLD", "Old Ready PQ", None, date(2026, 8, 20)))

        with patch("geocaches.pq.service._mark_pq_imported") as mock_mark:
            pq_service._queue_worker()

        mock_mark.assert_called_once_with("PQOLD@2026-08-20")

    @patch("geocaches.pq.trigger._gc_today_pst", return_value=date(2026, 9, 6))
    @patch("geocaches.pq.service._start_batch_enrich")
    @patch("geocaches.pq.service._do_download_and_import")
    def test_missing_generation_date_defaults_to_today_pst(
        self, mock_download, _mock_enrich, _mock_today,
    ):
        from geocaches.pq import service as pq_service

        mock_download.return_value = {"created": 0, "updated": 1, "locked": 0, "errors": []}
        pq_service._download_queue.append(("PQFRESH", "Freshly Triggered PQ", None, None))

        with patch("geocaches.pq.service._mark_pq_imported") as mock_mark:
            pq_service._queue_worker()

        mock_mark.assert_called_once_with("PQFRESH@2026-09-06")


class PqManagementViewImportedBadgeTest(TestCase):
    @patch("geocaches.pq.service.ensure_web_status_fresh")
    @patch("geocaches.pq.service.get_web_status_snapshot")
    @patch("geocaches.pq.service.get_imported_pqs")
    @patch("geocaches.pq.service.list_pocket_queries")
    def test_stuck_pq_no_longer_shows_imported(
        self, mock_list, mock_imported, mock_snapshot, _mock_fresh,
    ):
        mock_list.return_value = [{
            "referenceCode": "PQ7HY70",
            "name": "_Kusterdingen 30km 2000-01-01 bis 2011-05-01",
            "lastUpdatedDateUtc": "2026-09-06T14:13:10.143",
        }]
        mock_imported.return_value = {"PQ7HY70@2026-08-12": "2026-08-12T15:39:07+00:00"}
        mock_snapshot.return_value = {"rows": [], "summary": {}, "refreshing": False}

        resp = self.client.get(reverse("geocaches:pq_management"))

        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, "Imported")

    @patch("geocaches.pq.service.ensure_web_status_fresh")
    @patch("geocaches.pq.service.get_web_status_snapshot")
    @patch("geocaches.pq.service.get_imported_pqs")
    @patch("geocaches.pq.service.list_pocket_queries")
    def test_freshly_reimported_pq_shows_imported(
        self, mock_list, mock_imported, mock_snapshot, _mock_fresh,
    ):
        mock_list.return_value = [{
            "referenceCode": "PQND4TX",
            "name": "_Kusterdingen 30km 2025-04-01 bis",
            "lastUpdatedDateUtc": "2026-09-06T14:13:22.147",
        }]
        mock_imported.return_value = {"PQND4TX@2026-09-06": "2026-09-06T14:14:17+00:00"}
        mock_snapshot.return_value = {"rows": [], "summary": {}, "refreshing": False}

        resp = self.client.get(reverse("geocaches:pq_management"))

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Imported")

    @patch("geocaches.pq.service.ensure_web_status_fresh")
    @patch("geocaches.pq.service.get_web_status_snapshot")
    @patch("geocaches.pq.service.get_imported_pqs", return_value={})
    @patch("geocaches.pq.service.list_pocket_queries")
    @patch("geocaches.pq.service.enqueue_pq_download")
    def test_download_action_forwards_the_rows_own_generation_date(
        self, mock_enqueue, mock_list, _mock_imported, mock_snapshot, _mock_fresh,
    ):
        """A manual download of a non-fresh (but still-ready) row must key
        the import record on *that row's* generation date, not today's —
        otherwise the badge mismatches on the next page load."""
        mock_list.return_value = []
        mock_snapshot.return_value = {"rows": [], "summary": {}, "refreshing": False}
        mock_enqueue.return_value = "task-1"

        resp = self.client.post(reverse("geocaches:pq_management"), {
            "action": "download",
            "selected": ["PQOLD"],
            "name_PQOLD": "Old Ready PQ",
            "date_PQOLD": "2026-08-20",
        })

        self.assertEqual(resp.status_code, 302)
        mock_enqueue.assert_called_once_with(
            "PQOLD", "Old Ready PQ", tag_names=None, generation_date=date(2026, 8, 20),
        )
