"""
Tests for the website-only GC log-draft ("Field Notes") download path (no
partner-API token) — geocaches.sync.gc_web.fieldnotes' list/detail scrapers
and geocaches.importers.fieldnote's api_preferred/web_preferred resolver.

Confirmed live 2026-08-16 via a real upload -> list -> detail -> delete
round trip on Oliver's own account — see gc_web/fieldnotes.py's module
docstring. No live network calls in the test suite.
"""

import tempfile
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase, TestCase, override_settings

from geocaches.sync.gc_web import fieldnotes as web_fieldnotes

# Real response shapes, 2026-08-16 live capture.
_LIST_RESPONSE = {
    "total": 1,
    "data": [
        {
            "logTypeId": 4,
            "referenceCode": "LDAZQ7NH",
            "guid": "05b39a22-3591-45df-a83e-980f628a3f1f",
            "notePreview": "GCForge test draft - safe to delete",
            "dateLoggedUtc": "2026-08-16T12:00:00",
            "dateLoggedGeocacheTime": "2026-08-16T14:00:00",
            "geocache": {
                "geocacheType": {"id": 2, "name": "Traditional Caches"},
                "name": "Bahnschatz #3",
                "referenceCode": "GCBPNTE",
                "ianaTimezoneId": "Europe/Berlin",
                "state": {"isArchived": False, "isAvailable": True, "isPremiumOnly": False, "isPublished": True, "isLocked": False},
            },
        },
    ],
}

_DETAIL_RESPONSE = {
    "note": "GCForge test draft - safe to delete",
    "useFavoritePoint": False,
    "isArchived": False,
    "imageCount": 0,
    "logTypeId": 4,
    "referenceCode": "LDAZQ7NH",
    "guid": "05b39a22-3591-45df-a83e-980f628a3f1f",
    "notePreview": "GCForge test draft - safe to delete",
    "dateLoggedUtc": "2026-08-16T12:00:00",
    "dateLoggedGeocacheTime": "2026-08-16T14:00:00",
    "geocache": {
        "geocacheType": {"id": 2, "name": "Traditional Caches"},
        "name": "Bahnschatz #3",
        "referenceCode": "GCBPNTE",
        "ianaTimezoneId": "Europe/Berlin",
        "state": {"isArchived": False, "isAvailable": True, "isPremiumOnly": False, "isPublished": True, "isLocked": False},
    },
}


def _resp(json_body, url="https://www.geocaching.com/api/proxy/web/v1/LogDrafts"):
    m = MagicMock()
    m.json.return_value = json_body
    m.url = url
    return m


class TestListDraftsWeb(SimpleTestCase):
    def test_parses_summary_list(self):
        session = MagicMock()
        session.get.return_value = _resp(_LIST_RESPONSE)

        rows = web_fieldnotes.list_drafts_web(session=session)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["referenceCode"], "LDAZQ7NH")
        kwargs = session.get.call_args[1]
        self.assertEqual(kwargs["params"], {"sortAsc": "false"})

    @patch("geocaches.sync.gc_web.fieldnotes.reset_session")
    def test_resets_session_on_signin_redirect(self, mock_reset):
        session = MagicMock()
        session.get.return_value = _resp({}, url="https://www.geocaching.com/account/signin?ReturnUrl=%2f")

        with self.assertRaises(RuntimeError):
            web_fieldnotes.list_drafts_web(session=session)
        mock_reset.assert_called_once()


class TestFetchDraftDetailWeb(SimpleTestCase):
    def test_parses_full_detail(self):
        session = MagicMock()
        session.get.return_value = _resp(_DETAIL_RESPONSE)

        detail = web_fieldnotes.fetch_draft_detail_web("LDAZQ7NH", session=session)

        self.assertEqual(detail["note"], "GCForge test draft - safe to delete")
        self.assertEqual(detail["geocache"]["referenceCode"], "GCBPNTE")
        call_url = session.get.call_args[0][0]
        self.assertTrue(call_url.endswith("/LogDrafts/LDAZQ7NH"))


class TestFetchAllDraftsWeb(SimpleTestCase):
    @patch("geocaches.sync.gc_web.fieldnotes.polite_delay")
    def test_fetches_detail_for_every_listed_draft(self, _delay):
        session = MagicMock()
        session.get.side_effect = [_resp(_LIST_RESPONSE), _resp(_DETAIL_RESPONSE)]

        drafts = web_fieldnotes.fetch_all_drafts_web(session=session)

        self.assertEqual(len(drafts), 1)
        self.assertEqual(drafts[0]["note"], "GCForge test draft - safe to delete")
        self.assertEqual(session.get.call_count, 2)

    def test_skips_rows_without_reference_code(self):
        session = MagicMock()
        empty_row_list = {"total": 1, "data": [{"referenceCode": ""}]}
        session.get.return_value = _resp(empty_row_list)

        drafts = web_fieldnotes.fetch_all_drafts_web(session=session)

        self.assertEqual(drafts, [])
        session.get.assert_called_once()  # only the list call, no detail call


class TestDownloadGcFieldnotesWeb(TestCase):
    def setUp(self):
        self._tmp_dir = tempfile.TemporaryDirectory()
        self._override = override_settings(FIELDNOTES_DIR=self._tmp_dir.name)
        self._override.enable()

    def tearDown(self):
        self._override.disable()
        self._tmp_dir.cleanup()

    @patch("geocaches.sync.gc_web.fieldnotes.fetch_all_drafts_web")
    def test_writes_field_note_file_with_true_utc(self, mock_fetch):
        mock_fetch.return_value = [_DETAIL_RESPONSE]

        path = web_fieldnotes.download_gc_fieldnotes_web()

        content = path.read_text(encoding="utf-8")
        self.assertIn("GCBPNTE,2026-08-16T12:00:00Z,Write note,", content)
        self.assertIn('"GCForge test draft - safe to delete"', content)
        # dateLoggedGeocacheTime (14:00, local) must NOT be used -- only true UTC.
        self.assertNotIn("14:00:00", content)

    @patch("geocaches.sync.gc_web.fieldnotes.fetch_all_drafts_web")
    def test_escapes_embedded_quotes(self, mock_fetch):
        row = dict(_DETAIL_RESPONSE)
        row["note"] = 'Cache said "hello"'
        mock_fetch.return_value = [row]

        path = web_fieldnotes.download_gc_fieldnotes_web()

        content = path.read_text(encoding="utf-8")
        self.assertIn('"Cache said ""hello"""', content)

    @patch("geocaches.sync.gc_web.fieldnotes.fetch_all_drafts_web")
    def test_unknown_log_type_id_falls_back_to_write_note(self, mock_fetch):
        row = dict(_DETAIL_RESPONSE)
        row["logTypeId"] = 999999
        mock_fetch.return_value = [row]

        path = web_fieldnotes.download_gc_fieldnotes_web()

        content = path.read_text(encoding="utf-8")
        self.assertIn(",Write note,", content)

    @patch("geocaches.sync.gc_web.fieldnotes.fetch_all_drafts_web", return_value=[])
    def test_no_drafts_raises(self, _mock_fetch):
        with self.assertRaises(ValueError):
            web_fieldnotes.download_gc_fieldnotes_web()


class TestFieldnoteResolver(TestCase):
    """geocaches.importers.fieldnote's api_preferred/web_preferred resolver."""

    def test_web_preferred_skips_api_entirely(self):
        from preferences.models import UserPreference
        from geocaches.importers import fieldnote as fn

        UserPreference.set("gc_access_mode", "web_preferred")

        with patch("geocaches.sync.gc_web.fieldnotes.download_gc_fieldnotes_web") as mock_web, \
                patch("geocaches.sync.gc_access.get_gc_client") as MockClient:
            mock_web.return_value = "sentinel-path"
            result = fn.download_gc_fieldnotes()

        self.assertEqual(result, "sentinel-path")
        MockClient.assert_not_called()
        mock_web.assert_called_once()

    def test_api_preferred_falls_back_to_web_on_exception(self):
        from geocaches.importers import fieldnote as fn

        with patch("geocaches.feature_flags.gc_api_available", return_value=True), \
                patch.object(fn, "_download_gc_fieldnotes_api", side_effect=RuntimeError("token expired")), \
                patch("geocaches.sync.gc_web.fieldnotes.download_gc_fieldnotes_web") as mock_web:
            mock_web.return_value = "sentinel-path"
            result = fn.download_gc_fieldnotes()

        self.assertEqual(result, "sentinel-path")
        mock_web.assert_called_once()

    def test_api_preferred_uses_api_when_it_succeeds(self):
        from geocaches.importers import fieldnote as fn

        with patch("geocaches.feature_flags.gc_api_available", return_value=True), \
                patch.object(fn, "_download_gc_fieldnotes_api", return_value="api-path") as mock_api, \
                patch("geocaches.sync.gc_web.fieldnotes.download_gc_fieldnotes_web") as mock_web:
            result = fn.download_gc_fieldnotes()

        self.assertEqual(result, "api-path")
        mock_api.assert_called_once()
        mock_web.assert_not_called()

    def test_api_preferred_uses_web_when_api_unavailable(self):
        from geocaches.importers import fieldnote as fn

        with patch("geocaches.feature_flags.gc_api_available", return_value=False), \
                patch("geocaches.sync.gc_web.fieldnotes.download_gc_fieldnotes_web") as mock_web:
            mock_web.return_value = "sentinel-path"
            result = fn.download_gc_fieldnotes()

        self.assertEqual(result, "sentinel-path")
        mock_web.assert_called_once()
