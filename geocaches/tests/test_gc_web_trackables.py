"""
Tests for the website-only GC trackable management path (no partner-API
token) — geocaches.sync.gc_web.trackables' resolve/detail/list/history
scrapers, its standalone-log tRPC calls, and the GCWebTrackableClient
adapter + geocaches.sync.gc_access.get_trackable_client() resolver.

Confirmed live 2026-08-16 via real create+delete round trips on Oliver's
own trackable (TB91ENV) — see gc_web/trackables.py's module docstring. No
live network calls in the test suite.
"""

import json
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase, TestCase

from geocaches.sync.gc_web import trackables as web_tb

# Real page title, 2026-08-16 live capture (leading whitespace as rendered).
_FOUND_TITLE_HTML = "<html><head><title>\n\t(TB91ENV) Community Volunteer Tag - 2018 Community Volunteer Tag - Found in San Diego</title></head><body></body></html>"
_NOT_FOUND_HTML = (
    "<html><head><title>Geocaching &gt; Trackable Items &gt; Trackable Item Details</title></head>"
    "<body>The Travel Bug you requested does not exist in the system.</body></html>"
)

_LOGGABLE = {
    "referenceCode": "TB91ENV",
    "iconUrl": "https://www.geocaching.com/images/wpttypes/10663.gif",
    "name": "2018 Community Volunteer Tag - Found in San Diego",
    "description": "<p>I found this one in a nice cache in San Diego</p>",
    "dateReleased": "2019-02-16T12:00:00",
    "distanceTraveledInKilometers": 80626.48,
    "trackableType": 10663,
    "owner": {"code": "PR11Z6J", "userName": "spazierenmitziel"},
    "holder": {"code": "PR11Z6J", "userName": "spazierenmitziel"},
    "isMissing": False, "isActive": True, "isLocked": False,
}


def _tb_log_page_html(loggable=None) -> str:
    payload = {"props": {"pageProps": {"tbCode": "TB91ENV", "loggable": loggable}}}
    return f'<html><head><script id="__NEXT_DATA__" type="application/json">{json.dumps(payload)}</script></head><body></body></html>'


def _log_view_page_html(**overrides) -> str:
    pp = {
        "csrfToken": "tok-abc123",
        "logReferenceCode": "TL276GPQV",
        "trackableReferenceCode": "TB91ENV",
        "dateTimeCreatedUtc": "2026-08-14T21:54:03.65Z",
        "logDate": "2026-08-14T12:00:00",
        "logType": {"id": 75},
        "logText": "Visited Wiesazschatz and logged using the cool new gcforge tool!",
        "username": "spazierenmitziel",
        "geocache": {"referenceCode": "GCBNTF9", "name": "Wiesazschatz"},
        "isArchived": False,
    }
    pp.update(overrides)
    payload = {"props": {"pageProps": pp}}
    return f'<html><head><script id="__NEXT_DATA__" type="application/json">{json.dumps(payload)}</script></head><body></body></html>'


# A trimmed real fragment (2026-08-16) matching /track/details.aspx's
# dl.BugDetailsList + "Current Goal"/"About This Item"/"Tracking Number"
# markup, for a trackable the account owns.
_CLASSIC_DETAIL_HTML = """
<html><body>
<dl class="BugDetailsList">
  <dt>Owner:</dt><dd><a href="/p/?guid=x">spazierenmitziel</a></dd>
  <dt>Released:</dt><dd>Saturday, 16 February 2019</dd>
  <dt>Origin:</dt><dd>Baden-Württemberg, Germany</dd>
  <dt>Recently Spotted:</dt><dd>In Siltcoos Trail</dd>
</dl>
<p>Tracking Number: SBJT2R</p>
<h2 class="details-subheader">Current Goal</h2>
<div>First of all, let's visit a desert.</div>
<h2 class="details-subheader">About This Item</h2>
<div>No additional details available.</div>
</body></html>
"""

_CLASSIC_DETAIL_NO_TRACKING_HTML = """
<html><body>
<dl class="BugDetailsList">
  <dt>Owner:</dt><dd><a href="/p/?guid=x">JuMaPa1</a></dd>
  <dt>Released:</dt><dd>Friday, 07 May 2021</dd>
  <dt>Origin:</dt><dd>Bayern, Germany</dd>
  <dt>Recently Spotted:</dt><dd>In the hands of penumbra59.</dd>
</dl>
<h2 class="details-subheader">Current Goal</h2>
<div>Ich möchte um die Welt reisen.</div>
<h2 class="details-subheader">About This Item</h2>
<div>Hallo ich bin Miraculix.</div>
</body></html>
"""

# A trimmed real fragment (2026-08-16) matching /track/gallery.aspx's markup.
_GALLERY_HTML = """
<table class="Table GalleryTable">
<tr>
  <td>
    <span class="date-stamp">2008-03-09</span>
    <a class="imageLink" href="https://img.geocaching.com/track/log/large/5e4b7c6e-0ec1-413b-87fa-c52ad888f1bc.jpg">
      <img alt="View Image" src="https://img.geocaching.com/track/log/thumb/5e4b7c6e-0ec1-413b-87fa-c52ad888f1bc.jpg"/>
    </a>
    <span class="image-description">Lily in San Luis Obispo Lily learned about Los Osos.</span>
  </td>
  <td>
    <span class="date-stamp">2006-06-26</span>
    <a class="imageLink" href="https://img.geocaching.com/track/large/d1fcb2a9-5eee-4e76-8c63-91ba7410fd20.jpg">
      <img alt="View Image" src="https://img.geocaching.com/track/thumb/d1fcb2a9-5eee-4e76-8c63-91ba7410fd20.jpg"/>
    </a>
    <span class="image-description">Lilly relaxing before the flight to Japan</span>
  </td>
</tr>
</table>
"""

# A trimmed real fragment (2026-08-17) matching /track/search.aspx?o=1's
# markup (found by Oliver -- lists every owned trackable regardless of
# current holder).
_OWNED_SEARCH_HTML = """
<table class="Table">
<tr><th></th><th>Name</th><th>Last Log</th><th>Owner</th><th>Location</th><th>Traveled</th></tr>
<tr>
  <td></td>
  <td><a href="https://www.geocaching.com/track/details.aspx?id=7944491">2018 Community Volunteer Tag - Found in San Diego</a></td>
  <td>2026-08-14</td>
  <td>spazierenmitziel</td>
  <td>spazierenm&hellip;</td>
  <td>80626 km</td>
</tr>
<tr>
  <td>NW 8319.6&nbsp;km</td>
  <td><a href="https://www.geocaching.com/track/details.aspx?id=498986">Lilly the little Lobster</a></td>
  <td>2015-01-09</td>
  <td>spazierenmitziel</td>
  <td>Ninja Hide&hellip;</td>
  <td>18152 km</td>
</tr>
</table>
"""

# A trimmed real fragment (2026-08-16) matching /my/inventory.aspx's markup.
_INVENTORY_HTML = """
<table>
<tr><th>Trackable Item Name</th><th>Owner</th><th>Last Log</th><th>Traveled</th><th>Action</th></tr>
<tr>
  <td><img/><a href="/track/details.aspx?guid=113e94ae-b885-4ac2-a3bc-fdf8681686ff">2018 Community Volunteer Tag - Found in San Diego</a></td>
  <td><a href="/profile/?guid=abc">spazierenmitziel</a></td>
  <td><a href="/track/log.aspx?LUID=1">14 Aug 2026</a></td>
  <td>80626.5km</td>
  <td><a href="/track/log.aspx?wid=1">Log</a></td>
</tr>
</table>
"""

# A trimmed real fragment matching the classic detail page's two-row-per-
# entry "Tracking History" table (date is in a <th>, not <td>).
_HISTORY_HTML = """
<table class="TrackableItemLogTable Table">
<tr><th>Date</th><th>Type</th><th>Location</th><th></th></tr>
<tr class="Data BorderTop">
  <th><img/>&nbsp;2026-08-14</th>
  <td><a href="/profile/?guid=x">spazierenmitziel</a> took it to <a href="/geocache/GCBNTF9">Wiesazschatz</a></td>
  <td>Baden-Württemberg, Germany\r\n                 - 195.02 meters&nbsp;</td>
  <td><a href="/live/log/TL276GPQV">Visit Log</a></td>
</tr>
<tr class="Data BorderBottom">
  <td colspan="4">Visited Wiesazschatz and logged using the cool new gcforge tool!</td>
</tr>
<tr class="Data BorderTop AlternatingRow">
  <th><img/>&nbsp;2026-08-14</th>
  <td><a href="/profile/?guid=x">spazierenmitziel</a> took it to <a href="/geocache/GCBOTHR">Haldenschatz</a></td>
  <td>Baden-Württemberg, Germany - 10.78 km&nbsp;</td>
  <td><a href="/live/log/TL276GPQW">Visit Log</a></td>
</tr>
<tr class="Data BorderBottom AlternatingRow">
  <td colspan="4">Visited Haldenschatz too!</td>
</tr>
</table>
"""


def _resp(text, url="https://www.geocaching.com/track/details.aspx"):
    m = MagicMock()
    m.text = text
    m.url = url
    return m


class TestResolveTrackableWeb(SimpleTestCase):
    def test_resolves_by_reference_code(self):
        session = MagicMock()
        session.get.return_value = _resp(_FOUND_TITLE_HTML)

        result = web_tb.resolve_trackable_web("TB91ENV", session=session)

        self.assertEqual(result["reference_code"], "TB91ENV")
        self.assertIn("Community Volunteer Tag", result["name"])
        kwargs = session.get.call_args[1]
        self.assertEqual(kwargs["params"], {"tracker": "TB91ENV"})

    def test_resolves_by_tracking_code_same_way(self):
        session = MagicMock()
        session.get.return_value = _resp(_FOUND_TITLE_HTML)

        result = web_tb.resolve_trackable_web("CVWPRJ", session=session)

        self.assertEqual(result["reference_code"], "TB91ENV")

    def test_resolves_by_guid(self):
        session = MagicMock()
        session.get.return_value = _resp(_FOUND_TITLE_HTML)

        result = web_tb.resolve_trackable_web(guid="113e94ae-b885-4ac2-a3bc-fdf8681686ff", session=session)

        self.assertEqual(result["reference_code"], "TB91ENV")
        kwargs = session.get.call_args[1]
        self.assertEqual(kwargs["params"], {"guid": "113e94ae-b885-4ac2-a3bc-fdf8681686ff"})

    def test_resolves_by_internal_id(self):
        session = MagicMock()
        session.get.return_value = _resp(_FOUND_TITLE_HTML)

        result = web_tb.resolve_trackable_web(internal_id="498986", session=session)

        self.assertEqual(result["reference_code"], "TB91ENV")
        kwargs = session.get.call_args[1]
        self.assertEqual(kwargs["params"], {"id": "498986"})

    def test_unknown_code_returns_none(self):
        session = MagicMock()
        session.get.return_value = _resp(_NOT_FOUND_HTML)

        result = web_tb.resolve_trackable_web("NOPE123456", session=session)

        self.assertIsNone(result)

    def test_neither_code_nor_guid_raises(self):
        with self.assertRaises(ValueError):
            web_tb.resolve_trackable_web()


class TestGetTrackableWeb(SimpleTestCase):
    def test_parses_loggable_detail(self):
        session = MagicMock()
        session.get.return_value = _resp(_tb_log_page_html(_LOGGABLE), url="https://www.geocaching.com/live/trackable/TB91ENV/log")

        detail = web_tb.get_trackable_web("TB91ENV", session=session)

        self.assertEqual(detail["referenceCode"], "TB91ENV")
        self.assertEqual(detail["name"], "2018 Community Volunteer Tag - Found in San Diego")
        self.assertTrue(detail["isActive"])

    def test_missing_loggable_raises(self):
        session = MagicMock()
        session.get.return_value = _resp(_tb_log_page_html(None))

        with self.assertRaises(RuntimeError):
            web_tb.get_trackable_web("TB91ENV", session=session)

    def test_404_redirect_raises(self):
        session = MagicMock()
        session.get.return_value = _resp("<html></html>", url="https://www.geocaching.com/error/404.aspx?statusCode=200")

        with self.assertRaises(RuntimeError):
            web_tb.get_trackable_web("TBNOPE", session=session)


class TestListHolderPages(SimpleTestCase):
    def test_list_inventory_parses_rows(self):
        session = MagicMock()
        session.get.return_value = _resp(_INVENTORY_HTML, url=web_tb._INVENTORY_URL)

        rows = web_tb.list_inventory_web(session=session)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["guid"], "113e94ae-b885-4ac2-a3bc-fdf8681686ff")
        self.assertEqual(rows[0]["name"], "2018 Community Volunteer Tag - Found in San Diego")
        self.assertEqual(rows[0]["owner"], "spazierenmitziel")
        self.assertEqual(rows[0]["distance_text"], "80626.5km")

    def test_list_collection_uses_the_collection_url(self):
        session = MagicMock()
        session.get.return_value = _resp(_INVENTORY_HTML, url=web_tb._COLLECTION_URL)

        web_tb.list_collection_web(session=session)

        args = session.get.call_args[0]
        self.assertEqual(args[0], web_tb._COLLECTION_URL)


class TestListOwnedWeb(TestCase):
    def test_uses_explicit_uid(self):
        session = MagicMock()
        session.get.return_value = _resp(_OWNED_SEARCH_HTML, url=web_tb._SEARCH_URL)

        rows = web_tb.list_owned_web(uid="758a432c-70bf-4ea9-9075-64ce1c645314", session=session)

        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["internal_id"], "7944491")
        self.assertEqual(rows[0]["name"], "2018 Community Volunteer Tag - Found in San Diego")
        self.assertEqual(rows[0]["owner"], "spazierenmitziel")
        self.assertEqual(rows[1]["internal_id"], "498986")
        kwargs = session.get.call_args[1]
        self.assertEqual(kwargs["params"], {"o": "1", "uid": "758a432c-70bf-4ea9-9075-64ce1c645314"})

    def test_falls_back_to_stored_preference(self):
        from preferences.models import UserPreference
        UserPreference.set("gc_public_guid", "758a432c-70bf-4ea9-9075-64ce1c645314")
        session = MagicMock()
        session.get.return_value = _resp(_OWNED_SEARCH_HTML, url=web_tb._SEARCH_URL)

        web_tb.list_owned_web(session=session)

        kwargs = session.get.call_args[1]
        self.assertEqual(kwargs["params"]["uid"], "758a432c-70bf-4ea9-9075-64ce1c645314")

    def test_no_uid_available_raises(self):
        session = MagicMock()
        with self.assertRaises(RuntimeError):
            web_tb.list_owned_web(session=session)
        session.get.assert_not_called()


class TestGetTrackableClassicDetailWeb(SimpleTestCase):
    def test_owned_item_shows_tracking_number(self):
        session = MagicMock()
        session.get.return_value = _resp(_CLASSIC_DETAIL_HTML)

        detail = web_tb.get_trackable_classic_detail_web("TBA08V7", session=session)

        self.assertEqual(detail["origin"], "Baden-Württemberg, Germany")
        self.assertEqual(detail["goal"], "First of all, let's visit a desert.")
        self.assertEqual(detail["about"], "No additional details available.")
        self.assertEqual(detail["tracking_code"], "SBJT2R")

    def test_not_owned_item_has_no_tracking_number(self):
        """Confirmed live: tracking number is owner-scoped, not holder-scoped
        -- shown for an owned-but-not-held item, absent for a not-owned one."""
        session = MagicMock()
        session.get.return_value = _resp(_CLASSIC_DETAIL_NO_TRACKING_HTML)

        detail = web_tb.get_trackable_classic_detail_web("TB9FHAG", session=session)

        self.assertEqual(detail["tracking_code"], "")
        self.assertEqual(detail["origin"], "Bayern, Germany")

    def test_not_found_raises(self):
        session = MagicMock()
        session.get.return_value = _resp(_NOT_FOUND_HTML)

        with self.assertRaises(RuntimeError):
            web_tb.get_trackable_classic_detail_web("TBNOPE", session=session)


class TestListTrackableImagesWeb(SimpleTestCase):
    @patch("geocaches.sync.gc_web.trackables.polite_delay")
    def test_parses_log_and_listing_images(self, _delay):
        session = MagicMock()
        detail_html = _CLASSIC_DETAIL_HTML.replace(
            "<p>Tracking Number: SBJT2R</p>",
            '<p>Tracking Number: SBJT2R</p><a href="gallery.aspx?ID=498986">View All 4 Gallery Images</a>',
        )
        session.get.side_effect = [_resp(detail_html), _resp(_GALLERY_HTML, url=web_tb._GALLERY_URL)]

        images = web_tb.list_trackable_images_web("TBZH18", session=session)

        self.assertEqual(len(images), 2)
        log_img, listing_img = images
        self.assertTrue(log_img["is_log_image"])
        self.assertEqual(log_img["guid"], "5e4b7c6e-0ec1-413b-87fa-c52ad888f1bc")
        self.assertEqual(log_img["date_text"], "2008-03-09")
        self.assertEqual(log_img["description"], "Lily in San Luis Obispo Lily learned about Los Osos.")
        self.assertFalse(listing_img["is_log_image"])

        second_call_kwargs = session.get.call_args_list[1][1]
        self.assertEqual(second_call_kwargs["params"], {"ID": "498986"})

    def test_no_gallery_link_returns_empty(self):
        session = MagicMock()
        session.get.return_value = _resp(_CLASSIC_DETAIL_HTML)

        images = web_tb.list_trackable_images_web("TBA08V7", session=session)

        self.assertEqual(images, [])

    def test_not_found_raises(self):
        session = MagicMock()
        session.get.return_value = _resp(_NOT_FOUND_HTML)

        with self.assertRaises(RuntimeError):
            web_tb.list_trackable_images_web("TBNOPE", session=session)


class TestGetTrackableLogsWeb(SimpleTestCase):
    def test_parses_two_row_entries(self):
        session = MagicMock()
        session.get.return_value = _resp(_HISTORY_HTML)

        rows = web_tb.get_trackable_logs_web("TB91ENV", session=session)

        self.assertEqual(len(rows), 2)
        first = rows[0]
        self.assertEqual(first["log_reference_code"], "TL276GPQV")
        self.assertEqual(first["date_text"], "2026-08-14")
        self.assertEqual(first["action_text"], "spazierenmitziel took it to Wiesazschatz")
        self.assertEqual(first["location_text"], "Baden-Württemberg, Germany - 195.02 meters")
        self.assertEqual(first["note_text"], "Visited Wiesazschatz and logged using the cool new gcforge tool!")
        self.assertEqual(rows[1]["log_reference_code"], "TL276GPQW")

    def test_page_param_only_sent_when_above_one(self):
        session = MagicMock()
        session.get.return_value = _resp(_HISTORY_HTML)

        web_tb.get_trackable_logs_web("TB91ENV", page=1, session=session)
        self.assertNotIn("page", session.get.call_args[1]["params"])

        web_tb.get_trackable_logs_web("TB91ENV", page=2, session=session)
        self.assertEqual(session.get.call_args[1]["params"]["page"], 2)

    def test_not_found_raises(self):
        session = MagicMock()
        session.get.return_value = _resp(_NOT_FOUND_HTML)

        with self.assertRaises(RuntimeError):
            web_tb.get_trackable_logs_web("TBNOPE", session=session)

    def test_no_history_table_returns_empty(self):
        session = MagicMock()
        session.get.return_value = _resp("<html><body>no table here</body></html>")

        rows = web_tb.get_trackable_logs_web("TB91ENV", session=session)
        self.assertEqual(rows, [])


class TestFetchTrackableLogDetailWeb(SimpleTestCase):
    @patch("geocaches.sync.gc_web.trackables.polite_delay")
    def test_parses_precise_detail(self, _delay):
        session = MagicMock()
        session.get.return_value = _resp(_log_view_page_html())

        detail = web_tb.fetch_trackable_log_detail_web("TL276GPQV", session=session)

        self.assertEqual(detail["log_type_id"], 75)
        self.assertEqual(detail["trackable_reference_code"], "TB91ENV")
        self.assertEqual(detail["geocache"]["referenceCode"], "GCBNTF9")


class TestSubmitAndDeleteTrackableLogWeb(SimpleTestCase):
    @patch("geocaches.sync.gc_web.trpc.polite_delay")
    def test_submit_with_geocache_code_sources_csrf_from_its_log_page(self, _delay):
        session = MagicMock()
        session.get.return_value = _resp('{"csrfToken":"tok-xyz"}', url="https://www.geocaching.com/live/geocache/GCBPNTE/log")
        session.post.return_value.json.return_value = [{"result": {"data": {
            "logReferenceCode": "TL27723PN", "trackableReferenceCode": "TB91ENV",
            "logType": {"id": 4}, "logText": "hi", "statusCode": 200,
        }}}]

        result = web_tb.submit_trackable_log_web(
            "TB91ENV", "Write note", "2026-08-16", "hi",
            geocache_code="GCBPNTE", session=session,
        )

        self.assertEqual(result["logReferenceCode"], "TL27723PN")
        session.get.assert_called_once_with(
            "https://www.geocaching.com/live/geocache/GCBPNTE/log", timeout=20,
        )
        _, kwargs = session.post.call_args
        self.assertEqual(kwargs["json"], {
            "0": {
                "referenceCode": "TB91ENV",
                "body": {
                    "images": [],
                    "logDate": "2026-08-16T12:00:00.000Z",
                    "logText": "hi",
                    "logType": 4,
                    "trackables": [],
                    "trackingCode": "",
                    "geocacheReferenceCode": "GCBPNTE",
                },
            }
        })
        self.assertEqual(kwargs["headers"]["csrf-token"], "tok-xyz")

    @patch("geocaches.sync.gc_web.trackables.polite_delay")
    @patch("geocaches.sync.gc_web.trpc.polite_delay")
    def test_submit_without_geocache_code_borrows_csrf_from_prior_log(self, _delay1, _delay2):
        session = MagicMock()
        # First GET: the classic history page (to find a prior log ref).
        # Second GET: that log's own view page (CSRF source).
        session.get.side_effect = [
            _resp(_HISTORY_HTML),
            _resp(_log_view_page_html()),
        ]
        session.post.return_value.json.return_value = [{"result": {"data": {"logReferenceCode": "TL9"}}}]

        web_tb.submit_trackable_log_web("TB91ENV", "Write note", "2026-08-16", "hi", session=session)

        second_call_url = session.get.call_args_list[1][0][0]
        self.assertEqual(second_call_url, "https://www.geocaching.com/live/log/TL276GPQV")

    def test_submit_without_geocache_code_or_history_raises(self):
        session = MagicMock()
        session.get.return_value = _resp("<html><body>no table here</body></html>")

        with self.assertRaises(RuntimeError):
            web_tb.submit_trackable_log_web("TB91ENV", "Write note", "2026-08-16", "hi", session=session)

    def test_unknown_log_type_raises(self):
        session = MagicMock()
        with self.assertRaises(ValueError):
            web_tb.submit_trackable_log_web("TB91ENV", "Not A Real Type", "2026-08-16", "hi", session=session)

    @patch("geocaches.sync.gc_web.trpc.polite_delay")
    def test_delete_sources_csrf_from_log_view_page(self, _delay):
        session = MagicMock()
        session.get.return_value = _resp(_log_view_page_html())
        session.post.return_value.json.return_value = [{"result": {}}]

        web_tb.delete_trackable_log_web("TL276GPQV", session=session)

        session.get.assert_called_once_with("https://www.geocaching.com/live/log/TL276GPQV", timeout=20)
        args, kwargs = session.post.call_args
        self.assertEqual(kwargs["json"], {"0": {"referenceCode": "TL276GPQV", "reasonText": ""}})


class TestGCWebTrackableClient(SimpleTestCase):
    def test_verify_tracking_code_matches_official_shape(self):
        client = web_tb.GCWebTrackableClient()
        with patch.object(web_tb, "resolve_trackable_web", return_value={"reference_code": "TB91ENV", "name": "Test TB"}), \
                patch.object(web_tb, "get_trackable_web", return_value=dict(_LOGGABLE)):
            result = client.verify_tracking_code("CVWPRJ")

        self.assertEqual(result["reference_code"], "TB91ENV")
        self.assertEqual(result["current_geocache_code"], "")
        self.assertEqual(result["holder"]["username"], "spazierenmitziel")
        self.assertEqual(result["holder"]["reference_code"], "PR11Z6J")

    def test_verify_tracking_code_no_holder(self):
        client = web_tb.GCWebTrackableClient()
        loggable = dict(_LOGGABLE)
        loggable["holder"] = {}
        with patch.object(web_tb, "resolve_trackable_web", return_value={"reference_code": "TB91ENV", "name": "Test TB"}), \
                patch.object(web_tb, "get_trackable_web", return_value=loggable):
            result = client.verify_tracking_code("CVWPRJ")

        self.assertIsNone(result["holder"])

    def test_verify_unknown_code_raises(self):
        client = web_tb.GCWebTrackableClient()
        with patch.object(web_tb, "resolve_trackable_web", return_value=None):
            with self.assertRaises(ValueError):
                client.verify_tracking_code("NOPE")

    def test_submit_trackable_log_aliases_reference_code(self):
        client = web_tb.GCWebTrackableClient()
        with patch.object(web_tb, "submit_trackable_log_web", return_value={"logReferenceCode": "TL123"}) as mock_submit:
            result = client.submit_trackable_log("TB91ENV", "Write note", "2026-08-16", "hi", geocache_code="GCBPNTE")

        self.assertEqual(result, {"referenceCode": "TL123"})
        mock_submit.assert_called_once_with(
            "TB91ENV", "Write note", "2026-08-16", "hi",
            geocache_code="GCBPNTE", tracking_code=None,
        )


class TestGetTrackableClientResolver(TestCase):
    """geocaches.sync.gc_access.get_trackable_client()'s api_preferred/web_preferred resolver."""

    def test_web_preferred_returns_web_client(self):
        from preferences.models import UserPreference
        from geocaches.sync import gc_access

        UserPreference.set("gc_access_mode", "web_preferred")

        client = gc_access.get_trackable_client()

        self.assertIsInstance(client, web_tb.GCWebTrackableClient)

    def test_api_preferred_returns_official_client_when_available(self):
        from geocaches.sync import gc_access

        official = object()
        with patch("geocaches.feature_flags.gc_api_available", return_value=True), \
                patch.object(gc_access, "get_trackable_api_client", return_value=official):
            client = gc_access.get_trackable_client()

        self.assertIs(client, official)

    def test_api_preferred_falls_back_to_web_when_unavailable(self):
        from geocaches.sync import gc_access

        with patch("geocaches.feature_flags.gc_api_available", return_value=False):
            client = gc_access.get_trackable_client()

        self.assertIsInstance(client, web_tb.GCWebTrackableClient)
