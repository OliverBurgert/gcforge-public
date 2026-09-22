"""
Tests for the website-only Pocket Query download path (no partner-API
token) — geocaches.pq.trigger's "Ready for Download" tab parser +
downloadpq.ashx client, and geocaches.pq.service's shared import helper.
Confirmed live 2026-08-15 (see pq/trigger.py's module docstring). No live
network calls in the test suite.
"""

import unicodedata
from datetime import date
from unittest.mock import MagicMock, patch

from bs4 import BeautifulSoup
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from geocaches.pq import trigger as pq_trigger

# A trimmed real table structure (2026-08-15), same shape as the
# "Pocket Queries Ready for Download" tab on /pocket/default.aspx.
_READY_TABLE_HTML = """
<table>
<tr>
  <th></th><th></th><th>Name</th>
  <th class="AlignRight">File Size</th>
  <th class="AlignCenter">Waypoints</th>
  <th>Last Generated (PST)</th>
</tr>
<tr>
  <td><input type="checkbox" name="chk" value="111"></td>
  <td>1.</td>
  <td><a href="/pocket/downloadpq.ashx?g=7fcfdeb0-7029-440e-bd8e-9ed3eed726f2&amp;src=web">Kusterdingen 30km</a></td>
  <td class="AlignRight">623.07 KB</td>
  <td class="AlignCenter">445</td>
  <td>2026-08-12 (4 days remaining)</td>
</tr>
<tr>
  <td><input type="checkbox" name="chk" value="222"></td>
  <td>2.</td>
  <td><a href="/pocket/downloadpq.ashx?g=ee95574c-738a-423b-aac1-e05640f98db8&amp;src=web">Ponyhof Route</a></td>
  <td class="AlignRight">1.42 MB</td>
  <td class="AlignCenter">933</td>
  <td>2026-08-10</td>
</tr>
</table>
"""


class TestParseFileSize(SimpleTestCase):
    def test_kb(self):
        self.assertEqual(pq_trigger._parse_file_size("623.07 KB"), int(623.07 * 1024))

    def test_mb(self):
        self.assertEqual(pq_trigger._parse_file_size("1.42 MB"), int(1.42 * 1024 * 1024))

    def test_unparseable_returns_none(self):
        self.assertIsNone(pq_trigger._parse_file_size("n/a"))


class TestParseDownloadablePqRows(SimpleTestCase):
    def test_parses_all_fields(self):
        soup = BeautifulSoup(_READY_TABLE_HTML, "html.parser")
        rows = pq_trigger._parse_downloadable_pq_rows(soup)

        self.assertEqual(len(rows), 2)
        first = rows[0]
        self.assertEqual(first["name"], "Kusterdingen 30km")
        self.assertEqual(first["guid"], "7fcfdeb0-7029-440e-bd8e-9ed3eed726f2")
        self.assertEqual(first["file_size_text"], "623.07 KB")
        self.assertEqual(first["file_size_bytes"], int(623.07 * 1024))
        self.assertEqual(first["waypoint_count"], 445)
        self.assertEqual(first["last_generated_date"], date(2026, 8, 12))
        self.assertEqual(first["expires_in_days"], 4)

    def test_last_generated_without_expiry_suffix(self):
        soup = BeautifulSoup(_READY_TABLE_HTML, "html.parser")
        rows = pq_trigger._parse_downloadable_pq_rows(soup)

        second = rows[1]
        self.assertEqual(second["last_generated_date"], date(2026, 8, 10))
        self.assertIsNone(second["expires_in_days"])

    def test_no_table_returns_empty_list(self):
        soup = BeautifulSoup("<html><body>nothing here</body></html>", "html.parser")
        self.assertEqual(pq_trigger._parse_downloadable_pq_rows(soup), [])


class TestListDownloadablePqsWeb(SimpleTestCase):
    @patch("geocaches.pq.trigger._fetch_page")
    def test_delegates_to_row_parser(self, mock_fetch):
        mock_fetch.return_value = BeautifulSoup(_READY_TABLE_HTML, "html.parser")

        rows = pq_trigger.list_downloadable_pqs_web()

        self.assertEqual(len(rows), 2)
        mock_fetch.assert_called_once_with()


class TestDownloadPqWeb(SimpleTestCase):
    def test_sends_the_confirmed_query_params(self):
        session = MagicMock()
        session.get.return_value.content = b"PK\x03\x04zip-bytes"
        session.get.return_value.url = "https://www.geocaching.com/pocket/downloadpq.ashx"

        result = pq_trigger.download_pq_web("7fcfdeb0-7029-440e-bd8e-9ed3eed726f2", session=session)

        self.assertEqual(result, b"PK\x03\x04zip-bytes")
        args, kwargs = session.get.call_args
        self.assertEqual(args[0], "https://www.geocaching.com/pocket/downloadpq.ashx")
        self.assertEqual(kwargs["params"], {"g": "7fcfdeb0-7029-440e-bd8e-9ed3eed726f2", "src": "web"})
        session.get.return_value.raise_for_status.assert_called_once()

    @patch("geocaches.pq.trigger._pq_reset_session")
    def test_resets_session_on_signin_redirect(self, mock_reset):
        session = MagicMock()
        session.get.return_value.url = "https://www.geocaching.com/account/signin?ReturnUrl=%2fpocket%2f"

        with self.assertRaises(RuntimeError):
            pq_trigger.download_pq_web("some-guid", session=session)
        mock_reset.assert_called_once()


class TestDownloadAndImportPqWeb(SimpleTestCase):
    @patch("geocaches.services.import_and_enrich")
    @patch("geocaches.pq.trigger.download_pq_web", return_value=b"PK\x03\x04fakezip")
    def test_downloads_imports_and_marks_imported(self, mock_download, mock_import):
        from geocaches.pq.service import download_and_import_pq_web

        mock_import.return_value = MagicMock(created=3, updated=1, locked=0, errors=[])

        with patch("geocaches.pq.service._mark_pq_imported") as mock_mark:
            result = download_and_import_pq_web(
                "7fcfdeb0-7029-440e-bd8e-9ed3eed726f2", "Kusterdingen 30km", tag_names=["t1"],
                generation_date=date(2026, 9, 6),
            )

        self.assertEqual(result["created"], 3)
        self.assertEqual(result["updated"], 1)
        mock_download.assert_called_once_with("7fcfdeb0-7029-440e-bd8e-9ed3eed726f2")
        mock_import.assert_called_once()
        args, kwargs = mock_import.call_args
        self.assertEqual(args[0], "unified_gpx")
        self.assertEqual(args[2], ["t1"])
        # Keyed by (guid, generation date), not the bare guid — see
        # pq_import_key() — so a later regeneration of the same
        # recurring PQ isn't shown as "Imported" from a stale past run
        # (2026-09-06: exactly this made 2 undownloaded PQs in a 7-PQ
        # pattern trigger show "Imported" from a run weeks earlier).
        mock_mark.assert_called_once_with("7fcfdeb0-7029-440e-bd8e-9ed3eed726f2@2026-09-06")

    @patch("geocaches.services.import_and_enrich")
    @patch("geocaches.pq.trigger.download_pq_web", return_value=b"PK\x03\x04fakezip")
    @patch("geocaches.pq.trigger._gc_today_pst", return_value=date(2026, 9, 6))
    def test_defaults_generation_date_to_today_pst(self, _mock_today, mock_download, mock_import):
        """No explicit generation_date (the actual call shape from every
        current caller) still keys on today's PST date, not the bare guid."""
        from geocaches.pq.service import download_and_import_pq_web

        mock_import.return_value = MagicMock(created=0, updated=1, locked=0, errors=[])

        with patch("geocaches.pq.service._mark_pq_imported") as mock_mark:
            download_and_import_pq_web("some-guid", "Some PQ")

        mock_mark.assert_called_once_with("some-guid@2026-09-06")


class TestWaitForPqGenerationWeb(SimpleTestCase):
    @patch("geocaches.pq.trigger.list_downloadable_pqs_web")
    def test_detects_ready_guid_by_date(self, mock_list):
        from datetime import datetime, timezone

        mock_list.return_value = [{"guid": "abc", "last_generated_date": date(2026, 8, 15)}]
        since = datetime(2026, 8, 15, 10, 0, tzinfo=timezone.utc)

        completed = pq_trigger.wait_for_pq_generation_web(["abc"], since, poll_interval=0.01)

        self.assertEqual(completed, {"abc": True})

    def test_stale_generation_before_since_is_not_ready(self):
        from datetime import datetime, timezone

        since = datetime(2026, 8, 15, 10, 0, tzinfo=timezone.utc)

        with patch("geocaches.pq.trigger.list_downloadable_pqs_web") as mock_list:
            mock_list.return_value = [{"guid": "abc", "last_generated_date": date(2026, 8, 10)}]
            completed = pq_trigger.wait_for_pq_generation_web(["abc"], since, timeout=0)

        self.assertEqual(completed, {"abc": False})

    def test_times_out_when_guid_never_appears(self):
        from datetime import datetime, timezone

        since = datetime(2026, 8, 15, 10, 0, tzinfo=timezone.utc)

        completed = pq_trigger.wait_for_pq_generation_web(["missing"], since, timeout=0)

        self.assertEqual(completed, {"missing": False})


class TestTriggerAndDownloadWeb(SimpleTestCase):
    @patch("geocaches.pq.trigger.wait_for_pq_generation_web")
    @patch("geocaches.pq.trigger.trigger_pqs_by_guid")
    @patch("geocaches.pq.service.download_and_import_pq_web")
    def test_triggers_waits_and_downloads(self, mock_download, mock_trigger, mock_wait):
        from geocaches.pq.service import trigger_and_download_web

        mock_trigger.return_value = [{"guid": "abc", "name": "Query A", "status": "triggered"}]
        mock_wait.return_value = {"abc": True}
        mock_download.return_value = {"created": 5, "updated": 1, "locked": 0, "errors": []}

        result = trigger_and_download_web(["abc"], tag_names=["t1"])

        self.assertEqual(result["created"], 5)
        self.assertEqual(result["updated"], 1)
        self.assertEqual(result["errors"], [])
        mock_trigger.assert_called_once_with(["abc"])
        mock_wait.assert_called_once()
        mock_download.assert_called_once_with("abc", "Query A", tag_names=["t1"])

    @patch("geocaches.pq.trigger.trigger_pqs_by_guid")
    @patch("geocaches.pq.service.download_and_import_pq_web")
    def test_already_ran_pqs_download_immediately_without_waiting(self, mock_download, mock_trigger):
        from geocaches.pq.service import trigger_and_download_web

        mock_trigger.return_value = [{"guid": "abc", "name": "Query A", "status": "already_ran"}]
        mock_download.return_value = {"created": 2, "updated": 0, "locked": 0, "errors": []}

        with patch("geocaches.pq.trigger.wait_for_pq_generation_web") as mock_wait:
            result = trigger_and_download_web(["abc"])

        mock_wait.assert_not_called()
        mock_download.assert_called_once_with("abc", "Query A", tag_names=None)
        self.assertEqual(result["created"], 2)

    @patch("geocaches.pq.trigger.trigger_pqs_by_guid")
    def test_trigger_failures_are_reported_not_raised(self, mock_trigger):
        from geocaches.pq.service import trigger_and_download_web

        mock_trigger.return_value = [{"guid": "abc", "name": "Query A", "status": "limit_reached"}]

        result = trigger_and_download_web(["abc"])

        self.assertEqual(result["created"], 0)
        self.assertEqual(len(result["trigger_errors"]), 1)
        self.assertIn("limit_reached", result["errors"][0])

    @patch("geocaches.pq.trigger.wait_for_pq_generation_web")
    @patch("geocaches.pq.trigger.trigger_pqs_by_guid")
    def test_generation_timeout_is_reported(self, mock_trigger, mock_wait):
        from geocaches.pq.service import trigger_and_download_web

        mock_trigger.return_value = [{"guid": "abc", "name": "Query A", "status": "triggered"}]
        mock_wait.return_value = {"abc": False}

        result = trigger_and_download_web(["abc"])

        self.assertEqual(result["created"], 0)
        self.assertIn("timed out", result["errors"][0])


class TestNormalizePqName(SimpleTestCase):
    """2026-09-05: a 5-PQ pattern trigger got stuck at 3/5 because 2 names
    scraped from the website never matched the API's ``name`` field for the
    same PQ — one due to an nbsp where the site's HTML had a doubled space,
    one due to differing Unicode normalization of its umlauts."""

    def test_collapses_nbsp_and_doubled_whitespace(self):
        self.assertEqual(
            pq_trigger._normalize_pq_name("Ueberlingen 30km \xa02025-03-01 bis"),
            pq_trigger._normalize_pq_name("Ueberlingen 30km 2025-03-01 bis"),
        )

    def test_normalizes_unicode_form(self):
        composed = "Gönningen_Ueberlingen Zwischenstück"  # NFC (ö = U+00F6)
        decomposed = unicodedata.normalize("NFD", composed)  # ö = "o" + U+0308
        self.assertNotEqual(composed, decomposed)
        self.assertEqual(
            pq_trigger._normalize_pq_name(composed),
            pq_trigger._normalize_pq_name(decomposed),
        )


class TestWaitForPqGeneration(SimpleTestCase):
    """The referenceCode-based waiter (checkbox-driven bulk trigger+download)
    has the identical PST-date-vs-exact-instant fix as the by-name waiter
    below — same clock-skew/fast-generation vulnerability, same repair."""

    @patch("geocaches.pq.trigger._poll_interval", return_value=0.01)
    @patch("geocaches.pq.service.list_pocket_queries")
    def test_resolves_despite_lastupdated_a_few_seconds_before_since(self, mock_list, _mock_interval):
        from datetime import datetime, timezone

        since = datetime(2026, 9, 6, 14, 13, 13, tzinfo=timezone.utc)
        mock_list.return_value = [{
            "referenceCode": "PQEARLY",
            "lastUpdatedDateUtc": "2026-09-06T14:13:10.143",  # 3s *before* since
        }]

        completed = pq_trigger.wait_for_pq_generation(["PQEARLY"], since, timeout=0.05)

        self.assertEqual(completed, {"PQEARLY": True})

    @patch("geocaches.pq.trigger._poll_interval", return_value=0.01)
    @patch("geocaches.pq.service.list_pocket_queries")
    def test_stale_previous_day_hit_still_rejected(self, mock_list, _mock_interval):
        from datetime import datetime, timezone

        since = datetime(2026, 9, 6, 14, 13, 13, tzinfo=timezone.utc)
        mock_list.return_value = [{
            "referenceCode": "PQSTALE",
            "lastUpdatedDateUtc": "2026-09-05T20:00:00Z",  # previous PST day
        }]

        completed = pq_trigger.wait_for_pq_generation(["PQSTALE"], since, timeout=0.05)

        self.assertEqual(completed, {"PQSTALE": False})


class TestWaitForPqGenerationByName(SimpleTestCase):
    @patch("geocaches.pq.trigger._poll_interval", return_value=0.01)
    @patch("geocaches.pq.service.list_pocket_queries")
    def test_matches_names_despite_nbsp_difference(self, mock_list, _mock_interval):
        from datetime import datetime, timezone

        since = datetime(2026, 9, 5, tzinfo=timezone.utc)
        mock_list.return_value = [{
            "name": "Ueberlingen 30km 2025-03-01 bis",  # API's plain-space name
            "referenceCode": "PQXYZ",
            "lastUpdatedDateUtc": "2026-09-05T12:00:00Z",
        }]

        resolved = pq_trigger.wait_for_pq_generation_by_name(
            {"Ueberlingen 30km \xa02025-03-01 bis": since},  # scraped nbsp name
            timeout=0.05,
        )

        self.assertEqual(resolved, {"Ueberlingen 30km \xa02025-03-01 bis": "PQXYZ"})

    @patch("geocaches.pq.trigger._poll_interval", return_value=0.01)
    @patch("geocaches.pq.service.list_pocket_queries")
    def test_on_resolved_fires_per_name_without_waiting_for_stragglers(self, mock_list, _mock_interval):
        from datetime import datetime, timezone

        since = datetime(2026, 9, 5, tzinfo=timezone.utc)
        mock_list.return_value = [{
            "name": "Ready One", "referenceCode": "PQREADY",
            "lastUpdatedDateUtc": "2026-09-05T12:00:00Z",
        }]

        resolved_calls = []
        resolved = pq_trigger.wait_for_pq_generation_by_name(
            {"Ready One": since, "Never Matches": since},
            timeout=0.05,
            on_resolved=lambda name, ref: resolved_calls.append((name, ref)),
        )

        self.assertEqual(resolved_calls, [("Ready One", "PQREADY")])
        self.assertEqual(resolved, {"Ready One": "PQREADY"})

    @patch("geocaches.pq.trigger._poll_interval", return_value=0.01)
    @patch("geocaches.pq.service.list_pocket_queries")
    def test_resolves_despite_lastupdated_a_few_seconds_before_since(self, mock_list, _mock_interval):
        """2026-09-06: in a 7-PQ pattern trigger, the 2 earliest-triggered
        names got permanently stuck because GC stamped lastUpdatedDateUtc a
        few seconds *before* our own locally-captured `since` (GC generated
        fast enough that clock skew / our own multi-item trigger loop's
        overhead put the recorded instant just on the wrong side of an exact
        `>=` comparison) — the other 5, triggered a couple of seconds later,
        happened to clear it. Comparing PST *dates* instead of instants
        fixes this without losing the "not a stale prior-day hit" guard."""
        from datetime import datetime, timezone

        since = datetime(2026, 9, 6, 14, 13, 13, tzinfo=timezone.utc)
        mock_list.return_value = [{
            "name": "Kusterdingen Early One",
            "referenceCode": "PQEARLY",
            "lastUpdatedDateUtc": "2026-09-06T14:13:10.143",  # 3s *before* since
        }]

        resolved = pq_trigger.wait_for_pq_generation_by_name(
            {"Kusterdingen Early One": since},
            timeout=0.05,
        )

        self.assertEqual(resolved, {"Kusterdingen Early One": "PQEARLY"})

    @patch("geocaches.pq.trigger._poll_interval", return_value=0.01)
    @patch("geocaches.pq.service.list_pocket_queries")
    def test_stale_previous_day_hit_still_rejected(self, mock_list, _mock_interval):
        """The PST-date comparison must still reject a hit from a genuinely
        earlier day — only the same-day clock-skew case (above) is relaxed."""
        from datetime import datetime, timezone

        since = datetime(2026, 9, 6, 14, 13, 13, tzinfo=timezone.utc)
        mock_list.return_value = [{
            "name": "Stale From Yesterday",
            "referenceCode": "PQSTALE",
            "lastUpdatedDateUtc": "2026-09-05T20:00:00Z",  # previous PST day
        }]

        resolved = pq_trigger.wait_for_pq_generation_by_name(
            {"Stale From Yesterday": since},
            timeout=0.05,
        )

        self.assertEqual(resolved, {})


class TestTriggerAndDownloadByPattern(SimpleTestCase):
    @patch("geocaches.pq.service.enqueue_pq_download")
    @patch("geocaches.pq.trigger.wait_for_pq_generation_by_name")
    @patch("geocaches.pq.trigger.trigger_pqs_by_guid")
    @patch("geocaches.pq.trigger.get_pq_web_status")
    def test_downloads_are_queued_as_each_name_resolves(
        self, mock_status, mock_trigger, mock_wait, mock_enqueue,
    ):
        """A straggler that never resolves must not hold up a PQ that's
        already ready — the regression behind the 2026-09-05 stuck-at-3/5
        report."""
        from geocaches.pq.service import trigger_and_download_by_pattern

        mock_status.return_value = ([
            {"name": "Ueberlingen Ready One", "guid": "g1"},
            {"name": "Ueberlingen Never Matches", "guid": "g2"},
        ], {})
        mock_trigger.return_value = [
            {"guid": "g1", "name": "Ueberlingen Ready One", "status": "triggered"},
            {"guid": "g2", "name": "Ueberlingen Never Matches", "status": "triggered"},
        ]

        def _wait(since_by_name, task_info=None, on_resolved=None):
            on_resolved("Ueberlingen Ready One", "PQREADY")
            return {"Ueberlingen Ready One": "PQREADY"}

        mock_wait.side_effect = _wait
        mock_enqueue.return_value = "task-123"

        result = trigger_and_download_by_pattern("Ueberlingen")

        mock_enqueue.assert_called_once_with("PQREADY", "Ueberlingen Ready One", tag_names=None)
        rows = {r["pq_name"]: r for r in result["results"]}
        self.assertEqual(rows["Ueberlingen Ready One"]["status"], "queued")
        self.assertEqual(rows["Ueberlingen Never Matches"]["error"], "Generation timed out")
        self.assertEqual(result["queue_task_id"], "task-123")


class TestPqManagementWebView(TestCase):
    @patch("geocaches.pq.trigger.get_pq_web_status", return_value=([], {}))
    @patch("geocaches.pq.trigger.list_downloadable_pqs_web")
    def test_get_renders_the_ready_list(self, mock_list, _mock_status):
        mock_list.return_value = [{
            "name": "Kusterdingen 30km", "guid": "7fcfdeb0-7029-440e-bd8e-9ed3eed726f2",
            "file_size_text": "623.07 KB", "file_size_bytes": 638023,
            "waypoint_count": 445, "last_generated_text": "2026-08-12 (4 days remaining)",
            "last_generated_date": date(2026, 8, 12), "expires_in_days": 4,
        }]

        resp = self.client.get(reverse("geocaches:pq_management_web"))

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Kusterdingen 30km")
        self.assertContains(resp, "623.07 KB")

    @patch("geocaches.pq.trigger.get_pq_web_status", return_value=([], {}))
    @patch("geocaches.pq.trigger.list_downloadable_pqs_web")
    def test_imported_badge_does_not_stick_across_a_new_generation(self, mock_list, _mock_status):
        """2026-09-06 report: a recurring PQ imported weeks ago, then
        regenerated and never actually re-downloaded, still showed
        "Imported" — the badge must reflect *this* generation."""
        from geocaches.pq.service import pq_import_key
        from preferences.models import UserPreference

        guid = "45760092-82b7-4f36-ae69-cec08c88e2a4"
        UserPreference.set("pq_imported", {
            pq_import_key(guid, date(2026, 8, 17)): "2026-08-17T10:54:35+00:00",
        })
        mock_list.return_value = [{
            "name": "_Kusterdingen 30km 2011-05-01 bis 2014-08-01", "guid": guid,
            "file_size_text": "1.5 MB", "file_size_bytes": 1500000,
            "waypoint_count": 973, "last_generated_text": "2026-09-06 (5 days remaining)",
            "last_generated_date": date(2026, 9, 6), "expires_in_days": 5,
        }]

        resp = self.client.get(reverse("geocaches:pq_management_web"))

        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, "Imported")

    @patch("geocaches.pq.trigger.get_pq_web_status", return_value=([], {}))
    @patch("geocaches.pq.trigger.list_downloadable_pqs_web", return_value=[])
    def test_get_with_no_ready_pqs_shows_empty_state(self, _mock_list, _mock_status):
        resp = self.client.get(reverse("geocaches:pq_management_web"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "No Pocket Queries are ready for download")

    @patch("geocaches.pq.trigger.get_pq_web_status")
    def test_get_renders_active_pqs_with_status_badges(self, mock_status):
        mock_status.return_value = ([
            {"name": "Ran Today Query", "guid": "aaa", "delete_id": "1", "is_deleted": False,
             "trigger_url": "", "already_ran": True, "already_sched": False, "last_gen_text": "08/15/2026"},
            {"name": "Schedulable Query", "guid": "bbb", "delete_id": "2", "is_deleted": False,
             "trigger_url": "https://www.geocaching.com/pocket/default.aspx?pq=bbb&d=2&opt=1",
             "already_ran": False, "already_sched": False, "last_gen_text": ""},
        ], {"today_pst": "2026-08-15", "ran_today": 1, "remaining_triggers": 9})

        with patch("geocaches.pq.trigger.list_downloadable_pqs_web", return_value=[]):
            resp = self.client.get(reverse("geocaches:pq_management_web"))

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Ran Today Query")
        self.assertContains(resp, "Schedulable Query")
        self.assertContains(resp, "Ran today")
        self.assertContains(resp, "Ready to trigger")

    @patch("geocaches.pq.trigger.get_pq_web_status", return_value=([], {}))
    @patch("geocaches.pq.trigger.list_downloadable_pqs_web", return_value=[])
    def test_post_without_selection_shows_error(self, _mock_list, _mock_status):
        resp = self.client.post(reverse("geocaches:pq_management_web"), {})
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Select at least one Pocket Query")

    @patch("geocaches.pq.trigger.get_pq_web_status", return_value=([], {}))
    @patch("geocaches.pq.trigger.list_downloadable_pqs_web", return_value=[])
    @patch("geocaches.pq.service.download_and_import_pq_web")
    def test_post_with_selection_runs_the_download_task(self, mock_download, _mock_list, _mock_status):
        mock_download.return_value = {"created": 2, "updated": 0, "locked": 0, "errors": []}

        resp = self.client.post(reverse("geocaches:pq_management_web"), {
            "action": "download",
            "selected": ["7fcfdeb0-7029-440e-bd8e-9ed3eed726f2"],
            "name_7fcfdeb0-7029-440e-bd8e-9ed3eed726f2": "Kusterdingen 30km",
            "tags": "test-tag",
        })

        self.assertEqual(resp.status_code, 200)
        mock_download.assert_called_once_with(
            "7fcfdeb0-7029-440e-bd8e-9ed3eed726f2", "Kusterdingen 30km", tag_names=["test-tag"],
        )
        self.assertContains(resp, "created")  # task completed synchronously under the test runner

    @patch("geocaches.pq.trigger.get_pq_web_status", return_value=([], {}))
    @patch("geocaches.pq.trigger.list_downloadable_pqs_web", return_value=[])
    @patch("geocaches.pq.service.trigger_and_download_web")
    def test_post_with_trigger_action_runs_the_trigger_task(self, mock_trigger, _mock_list, _mock_status):
        mock_trigger.return_value = {"created": 3, "updated": 0, "locked": 0, "errors": [], "trigger_errors": []}

        resp = self.client.post(reverse("geocaches:pq_management_web"), {
            "action": "trigger",
            "selected": ["bbb"],
            "name_bbb": "Schedulable Query",
            "tags": "test-tag",
        })

        self.assertEqual(resp.status_code, 200)
        args, kwargs = mock_trigger.call_args
        self.assertEqual(args[0], ["bbb"])
        self.assertEqual(kwargs["names"], {"bbb": "Schedulable Query"})
        self.assertEqual(kwargs["tag_names"], ["test-tag"])
        self.assertContains(resp, "created")
