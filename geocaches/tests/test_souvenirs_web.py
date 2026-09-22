"""
Tests for the website-only souvenir refresh path (no partner-API token) —
geocaches.sync.gc_web.souvenirs' list/detail scrapers and
geocaches.services.souvenirs' api_preferred/web_preferred resolver.

Confirmed live 2026-08-15 via Oliver's own HAR capture of a real account
(252 souvenirs) — see gc_web/souvenirs.py's module docstring. No live
network calls in the test suite.
"""

from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase, TestCase

from geocaches.sync.gc_web import souvenirs as web_souvenirs

# A trimmed real fragment (2026-08-15) matching /my/souvenirs.aspx's markup.
_LIST_HTML = """
<ul id="souvenirsList" class="souvenir-gallery-list">
  <li id="souvenir_4876" data-earned="02/17/2026 07:07:35" data-name="10 Collections Completed" data-area="main">
    <span class="souvenir-content-container">
      <a href='/souvenir/?id=4876' title='10 Collections Completed'>
        <img class="SouvenirThumb" src="https://s3.amazonaws.com/gs-geo-images/be109058-63e8-450e-b06f-908d077a9079.png" />
      </a>
      <a href="/souvenir/?id=4876">10 Collections Completed</a>
      Acquired on 2026-02-17<br/>
    </span>
  </li>
  <li id="souvenir_155" data-earned="10/10/2010 20:12:00" data-name="10/10/10" data-area="main">
    <span class="souvenir-content-container">
      <a href='/souvenir/?id=155' title='10/10/10'>
        <img class="SouvenirThumb" src="https://img.geocaching.com/c63a0738-8e5b-491c-b9f6-c3f32bc1f595.png" />
      </a>
      <a href="/souvenir/?id=155">10/10/10</a>
      Acquired on 2010-10-10<br/>
    </span>
  </li>
</ul>
"""

# A trimmed real fragment matching /souvenir/?id=... 's markup.
_DETAIL_HTML = """
<div id="SouvenirDetails">
  <h2 class="span-20 last">
    <img id="ctl00_ContentBody_SouvenirDisplayControl1_uxThumbImage" class="SouvenirThumb"
         src="https://s3.amazonaws.com/gs-geo-images/369985e7-33cb-4422-b671-c0ef7ef8a215.png" />
    <span id="ctl00_ContentBody_SouvenirDisplayControl1_uxTitle">International Geocaching Day 2026 finder</span>
  </h2>
  <div class="span-7">
    <div id="ctl00_ContentBody_SouvenirDisplayControl1_uxInformationWrapper" class="SouvenirInfo">
      <strong>Additional Information:</strong>
      <span id="ctl00_ContentBody_SouvenirDisplayControl1_uxInformation">Congratulations! You earned this souvenir.</span>
    </div>
  </div>
  <div class="span-13 last">
    <div class="InformationWidget AlignCenter">
      <img id="ctl00_ContentBody_SouvenirDisplayControl1_uxLargeImage" class="img-souvenir-full-size"
           src="https://s3.amazonaws.com/gs-geo-images/edf8dc24-5788-407f-8bda-0f38a9a2c0d9.png" />
    </div>
  </div>
</div>
"""


class TestListSouvenirsWeb(SimpleTestCase):
    def test_parses_all_fields(self):
        session = MagicMock()
        session.get.return_value.text = _LIST_HTML
        session.get.return_value.url = web_souvenirs._LIST_URL

        rows = web_souvenirs.list_souvenirs_web(session=session)

        self.assertEqual(len(rows), 2)
        first = rows[0]
        self.assertEqual(first["id"], 4876)
        self.assertEqual(first["title"], "10 Collections Completed")
        self.assertTrue(first["imagePath"].endswith("be109058-63e8-450e-b06f-908d077a9079.png"))
        self.assertEqual(first["foundDateUtc"], "2026-02-17T07:07:35+00:00")
        self.assertEqual(first["url"], "https://www.geocaching.com/souvenir/?id=4876")

    def test_parses_non_padded_12h_am_pm_format(self):
        # Live-confirmed 2026-08-15: the same souvenir/account returned this
        # format on a live fetch, differing from an earlier HAR's zero-padded
        # 24h format for the exact same data -- ASP.NET's culture-dependent
        # DateTime.ToString(), not a fixed format. Both must parse.
        html = _LIST_HTML.replace('data-earned="02/17/2026 07:07:35"', 'data-earned="2/17/2026 7:07:35 AM"')
        session = MagicMock()
        session.get.return_value.text = html
        session.get.return_value.url = web_souvenirs._LIST_URL

        rows = web_souvenirs.list_souvenirs_web(session=session)

        self.assertEqual(rows[0]["foundDateUtc"], "2026-02-17T07:07:35+00:00")

    def test_unparseable_earned_date_does_not_crash(self):
        html = _LIST_HTML.replace('data-earned="02/17/2026 07:07:35"', 'data-earned="not a date"')
        session = MagicMock()
        session.get.return_value.text = html
        session.get.return_value.url = web_souvenirs._LIST_URL

        rows = web_souvenirs.list_souvenirs_web(session=session)

        self.assertIsNone(rows[0]["foundDateUtc"])

    def test_no_pagination_all_items_in_one_call(self):
        session = MagicMock()
        session.get.return_value.text = _LIST_HTML
        session.get.return_value.url = web_souvenirs._LIST_URL

        rows = web_souvenirs.list_souvenirs_web(session=session)

        self.assertEqual(len(rows), 2)
        session.get.assert_called_once()

    @patch("geocaches.sync.gc_web.souvenirs.reset_session")
    def test_resets_session_on_signin_redirect(self, mock_reset):
        session = MagicMock()
        session.get.return_value.text = ""
        session.get.return_value.url = "https://www.geocaching.com/account/signin?ReturnUrl=%2f"

        with self.assertRaises(RuntimeError):
            web_souvenirs.list_souvenirs_web(session=session)
        mock_reset.assert_called_once()


class TestFetchSouvenirDetailWeb(SimpleTestCase):
    @patch("geocaches.sync.gc_web.souvenirs.polite_delay")
    def test_parses_description_and_images(self, _delay):
        session = MagicMock()
        session.get.return_value.text = _DETAIL_HTML
        session.get.return_value.url = web_souvenirs._DETAIL_URL

        result = web_souvenirs.fetch_souvenir_detail_web(4960, session=session)

        self.assertEqual(result["description"], "Congratulations! You earned this souvenir.")
        self.assertTrue(result["imagePath"].endswith("edf8dc24-5788-407f-8bda-0f38a9a2c0d9.png"))
        self.assertTrue(result["thumbImagePath"].endswith("369985e7-33cb-4422-b671-c0ef7ef8a215.png"))
        args, kwargs = session.get.call_args
        self.assertEqual(kwargs["params"], {"id": 4960})

    @patch("geocaches.sync.gc_web.souvenirs.polite_delay")
    def test_missing_elements_return_empty_strings(self, _delay):
        session = MagicMock()
        session.get.return_value.text = "<html><body>nothing here</body></html>"
        session.get.return_value.url = web_souvenirs._DETAIL_URL

        result = web_souvenirs.fetch_souvenir_detail_web(1, session=session)

        self.assertEqual(result, {"description": "", "imagePath": "", "thumbImagePath": ""})


class TestRefreshWebResolver(TestCase):
    """geocaches.services.souvenirs' api_preferred/web_preferred resolver."""

    @patch("geocaches.sync.gc_web.souvenirs.fetch_souvenir_detail_web")
    @patch("geocaches.sync.gc_web.souvenirs.list_souvenirs_web")
    def test_web_refresh_fetches_detail_only_for_new_souvenirs(self, mock_list, mock_detail):
        from geocaches.models import Souvenir
        from geocaches.services import souvenirs as svc

        Souvenir.objects.create(gc_id=155, title="10/10/10")
        mock_list.return_value = [
            {"id": 155, "title": "10/10/10", "imagePath": "x", "foundDateUtc": None, "url": "u"},
            {"id": 4876, "title": "10 Collections Completed", "imagePath": "y", "foundDateUtc": None, "url": "u2"},
        ]
        mock_detail.return_value = {"description": "desc", "imagePath": "big", "thumbImagePath": "thumb"}

        summary = svc._refresh_web()

        self.assertEqual(summary, {"added": 1, "updated": 1, "total": 2})
        mock_detail.assert_called_once_with(4876)
        new_souvenir = Souvenir.objects.get(gc_id=4876)
        self.assertEqual(new_souvenir.description, "desc")
        # the already-existing souvenir was left completely untouched
        existing = Souvenir.objects.get(gc_id=155)
        self.assertEqual(existing.description, "")

    @patch("geocaches.services.souvenirs._refresh_web", return_value={"added": 1, "updated": 0, "total": 1})
    def test_web_preferred_skips_api_entirely(self, mock_web):
        from preferences.models import UserPreference
        from geocaches.services import souvenirs as svc

        UserPreference.set("gc_access_mode", "web_preferred")

        with patch("geocaches.sync.gc_access.get_gc_client") as MockClient:
            result = svc.refresh_all()

        self.assertEqual(result, {"added": 1, "updated": 0, "total": 1})
        MockClient.assert_not_called()
        mock_web.assert_called_once()

    @patch("geocaches.services.souvenirs._refresh_web", return_value={"added": 2, "updated": 0, "total": 2})
    @patch("geocaches.feature_flags.gc_api_available", return_value=True)
    def test_api_preferred_falls_back_to_web_on_exception(self, _avail, mock_web):
        from geocaches.services import souvenirs as svc

        with patch.object(svc, "_refresh_all_api", side_effect=RuntimeError("token expired")):
            result = svc.refresh_all()

        self.assertEqual(result, {"added": 2, "updated": 0, "total": 2})
        mock_web.assert_called_once()

    @patch("geocaches.services.souvenirs._refresh_web", return_value={"added": 0, "updated": 0, "total": 0})
    @patch("geocaches.feature_flags.gc_api_available", return_value=True)
    def test_api_preferred_uses_api_when_it_succeeds(self, _avail, mock_web):
        from geocaches.services import souvenirs as svc

        with patch.object(svc, "_refresh_all_api", return_value={"added": 5, "updated": 1, "total": 6}) as mock_api:
            result = svc.refresh_all()

        self.assertEqual(result, {"added": 5, "updated": 1, "total": 6})
        mock_api.assert_called_once()
        mock_web.assert_not_called()

    @patch("geocaches.services.souvenirs._refresh_web", return_value={"added": 0, "updated": 0, "total": 0})
    @patch("geocaches.feature_flags.gc_api_available", return_value=False)
    def test_api_preferred_uses_web_when_api_unavailable(self, _avail, mock_web):
        from geocaches.services import souvenirs as svc

        result = svc.refresh_all()

        self.assertEqual(result, {"added": 0, "updated": 0, "total": 0})
        mock_web.assert_called_once()


class TestDashboardContextAvailability(TestCase):
    def test_available_with_gc_account_but_no_api_tokens(self):
        from accounts.models import UserAccount
        from geocaches.services import souvenirs as svc

        UserAccount.objects.create(platform="gc", username="tester")

        with patch("accounts.gc_client.has_api_tokens", return_value=False):
            ctx = svc.dashboard_context()

        self.assertTrue(ctx["souvenir_gc_available"])

    def test_unavailable_with_neither(self):
        from geocaches.services import souvenirs as svc

        with patch("accounts.gc_client.has_api_tokens", return_value=False):
            ctx = svc.dashboard_context()

        self.assertFalse(ctx["souvenir_gc_available"])
