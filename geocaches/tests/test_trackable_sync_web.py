"""
Tests for the website-only trackable DB-persistence sync path (no
partner-API token) — geocaches.services.trackable_sync's
sync_trackable_web()/sync_trackable_logs_web() and the
sync_trackable()/sync_trackable_logs() api_preferred/web_preferred
resolvers.

**Live-verified 2026-08-16** against three real trackables covering all
three ownership/holder states (Oliver's own TBZH18 held-by-user-turned-
in-a-cache, his TBA08V7 owned-but-in-a-cache, and TB9FHAG which he no
longer owns or holds), cross-checked against the official API's own
output for two of them — see trackable_sync.py's sync_trackable_web()
docstring. No live network calls in this suite.
"""

from unittest.mock import patch

from django.test import TestCase

from geocaches.models import Geocache, Trackable, TrackableImage, TrackableLog
from geocaches.services import trackable_sync as svc

# Real loggable shapes, 2026-08-16 live capture.
_LOGGABLE_IN_CACHE = {
    "referenceCode": "TBZH18",
    "iconUrl": "https://www.geocaching.com/images/wpttypes/21.gif",
    "name": "Lilly the little Lobster",
    "dateReleased": "2006-06-25T00:00:00",
    "distanceTraveledInKilometers": 18152.193312,
    "trackableType": 21,
    "owner": {"code": "PR11Z6J", "userName": "spazierenmitziel"},
    "currentGeocache": {"id": 805885, "referenceCode": "GC19VC7", "name": "Ninja Hideout - Travelbug Dojo"},
    "isMissing": False, "isActive": True, "isLocked": False,
}

_LOGGABLE_HELD_BY_OTHER = {
    "referenceCode": "TB9FHAG",
    "iconUrl": "https://www.geocaching.com/images/wpttypes/21.gif",
    "name": "Miraculix on Tour",
    "dateReleased": "2021-05-07T12:00:00",
    "distanceTraveledInKilometers": 16096.7,
    "trackableType": 21,
    "owner": {"code": "PR1683HA", "userName": "JuMaPa1"},
    "holder": {"code": "PR6K15A", "userName": "penumbra59"},
    "isMissing": False, "isActive": True, "isLocked": False,
}

_LOGGABLE_MISSING = dict(_LOGGABLE_IN_CACHE, isMissing=True)
del _LOGGABLE_MISSING["currentGeocache"]

_CLASSIC_OWNED = {"origin": "Germany", "goal": "See the world.", "about": "A lobster.", "tracking_code": "601023"}
_CLASSIC_NOT_OWNED = {"origin": "Bayern, Germany", "goal": "Travel.", "about": "Hallo.", "tracking_code": ""}


class TestSyncTrackableWeb(TestCase):
    @patch("geocaches.sync.gc_web.trackables.list_trackable_images_web", return_value=[])
    @patch("geocaches.sync.gc_web.trackables.list_inventory_web", return_value=[])
    @patch("geocaches.sync.gc_web.trackables.list_collection_web", return_value=[])
    @patch("geocaches.sync.gc_web.trackables.get_trackable_classic_detail_web", return_value=_CLASSIC_OWNED)
    @patch("geocaches.sync.gc_web.trackables.get_trackable_web", return_value=_LOGGABLE_IN_CACHE)
    def test_in_cache_state(self, *_mocks):
        tb = svc.sync_trackable_web("TBZH18")

        self.assertEqual(tb.name, "Lilly the little Lobster")
        self.assertEqual(tb.holder_state, "in_cache")
        self.assertEqual(tb.current_geocache_code, "GC19VC7")
        self.assertEqual(tb.current_geocache_name, "Ninja Hideout - Travelbug Dojo")
        self.assertEqual(tb.owner_name, "spazierenmitziel")
        # "PR11Z6J" isn't purely numeric after the PR prefix, so owner_gc_id
        # stays unset -- same parsing rule as the official-API path.
        self.assertIsNone(tb.owner_gc_id)
        self.assertEqual(tb.origin, "Germany")
        self.assertEqual(tb.goal, "See the world.")
        self.assertEqual(tb.about, "A lobster.")
        self.assertEqual(tb.tracking_code, "601023")
        self.assertEqual(tb.total_distance_km, 18152.193312)
        self.assertEqual(str(tb.released_date), "2006-06-25")

    @patch("geocaches.sync.gc_web.trackables.list_trackable_images_web", return_value=[])
    @patch("geocaches.sync.gc_web.trackables.list_inventory_web", return_value=[])
    @patch("geocaches.sync.gc_web.trackables.list_collection_web", return_value=[])
    @patch("geocaches.sync.gc_web.trackables.get_trackable_classic_detail_web", return_value=_CLASSIC_NOT_OWNED)
    @patch("geocaches.sync.gc_web.trackables.get_trackable_web", return_value=_LOGGABLE_HELD_BY_OTHER)
    def test_held_by_other_not_owned(self, *_mocks):
        """The core bug this fixes: tracking_code (owner-scoped, not
        holder-scoped) must NOT be used as the we_hold_it signal -- must
        come out held_by_other here, not held_by_user."""
        tb = svc.sync_trackable_web("TB9FHAG")

        self.assertEqual(tb.holder_state, "held_by_other")
        self.assertEqual(tb.current_holder_name, "penumbra59")
        self.assertEqual(tb.current_geocache_code, "")
        self.assertEqual(tb.tracking_code, "")

    @patch("geocaches.sync.gc_web.trackables.list_trackable_images_web", return_value=[])
    @patch("geocaches.sync.gc_web.trackables.list_inventory_web", return_value=[{"name": "Lilly the little Lobster"}])
    @patch("geocaches.sync.gc_web.trackables.list_collection_web", return_value=[])
    @patch("geocaches.sync.gc_web.trackables.get_trackable_classic_detail_web", return_value=_CLASSIC_OWNED)
    @patch("geocaches.sync.gc_web.trackables.get_trackable_web")
    def test_held_by_user_when_in_inventory_list(self, mock_get, *_mocks):
        loggable = {k: v for k, v in _LOGGABLE_IN_CACHE.items() if k != "currentGeocache"}
        loggable["holder"] = {"userName": "spazierenmitziel", "code": "PR11Z6J"}
        mock_get.return_value = loggable

        tb = svc.sync_trackable_web("TBZH18")

        self.assertEqual(tb.holder_state, "held_by_user")
        self.assertEqual(tb.current_geocache_code, "")

    @patch("geocaches.sync.gc_web.trackables.list_trackable_images_web", return_value=[])
    @patch("geocaches.sync.gc_web.trackables.list_inventory_web", return_value=[])
    @patch("geocaches.sync.gc_web.trackables.list_collection_web", return_value=[{"name": "Lilly the little Lobster"}])
    @patch("geocaches.sync.gc_web.trackables.get_trackable_classic_detail_web", return_value=_CLASSIC_OWNED)
    @patch("geocaches.sync.gc_web.trackables.get_trackable_web")
    def test_collection_state(self, mock_get, *_mocks):
        mock_get.return_value = {k: v for k, v in _LOGGABLE_IN_CACHE.items() if k != "currentGeocache"}

        tb = svc.sync_trackable_web("TBZH18")

        self.assertEqual(tb.holder_state, "collection")

    @patch("geocaches.sync.gc_web.trackables.list_trackable_images_web", return_value=[])
    @patch("geocaches.sync.gc_web.trackables.list_inventory_web", return_value=[])
    @patch("geocaches.sync.gc_web.trackables.list_collection_web", return_value=[])
    @patch("geocaches.sync.gc_web.trackables.get_trackable_classic_detail_web", return_value=_CLASSIC_OWNED)
    @patch("geocaches.sync.gc_web.trackables.get_trackable_web", return_value=_LOGGABLE_MISSING)
    def test_missing_state(self, *_mocks):
        tb = svc.sync_trackable_web("TBZH18")

        self.assertEqual(tb.holder_state, "missing")
        self.assertEqual(tb.current_geocache_code, "")

    @patch("geocaches.sync.gc_web.trackables.list_trackable_images_web", return_value=[])
    @patch("geocaches.sync.gc_web.trackables.list_inventory_web", return_value=[])
    @patch("geocaches.sync.gc_web.trackables.list_collection_web", return_value=[])
    @patch("geocaches.sync.gc_web.trackables.get_trackable_classic_detail_web", return_value=_CLASSIC_OWNED)
    @patch("geocaches.sync.gc_web.trackables.get_trackable_web", return_value=_LOGGABLE_IN_CACHE)
    def test_resolves_local_geocache_fk_when_known(self, *_mocks):
        Geocache.objects.create(gc_code="GC19VC7", name="Ninja Hideout - Travelbug Dojo", latitude=1.0, longitude=1.0, cache_type="Traditional Cache")

        tb = svc.sync_trackable_web("TBZH18")

        self.assertIsNotNone(tb.current_geocache)
        self.assertEqual(tb.current_geocache.gc_code, "GC19VC7")

    @patch("geocaches.sync.gc_web.trackables.list_trackable_images_web", return_value=[])
    @patch("geocaches.sync.gc_web.trackables.list_inventory_web", return_value=[])
    @patch("geocaches.sync.gc_web.trackables.list_collection_web", return_value=[])
    @patch("geocaches.sync.gc_web.trackables.get_trackable_classic_detail_web", return_value=_CLASSIC_OWNED)
    @patch("geocaches.sync.gc_web.trackables.get_trackable_web", return_value=_LOGGABLE_IN_CACHE)
    def test_kind_left_untouched(self, *_mocks):
        from geocaches.models import TrackableKind
        Trackable.objects.create(reference_code="TBZH18", name="stale", kind=TrackableKind.GEOCOIN)

        tb = svc.sync_trackable_web("TBZH18")

        self.assertEqual(tb.kind, TrackableKind.GEOCOIN)

    def test_empty_ref_raises(self):
        with self.assertRaises(ValueError):
            svc.sync_trackable_web("")


class TestHolderFlagsWeb(TestCase):
    @patch("geocaches.sync.gc_web.trackables.list_inventory_web", return_value=[{"name": "A"}])
    @patch("geocaches.sync.gc_web.trackables.list_collection_web", return_value=[{"name": "A"}])
    def test_collection_takes_priority_over_inventory(self, _coll, _inv):
        we_hold_it, in_collection = svc._holder_flags_web("A")
        self.assertTrue(we_hold_it)
        self.assertTrue(in_collection)

    @patch("geocaches.sync.gc_web.trackables.list_inventory_web", return_value=[{"name": "A"}])
    @patch("geocaches.sync.gc_web.trackables.list_collection_web", return_value=[])
    def test_inventory_only(self, _coll, _inv):
        we_hold_it, in_collection = svc._holder_flags_web("A")
        self.assertTrue(we_hold_it)
        self.assertFalse(in_collection)

    @patch("geocaches.sync.gc_web.trackables.list_inventory_web", return_value=[])
    @patch("geocaches.sync.gc_web.trackables.list_collection_web", return_value=[])
    def test_neither(self, _coll, _inv):
        we_hold_it, in_collection = svc._holder_flags_web("A")
        self.assertFalse(we_hold_it)
        self.assertFalse(in_collection)

    @patch("geocaches.sync.gc_web.trackables.list_inventory_web", side_effect=RuntimeError("boom"))
    @patch("geocaches.sync.gc_web.trackables.list_collection_web", side_effect=RuntimeError("boom"))
    def test_failures_are_non_fatal(self, _coll, _inv):
        we_hold_it, in_collection = svc._holder_flags_web("A")
        self.assertFalse(we_hold_it)
        self.assertFalse(in_collection)


class TestBuildTrackableLogWeb(TestCase):
    def setUp(self):
        self.tb = Trackable.objects.create(reference_code="TBZH18", name="Lilly")

    def test_maps_precise_detail(self):
        detail = {
            "log_reference_code": "TL276GPQV", "date_created_utc": "2026-08-14T21:54:03.65Z",
            "log_date": "2026-08-14T12:00:00", "log_type_id": 75,
            "log_text": "Visited!", "username": "spazierenmitziel",
            "geocache": {"referenceCode": "GCBNTF9"},
        }
        log = svc._build_trackable_log_web(self.tb, detail)

        self.assertEqual(log.log_type, "Visited")
        self.assertEqual(log.text, "Visited!")
        self.assertEqual(log.source_id, "TL276GPQV")
        self.assertEqual(log.geocache_ref_code, "GCBNTF9")

    def test_unknown_type_id_falls_back_to_write_note(self):
        detail = {"log_reference_code": "TL1", "log_type_id": 999999, "log_date": "2026-08-14T12:00:00"}
        log = svc._build_trackable_log_web(self.tb, detail)
        self.assertEqual(log.log_type, "Write note")

    def test_missing_source_id_returns_none(self):
        self.assertIsNone(svc._build_trackable_log_web(self.tb, {}))


class TestSyncTrackableLogsWeb(TestCase):
    def setUp(self):
        self.tb = Trackable.objects.create(reference_code="TBZH18", name="Lilly")

    def _row(self, ref):
        return {"log_reference_code": ref, "date_text": "2026-08-14", "action_text": "x", "location_text": "", "note_text": ""}

    def _detail(self, ref):
        return {"log_reference_code": ref, "date_created_utc": "2026-08-14T12:00:00Z", "log_date": "2026-08-14T12:00:00", "log_type_id": 75, "log_text": "hi", "username": "u"}

    @patch("geocaches.sync.gc_web.trackables.fetch_trackable_log_detail_web")
    @patch("geocaches.sync.gc_web.trackables.get_trackable_logs_web")
    def test_incremental_stops_on_fully_redundant_page(self, mock_logs, mock_detail):
        TrackableLog.objects.create(trackable=self.tb, log_type="Visited", logged_date="2026-08-13", source_id="TL_OLD")
        mock_logs.side_effect = [[self._row("TL_OLD")]]
        mock_detail.side_effect = lambda ref, **kw: self._detail(ref)

        new_count = svc.sync_trackable_logs_web("TBZH18", full=False)

        self.assertEqual(new_count, 0)
        mock_detail.assert_not_called()  # existing log skipped before any detail fetch
        mock_logs.assert_called_once()

    @patch("geocaches.sync.gc_web.trackables.fetch_trackable_log_detail_web")
    @patch("geocaches.sync.gc_web.trackables.get_trackable_logs_web")
    def test_new_entries_get_detail_fetched_and_saved(self, mock_logs, mock_detail):
        rows = [self._row(f"TL{i}") for i in range(3)]
        mock_logs.side_effect = [rows, []]
        mock_detail.side_effect = lambda ref, **kw: self._detail(ref)

        new_count = svc.sync_trackable_logs_web("TBZH18", full=False)

        self.assertEqual(new_count, 3)
        self.assertEqual(mock_detail.call_count, 3)
        self.assertEqual(TrackableLog.objects.filter(trackable=self.tb).count(), 3)

    @patch("geocaches.sync.gc_web.trackables.fetch_trackable_log_detail_web")
    @patch("geocaches.sync.gc_web.trackables.get_trackable_logs_web")
    def test_full_walks_multiple_pages(self, mock_logs, mock_detail):
        page1 = [self._row(f"TL{i}") for i in range(10)]
        page2 = [self._row(f"TL1{i}") for i in range(3)]
        mock_logs.side_effect = [page1, page2]
        mock_detail.side_effect = lambda ref, **kw: self._detail(ref)

        new_count = svc.sync_trackable_logs_web("TBZH18", full=True)

        self.assertEqual(new_count, 13)
        # Page 2 has fewer than 10 rows, signalling end of history -- no
        # third (empty) page fetch needed.
        self.assertEqual(mock_logs.call_count, 2)

    def test_empty_ref_raises(self):
        with self.assertRaises(ValueError):
            svc.sync_trackable_logs_web("")


class TestSyncTrackableImagesWeb(TestCase):
    def setUp(self):
        self.tb = Trackable.objects.create(reference_code="TBZH18", name="Lilly")

    @patch("geocaches.services.image_cache.prefetch")
    @patch("geocaches.sync.gc_web.trackables.list_trackable_images_web")
    def test_upserts_by_guid_and_drops_stale(self, mock_list, _prefetch):
        TrackableImage.objects.create(trackable=self.tb, source_id="stale-guid", url="x")
        mock_list.return_value = [{
            "guid": "5e4b7c6e-0ec1-413b-87fa-c52ad888f1bc",
            "thumbnail_url": "https://img.geocaching.com/track/log/thumb/5e4b.jpg",
            "large_url": "https://img.geocaching.com/track/log/large/5e4b.jpg",
            "date_text": "2008-03-09", "description": "A photo.", "is_log_image": True,
        }]

        svc._sync_trackable_images_web(self.tb)

        images = TrackableImage.objects.filter(trackable=self.tb)
        self.assertEqual(images.count(), 1)
        img = images.first()
        self.assertEqual(img.source_id, "5e4b7c6e-0ec1-413b-87fa-c52ad888f1bc")
        self.assertEqual(img.description, "A photo.")
        self.assertEqual(str(img.uploaded_at.date()), "2008-03-09")

    @patch("geocaches.sync.gc_web.trackables.list_trackable_images_web", side_effect=RuntimeError("boom"))
    def test_failure_is_non_fatal(self, _mock):
        svc._sync_trackable_images_web(self.tb)  # must not raise


class TestTrackableSyncResolvers(TestCase):
    """sync_trackable()/sync_trackable_logs()'s api_preferred/web_preferred resolvers."""

    def test_web_preferred_sync_trackable_skips_api(self):
        from preferences.models import UserPreference
        UserPreference.set("gc_access_mode", "web_preferred")

        with patch.object(svc, "sync_trackable_web", return_value="web-tb") as mock_web, \
                patch.object(svc, "_sync_trackable_api") as mock_api:
            result = svc.sync_trackable("TBZH18")

        self.assertEqual(result, "web-tb")
        mock_web.assert_called_once_with("TBZH18")
        mock_api.assert_not_called()

    def test_api_preferred_falls_back_to_web_on_exception(self):
        with patch("geocaches.feature_flags.gc_api_available", return_value=True), \
                patch.object(svc, "_sync_trackable_api", side_effect=RuntimeError("token expired")), \
                patch.object(svc, "sync_trackable_web", return_value="web-tb") as mock_web:
            result = svc.sync_trackable("TBZH18")

        self.assertEqual(result, "web-tb")
        mock_web.assert_called_once()

    def test_api_preferred_uses_api_when_it_succeeds(self):
        with patch("geocaches.feature_flags.gc_api_available", return_value=True), \
                patch.object(svc, "_sync_trackable_api", return_value="api-tb") as mock_api, \
                patch.object(svc, "sync_trackable_web") as mock_web:
            result = svc.sync_trackable("TBZH18")

        self.assertEqual(result, "api-tb")
        mock_api.assert_called_once()
        mock_web.assert_not_called()

    def test_client_kwarg_forwarded_to_api_path_only(self):
        sentinel_client = object()
        with patch("geocaches.feature_flags.gc_api_available", return_value=True), \
                patch.object(svc, "_sync_trackable_api", return_value="api-tb") as mock_api:
            svc.sync_trackable("TBZH18", client=sentinel_client)

        mock_api.assert_called_once_with("TBZH18", client=sentinel_client)

    def test_web_preferred_sync_trackable_logs_skips_api(self):
        from preferences.models import UserPreference
        UserPreference.set("gc_access_mode", "web_preferred")

        with patch.object(svc, "sync_trackable_logs_web", return_value=5) as mock_web, \
                patch.object(svc, "_sync_trackable_logs_api") as mock_api:
            result = svc.sync_trackable_logs("TBZH18", full=True)

        self.assertEqual(result, 5)
        mock_web.assert_called_once_with("TBZH18", full=True, max_pages=200)
        mock_api.assert_not_called()

    def test_sync_trackable_logs_api_preferred_falls_back(self):
        with patch("geocaches.feature_flags.gc_api_available", return_value=True), \
                patch.object(svc, "_sync_trackable_logs_api", side_effect=RuntimeError("boom")), \
                patch.object(svc, "sync_trackable_logs_web", return_value=2) as mock_web:
            result = svc.sync_trackable_logs("TBZH18")

        self.assertEqual(result, 2)
        mock_web.assert_called_once()
