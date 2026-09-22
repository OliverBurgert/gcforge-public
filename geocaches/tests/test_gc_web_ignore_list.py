"""
Tests for the website-only GC ignore-list path (no partner-API token) —
geocaches.sync.gc_web.ignore_list's list/add/remove scrapers and
geocaches.services.ignore_list's api_preferred/web_preferred resolver.

Confirmed live 2026-08-16 via a real add-then-remove round trip on Oliver's
own account (GCBPNTE) — see gc_web/ignore_list.py's module docstring. No
live network calls in the test suite.
"""

import json
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase, TestCase

from geocaches.sync.gc_web import ignore_list as web_ignore

# A trimmed real fragment (2026-08-16) matching /geocache/{code}'s markup —
# the "Ignore" sidebar link, HTML-entity-escaped like the real page.
_CACHE_PAGE_HTML = """
<div class="CacheDetailNavigationWidget">
  <a href="/bookmarks/ignore.aspx?guid=9ca58656-240e-4dd8-a135-a88cb4816d67&amp;WptTypeID=2">Ignore</a>
</div>
"""

_FORM_FIELDS = """
<form id="aspnetForm" method="post">
  <input type="hidden" name="__EVENTTARGET" value="" />
  <input type="hidden" name="__EVENTARGUMENT" value="" />
  <input type="hidden" name="__VIEWSTATE" value="VSTATE123" />
  <input type="hidden" name="__VIEWSTATEGENERATOR" value="GEN456" />
  <input type="hidden" name="returnUrl" value="/geocache/GCBPNTE" />
  <input type="hidden" name="__RequestVerificationToken" value="TOKEN789" />
  <input type="submit" name="ctl00$ContentBody$btnYes" value="{yes_value}" />
  <input type="submit" name="ctl00$ContentBody$btnNo" value="{no_value}" />
</form>
"""

# Real confirmation text, 2026-08-16 live capture.
_ADD_CONFIRM_HTML = "<h2>Ignore Listing</h2><p>Are you sure you want to add Bahnschatz #3 to your ignore list?</p>" + _FORM_FIELDS.format(
    yes_value="Yes. Ignore it.", no_value="No. Don't ignore it.",
)
_REMOVE_CONFIRM_HTML = "<h2>Ignore Listing</h2><p>The listing Bahnschatz #3 already exists on your ignore list. Do you want to remove it?</p>" + _FORM_FIELDS.format(
    yes_value="Yes. I want to restore it.", no_value="No. Keep ignoring it.",
)
_ADD_SUCCESS_HTML = "<h2>Ignore Listing</h2><p>You have successfully added Bahnschatz #3 to your ignore list.</p>"
_REMOVE_SUCCESS_HTML = "<h2>Ignore Listing</h2><p>Removed from the ignore list. Go Back.</p>"


def _next_data(rows: list[dict], *, total: int | None = None) -> str:
    payload = {
        "props": {
            "pageProps": {
                "bmCode": "ignored",
                "isIgnored": True,
                "skip": 0,
                "take": 500,
                "list": {"referenceCode": "ILW5Y1", "type": {"code": "il"}},
                "geocaches": {"total": total if total is not None else len(rows), "data": rows},
            }
        }
    }
    return f'<script id="__NEXT_DATA__" type="application/json">{json.dumps(payload)}</script>'


_ROW_ACTIVE = {
    "referenceCode": "GCBPNTE", "name": "Bahnschatz #3", "owner": "vroni1975",
    "state": {"isArchived": False, "isAvailable": True, "isPublished": True},
}
_ROW_DISABLED = {
    "referenceCode": "GCXXXXX", "name": "Some Disabled Cache", "owner": "someone",
    "state": {"isArchived": False, "isAvailable": False, "isPublished": True},
}
_ROW_ARCHIVED = {
    "referenceCode": "GCYYYYY", "name": "Some Archived Cache", "owner": "someone",
    "state": {"isArchived": True, "isAvailable": False, "isPublished": True},
}


class TestListIgnoredGcWeb(SimpleTestCase):
    def test_parses_rows_and_derives_status(self):
        session = MagicMock()
        session.get.return_value.text = _next_data([_ROW_ACTIVE, _ROW_DISABLED, _ROW_ARCHIVED])
        session.get.return_value.url = web_ignore._IGNORED_LIST_URL

        rows = web_ignore.list_ignored_gc_web(session=session)

        self.assertEqual(len(rows), 3)
        by_code = {r["referenceCode"]: r for r in rows}
        self.assertEqual(by_code["GCBPNTE"]["name"], "Bahnschatz #3")
        self.assertEqual(by_code["GCBPNTE"]["status"], "Active")
        self.assertEqual(by_code["GCXXXXX"]["status"], "Disabled")
        self.assertEqual(by_code["GCYYYYY"]["status"], "Archived")

    @patch("geocaches.sync.gc_web.ignore_list.polite_delay")
    def test_paginates_when_total_exceeds_one_page(self, _delay):
        session = MagicMock()
        page1 = _next_data([_ROW_ACTIVE], total=2)
        page2 = _next_data([_ROW_DISABLED], total=2)
        session.get.side_effect = [
            MagicMock(text=page1, url=web_ignore._IGNORED_LIST_URL),
            MagicMock(text=page2, url=web_ignore._IGNORED_LIST_URL),
        ]

        rows = web_ignore.list_ignored_gc_web(session=session)

        self.assertEqual(len(rows), 2)
        self.assertEqual(session.get.call_count, 2)
        second_call_kwargs = session.get.call_args_list[1][1]
        self.assertEqual(second_call_kwargs["params"]["skip"], 1)

    def test_missing_next_data_raises(self):
        session = MagicMock()
        session.get.return_value.text = "<html><body>nope</body></html>"
        session.get.return_value.url = web_ignore._IGNORED_LIST_URL

        with self.assertRaises(RuntimeError):
            web_ignore.list_ignored_gc_web(session=session)

    @patch("geocaches.sync.gc_web.ignore_list.reset_session")
    def test_resets_session_on_signin_redirect(self, mock_reset):
        session = MagicMock()
        session.get.return_value.text = ""
        session.get.return_value.url = "https://www.geocaching.com/account/signin?ReturnUrl=%2f"

        with self.assertRaises(RuntimeError):
            web_ignore.list_ignored_gc_web(session=session)
        mock_reset.assert_called_once()


class TestResolveIgnoreLink(SimpleTestCase):
    def test_extracts_guid_and_wpt_type_id(self):
        session = MagicMock()
        session.get.return_value.text = _CACHE_PAGE_HTML
        session.get.return_value.url = "https://www.geocaching.com/geocache/GCBPNTE"

        guid, wpt_type_id = web_ignore._resolve_ignore_link(session, "GCBPNTE")

        self.assertEqual(guid, "9ca58656-240e-4dd8-a135-a88cb4816d67")
        self.assertEqual(wpt_type_id, "2")

    def test_missing_link_raises(self):
        session = MagicMock()
        session.get.return_value.text = "<html><body>no ignore link here</body></html>"
        session.get.return_value.url = "https://www.geocaching.com/geocache/GCBPNTE"

        with self.assertRaises(RuntimeError):
            web_ignore._resolve_ignore_link(session, "GCBPNTE")


class TestAddRemoveGcWeb(SimpleTestCase):
    @patch("geocaches.sync.gc_web.ignore_list.polite_delay")
    def test_add_confirms_and_posts(self, _delay):
        session = MagicMock()
        get_mock = MagicMock(text=_ADD_CONFIRM_HTML, url=web_ignore._IGNORE_TOGGLE_URL)
        post_mock = MagicMock(text=_ADD_SUCCESS_HTML, url=web_ignore._IGNORE_TOGGLE_URL)
        # First session.get resolves the cache page, second GETs the confirm page.
        session.get.side_effect = [MagicMock(text=_CACHE_PAGE_HTML, url="x"), get_mock]
        session.post.return_value = post_mock

        result = web_ignore.add_gc_web("GCBPNTE", session=session)

        self.assertTrue(result)
        post_kwargs = session.post.call_args[1]
        self.assertEqual(post_kwargs["data"]["ctl00$ContentBody$btnYes"], "Yes. Ignore it.")
        self.assertNotIn("ctl00$ContentBody$btnNo", post_kwargs["data"])
        self.assertEqual(post_kwargs["data"]["__VIEWSTATE"], "VSTATE123")

    @patch("geocaches.sync.gc_web.ignore_list.polite_delay")
    def test_remove_confirms_and_posts(self, _delay):
        session = MagicMock()
        get_mock = MagicMock(text=_REMOVE_CONFIRM_HTML, url=web_ignore._IGNORE_TOGGLE_URL)
        post_mock = MagicMock(text=_REMOVE_SUCCESS_HTML, url=web_ignore._IGNORE_TOGGLE_URL)
        session.get.side_effect = [MagicMock(text=_CACHE_PAGE_HTML, url="x"), get_mock]
        session.post.return_value = post_mock

        result = web_ignore.remove_gc_web("GCBPNTE", session=session)

        self.assertTrue(result)
        post_kwargs = session.post.call_args[1]
        self.assertEqual(post_kwargs["data"]["ctl00$ContentBody$btnYes"], "Yes. I want to restore it.")

    def test_add_when_already_ignored_is_idempotent_noop(self):
        # Page shows the REMOVE confirmation (already ignored) but caller
        # asked to add -> must not POST the remove confirmation by mistake.
        session = MagicMock()
        get_mock = MagicMock(text=_REMOVE_CONFIRM_HTML, url=web_ignore._IGNORE_TOGGLE_URL)
        session.get.side_effect = [MagicMock(text=_CACHE_PAGE_HTML, url="x"), get_mock]

        result = web_ignore.add_gc_web("GCBPNTE", session=session)

        self.assertFalse(result)
        session.post.assert_not_called()

    def test_remove_when_not_ignored_is_idempotent_noop(self):
        session = MagicMock()
        get_mock = MagicMock(text=_ADD_CONFIRM_HTML, url=web_ignore._IGNORE_TOGGLE_URL)
        session.get.side_effect = [MagicMock(text=_CACHE_PAGE_HTML, url="x"), get_mock]

        result = web_ignore.remove_gc_web("GCBPNTE", session=session)

        self.assertFalse(result)
        session.post.assert_not_called()

    def test_unrecognized_confirmation_page_raises(self):
        session = MagicMock()
        get_mock = MagicMock(text="<html>something unexpected</html>", url=web_ignore._IGNORE_TOGGLE_URL)
        session.get.side_effect = [MagicMock(text=_CACHE_PAGE_HTML, url="x"), get_mock]

        with self.assertRaises(RuntimeError):
            web_ignore.add_gc_web("GCBPNTE", session=session)

    @patch("geocaches.sync.gc_web.ignore_list.polite_delay")
    def test_post_without_success_text_raises(self, _delay):
        session = MagicMock()
        get_mock = MagicMock(text=_ADD_CONFIRM_HTML, url=web_ignore._IGNORE_TOGGLE_URL)
        post_mock = MagicMock(text="<html>unexpected response</html>", url=web_ignore._IGNORE_TOGGLE_URL)
        session.get.side_effect = [MagicMock(text=_CACHE_PAGE_HTML, url="x"), get_mock]
        session.post.return_value = post_mock

        with self.assertRaises(RuntimeError):
            web_ignore.add_gc_web("GCBPNTE", session=session)


class TestIgnoreListResolver(TestCase):
    """geocaches.services.ignore_list's api_preferred/web_preferred resolver."""

    def test_web_preferred_sync_skips_api_entirely(self):
        from preferences.models import UserPreference
        from geocaches.services import ignore_list as svc
        from geocaches.models import IgnoreListEntry, IgnoreSource

        UserPreference.set("gc_access_mode", "web_preferred")

        with patch("geocaches.sync.gc_web.ignore_list.list_ignored_gc_web") as mock_web, \
                patch("geocaches.sync.gc_access.get_gc_client") as MockClient:
            mock_web.return_value = [{"referenceCode": "GCBPNTE", "name": "Bahnschatz #3", "status": "Active"}]
            count = svc.sync_gc_ignore_list()

        self.assertEqual(count, 1)
        MockClient.assert_not_called()
        self.assertTrue(IgnoreListEntry.objects.filter(source=IgnoreSource.GC, code="GCBPNTE").exists())

    def test_api_preferred_falls_back_to_web_on_exception(self):
        from geocaches.services import ignore_list as svc

        with patch("geocaches.feature_flags.gc_api_available", return_value=True), \
                patch("geocaches.sync.gc_access.get_gc_client") as MockClient, \
                patch("geocaches.sync.gc_web.ignore_list.list_ignored_gc_web") as mock_web:
            MockClient.return_value.get_ignore_list.side_effect = RuntimeError("token expired")
            mock_web.return_value = [{"referenceCode": "GCBPNTE", "name": "", "status": "Active"}]
            count = svc.sync_gc_ignore_list()

        self.assertEqual(count, 1)
        mock_web.assert_called_once()

    def test_add_gc_web_preferred_calls_web_only(self):
        from preferences.models import UserPreference
        from geocaches.services import ignore_list as svc

        UserPreference.set("gc_access_mode", "web_preferred")

        with patch("geocaches.sync.gc_web.ignore_list.add_gc_web") as mock_web, \
                patch("geocaches.sync.gc_access.get_gc_client") as MockClient:
            entry = svc.add_gc("GCBPNTE")

        mock_web.assert_called_once_with("GCBPNTE")
        MockClient.assert_not_called()
        self.assertEqual(entry.code, "GCBPNTE")

    def test_remove_gc_web_preferred_calls_web_only(self):
        from preferences.models import UserPreference
        from geocaches.services import ignore_list as svc
        from geocaches.services.ignore_list import upsert_remote
        from geocaches.models import IgnoreSource

        UserPreference.set("gc_access_mode", "web_preferred")
        upsert_remote(IgnoreSource.GC, "GCBPNTE")

        with patch("geocaches.sync.gc_web.ignore_list.remove_gc_web") as mock_web, \
                patch("geocaches.sync.gc_access.get_gc_client") as MockClient:
            result = svc.remove_gc("GCBPNTE")

        mock_web.assert_called_once_with("GCBPNTE")
        MockClient.assert_not_called()
        self.assertTrue(result)
