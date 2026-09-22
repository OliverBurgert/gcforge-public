"""
Tests for geocaches.sync.gc_web.log_fetch — the web-scraped alternative to
the official partner API's deep/paginated GC log history, pinned to the
live-verified /seek/geocache.logbook JSON shape (discovered 2026-08-14, see
docs/reference/geocaching-com-web.md §5/§10). No live network calls.
"""

from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase, TestCase

from geocaches.models import CacheSize, CacheStatus, CacheType, Geocache
from geocaches.sync.gc_web import log_fetch


def _cache(gc_code="GC9WVK7", **kw):
    defaults = dict(
        name="C", cache_type=CacheType.TRADITIONAL, size=CacheSize.MICRO,
        status=CacheStatus.ACTIVE, latitude=48.0, longitude=9.0,
        difficulty=2.0, terrain=2.0,
    )
    defaults.update(kw)
    return Geocache.objects.create(gc_code=gc_code, **defaults)


# A trimmed, real live-captured row shape (2026-08-14, GC9WVK7) — field names
# and value formats exactly as returned, text shortened.
_RAW_LOG = {
    "LogID": 1348975460,
    "CacheID": 8730508,
    "LogGuid": "fed6fa87-e5e4-4f5e-942e-f3370361b264",
    "LogTypeID": 46,
    "LogType": "Owner Maintenance",
    "LogText": "<p>Alles wieder frisch...<br />\nim unteren Teil war es matschig.</p>\n",
    "Created": "2026-03-14",
    "Visited": "2026-03-14",
    "UserName": "spazierenmitziel",
    "AccountID": 571226,
    "AccountGuid": "758a432c-70bf-4ea9-9075-64ce1c645314",
    "IsEncoded": False,
    "FavoritePointUsed": False,
    "Images": [],
}


def _page(rows, *, idx=1, size=50, total_rows=None, total_pages=None):
    total_rows = len(rows) if total_rows is None else total_rows
    total_pages = 1 if total_pages is None else total_pages
    return {
        "status": "success",
        "data": rows,
        "pageInfo": {"idx": idx, "rows": len(rows), "size": size,
                     "totalPages": total_pages, "totalRows": total_rows},
    }


class TestNormalizeEntries(SimpleTestCase):
    def test_maps_fields_to_the_shared_dedup_shape(self):
        [normalized] = log_fetch._normalize_entries([_RAW_LOG])

        self.assertEqual(normalized["log_type"], "Owner Maintenance")
        self.assertEqual(normalized["logged_date"], "2026-03-14")
        self.assertEqual(normalized["user_name"], "spazierenmitziel")
        self.assertEqual(normalized["user_id"], "758a432c-70bf-4ea9-9075-64ce1c645314")
        self.assertEqual(normalized["source_id"], "1348975460")
        self.assertEqual(normalized["source"], "gc")
        self.assertNotIn("<p>", normalized["text"])
        self.assertIn("Alles wieder frisch", normalized["text"])

    def test_skips_entries_without_a_visited_date(self):
        bad = dict(_RAW_LOG, Visited="")
        self.assertEqual(log_fetch._normalize_entries([bad]), [])

    def test_missing_log_type_falls_back_to_write_note(self):
        no_type = dict(_RAW_LOG, LogType="")
        [normalized] = log_fetch._normalize_entries([no_type])
        self.assertEqual(normalized["log_type"], "Write note")


class TestStripHtml(SimpleTestCase):
    def test_strips_tags_and_collapses_br(self):
        text = log_fetch._strip_html("<p>Line one<br />\nLine two</p>\n")
        self.assertNotIn("<", text)
        self.assertIn("Line one", text)
        self.assertIn("Line two", text)

    def test_empty_input(self):
        self.assertEqual(log_fetch._strip_html(""), "")


class TestResolveGuid(SimpleTestCase):
    def test_scrapes_the_guid_from_the_view_all_logs_link(self):
        session = MagicMock()
        session.get.return_value.text = (
            '<a href="/seek/geocache_logs.aspx?guid=f1aea01d-6414-4a7f-bcf0-073abd1a90a8">'
            "View all logs (57)</a>"
        )
        session.get.return_value.url = "https://www.geocaching.com/geocache/GC9WVK7"

        guid = log_fetch._resolve_guid(session, "GC9WVK7")

        self.assertEqual(guid, "f1aea01d-6414-4a7f-bcf0-073abd1a90a8")
        session.get.assert_called_once_with(
            "https://www.geocaching.com/geocache/GC9WVK7", timeout=20,
        )

    def test_raises_when_no_guid_link_is_present(self):
        session = MagicMock()
        session.get.return_value.text = "<html>no logs link here</html>"
        session.get.return_value.url = "https://www.geocaching.com/geocache/GC9WVK7"

        with self.assertRaises(RuntimeError):
            log_fetch._resolve_guid(session, "GC9WVK7")

    @patch("geocaches.sync.gc_web.log_fetch.reset_session")
    def test_resets_session_on_signin_redirect(self, mock_reset):
        session = MagicMock()
        session.get.return_value.text = ""
        session.get.return_value.url = "https://www.geocaching.com/account/signin?ReturnUrl=%2f"

        with self.assertRaises(RuntimeError):
            log_fetch._resolve_guid(session, "GC9WVK7")
        mock_reset.assert_called_once()


class TestResolveToken(SimpleTestCase):
    def test_scrapes_the_user_token(self):
        session = MagicMock()
        session.get.return_value.text = "var x = 1; userToken = 'abc123tokvalue'; more();"

        token = log_fetch._resolve_token(session, "f1aea01d-6414-4a7f-bcf0-073abd1a90a8")

        self.assertEqual(token, "abc123tokvalue")
        args, kwargs = session.get.call_args
        self.assertEqual(args[0], "https://www.geocaching.com/seek/geocache_logs.aspx")
        self.assertEqual(kwargs["params"], {"guid": "f1aea01d-6414-4a7f-bcf0-073abd1a90a8"})

    def test_raises_when_no_token_is_present(self):
        session = MagicMock()
        session.get.return_value.text = "<html>nothing here</html>"

        with self.assertRaises(RuntimeError):
            log_fetch._resolve_token(session, "some-guid")


class TestFetchLogbookPage(SimpleTestCase):
    @patch("geocaches.sync.gc_web.log_fetch.polite_delay")
    def test_sends_the_confirmed_query_params(self, _delay):
        session = MagicMock()
        session.get.return_value.json.return_value = _page([_RAW_LOG])

        data = log_fetch._fetch_logbook_page(session, "tok", idx=2, num=10, show_owner_only=True)

        self.assertEqual(data["status"], "success")
        args, kwargs = session.get.call_args
        self.assertEqual(args[0], "https://www.geocaching.com/seek/geocache.logbook")
        self.assertEqual(kwargs["params"], {
            "tkn": "tok", "idx": 2, "num": 10,
            "showOwnerOnly": "true", "decrypt": "true", "sp": "false", "sf": "false",
        })

    @patch("geocaches.sync.gc_web.log_fetch.polite_delay")
    def test_raises_on_non_success_status(self, _delay):
        session = MagicMock()
        session.get.return_value.json.return_value = {"status": "error"}

        with self.assertRaises(RuntimeError):
            log_fetch._fetch_logbook_page(session, "tok", idx=1, num=10)


class TestFetchRecentGcLogs(TestCase):
    def test_returns_zero_for_unknown_cache(self):
        saved, count = log_fetch.fetch_recent_gc_logs("GCNOPE", session=MagicMock())
        self.assertEqual((saved, count), (0, 0))

    @patch("geocaches.sync.gc_web.log_fetch._fetch_logbook_page")
    @patch("geocaches.sync.gc_web.log_fetch._resolve_batch", return_value="tok")
    def test_saves_new_logs_from_page_one(self, _resolve, mock_fetch):
        _cache()
        mock_fetch.return_value = _page([_RAW_LOG])

        saved, count = log_fetch.fetch_recent_gc_logs("GC9WVK7", count=50, session=MagicMock())

        self.assertEqual((saved, count), (1, 1))
        mock_fetch.assert_called_once()
        _, kwargs = mock_fetch.call_args
        self.assertEqual(kwargs, {"idx": 1, "num": 50})


class TestFetchMoreGcLogs(TestCase):
    @patch("geocaches.sync.gc_web.log_fetch._fetch_logbook_page")
    @patch("geocaches.sync.gc_web.log_fetch._resolve_batch", return_value="tok")
    def test_converts_skip_to_a_page_index(self, _resolve, mock_fetch):
        _cache()
        mock_fetch.return_value = _page([_RAW_LOG], idx=3)

        log_fetch.fetch_more_gc_logs("GC9WVK7", skip=100, count=50, session=MagicMock())

        _, kwargs = mock_fetch.call_args
        self.assertEqual(kwargs["idx"], 3)  # skip=100, count=50 -> page 3
        self.assertEqual(kwargs["num"], 50)


class TestFetchAllGcLogs(TestCase):
    @patch("geocaches.sync.gc_web.log_fetch._fetch_logbook_page")
    @patch("geocaches.sync.gc_web.log_fetch._resolve_batch", return_value="tok")
    def test_pages_until_total_pages_reached(self, _resolve, mock_fetch):
        _cache()
        row2 = dict(_RAW_LOG, LogID=999, LogGuid="93b45da6-0aa8-4a3b-a5a9-57073fbda397")
        mock_fetch.side_effect = [
            _page([_RAW_LOG], idx=1, total_pages=2, total_rows=2),
            _page([row2], idx=2, total_pages=2, total_rows=2),
        ]

        total = log_fetch.fetch_all_gc_logs("GC9WVK7", session=MagicMock())

        self.assertEqual(total, 2)
        self.assertEqual(mock_fetch.call_count, 2)

    @patch("geocaches.sync.gc_web.log_fetch._fetch_logbook_page")
    @patch("geocaches.sync.gc_web.log_fetch._resolve_batch", return_value="tok")
    def test_stops_on_an_empty_page(self, _resolve, mock_fetch):
        _cache()
        mock_fetch.return_value = _page([])

        total = log_fetch.fetch_all_gc_logs("GC9WVK7", session=MagicMock())

        self.assertEqual(total, 0)
        mock_fetch.assert_called_once()


class TestEnsureMyGcLogs(TestCase):
    @patch("geocaches.sync.gc_web.log_fetch._fetch_logbook_page")
    @patch("geocaches.sync.gc_web.log_fetch._resolve_batch", return_value="tok")
    def test_requests_show_owner_only(self, _resolve, mock_fetch):
        _cache()
        mock_fetch.return_value = _page([_RAW_LOG])

        saved = log_fetch.ensure_my_gc_logs("GC9WVK7", session=MagicMock())

        self.assertEqual(saved, 1)
        _, kwargs = mock_fetch.call_args
        self.assertEqual(kwargs["show_owner_only"], True)

    @patch("geocaches.sync.gc_web.log_fetch._fetch_logbook_page")
    @patch("geocaches.sync.gc_web.log_fetch._resolve_batch", return_value="tok")
    def test_no_logs_found_returns_zero(self, _resolve, mock_fetch):
        _cache()
        mock_fetch.return_value = _page([])

        saved = log_fetch.ensure_my_gc_logs("GC9WVK7", session=MagicMock())

        self.assertEqual(saved, 0)
