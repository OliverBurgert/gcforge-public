"""
Tests for geocaches.sync.gc_web.cache_fetch against a mocked session — pinned
to real, live-captured web.lists.create/addGeocache/bulkAddGeocaches/delete
requests/responses and a GPX-list download (HAR captures, 2026-08-13).
"""

from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from geocaches.sync.gc_web import cache_fetch


class TestCreateList(SimpleTestCase):
    @patch("geocaches.sync.gc_web.trpc.polite_delay")
    def test_sends_the_live_captured_request_shape(self, _delay):
        session = MagicMock()
        session.post.return_value.json.return_value = [
            {
                "result": {
                    "data": {
                        "count": 0, "totalRatingsCount": 0, "usefulRatingsCount": 0,
                        "createdUtc": "2026-08-13T15:53:27.37Z",
                        "lastUpdateUtc": "2026-08-13T15:53:27.37Z",
                        "referenceCode": "BMFRQXR",
                        "owner": {"referenceCode": "PR11Z6J", "username": "spazierenmitziel"},
                        "type": {"code": "bm"}, "name": "new test list",
                        "isShared": False, "isPublic": False, "isNotify": False,
                    }
                }
            }
        ]

        result = cache_fetch.create_list("new test list", csrf_token="tok", session=session)

        self.assertEqual(result["referenceCode"], "BMFRQXR")
        args, kwargs = session.post.call_args
        self.assertEqual(
            args[0], "https://www.geocaching.com/api/live/v1/trpc/web.lists.create?batch=1",
        )
        self.assertEqual(
            kwargs["json"], {"0": {"name": "new test list", "type": {"code": "bm"}}},
        )
        self.assertEqual(kwargs["headers"]["csrf-token"], "tok")


class TestAddGeocache(SimpleTestCase):
    @patch("geocaches.sync.gc_web.trpc.polite_delay")
    def test_sends_the_live_captured_request_shape(self, _delay):
        session = MagicMock()
        session.post.return_value.json.return_value = [
            {"result": {"data": {"referenceCode": "GC9WVK7"}}}
        ]

        cache_fetch.add_geocache("BMFRQXR", "GC9WVK7", csrf_token="tok", session=session)

        _, kwargs = session.post.call_args
        self.assertEqual(
            kwargs["json"], {"0": {"listReferenceCode": "BMFRQXR", "gcCode": "GC9WVK7"}},
        )


class TestBulkAddGeocaches(SimpleTestCase):
    @patch("geocaches.sync.gc_web.trpc.polite_delay")
    def test_sends_the_live_captured_request_shape(self, _delay):
        session = MagicMock()
        session.post.return_value.json.return_value = [
            {
                "result": {
                    "data": {
                        "findCount": 0,
                        "successes": [{"referenceCode": "GCAKKE5"}, {"referenceCode": "GC9WVHR"}],
                        "failures": [],
                    }
                }
            }
        ]

        result = cache_fetch.bulk_add_geocaches(
            "BMFRQXR", ["GCAKKE5", "GC9WVHR"], csrf_token="tok", session=session,
        )

        self.assertEqual(len(result["successes"]), 2)
        self.assertEqual(result["failures"], [])
        _, kwargs = session.post.call_args
        self.assertEqual(
            kwargs["json"],
            {"0": {"listReferenceCode": "BMFRQXR", "referenceCodes": ["GCAKKE5", "GC9WVHR"]}},
        )

    def test_rejects_more_than_500_codes(self):
        # Server hard-rejects with a 400 above 500 (confirmed live 2026-08-13,
        # 1000 real codes -> "Array must contain at most 500 element(s)").
        # bulk_add_geocaches() raises before ever making the request.
        with self.assertRaises(ValueError):
            cache_fetch.bulk_add_geocaches(
                "BMFRQXR", [f"GC{i:05d}" for i in range(501)],
                csrf_token="tok", session=MagicMock(),
            )


class TestDeleteList(SimpleTestCase):
    @patch("geocaches.sync.gc_web.trpc.polite_delay")
    def test_sends_the_live_captured_request_shape(self, _delay):
        session = MagicMock()
        # Real captured response: bare boolean data, not an object.
        session.post.return_value.json.return_value = [{"result": {"data": True}}]

        result = cache_fetch.delete_list("BMFRQXR", csrf_token="tok", session=session)

        self.assertTrue(result)
        args, kwargs = session.post.call_args
        self.assertEqual(
            args[0], "https://www.geocaching.com/api/live/v1/trpc/web.lists.delete?batch=1",
        )
        self.assertEqual(kwargs["json"], {"0": {"referenceCode": "BMFRQXR"}})


class TestDownloadListGpx(SimpleTestCase):
    @patch("geocaches.sync.gc_web.trpc.polite_delay")
    def test_requests_gpx_with_the_confirmed_headers(self, _delay):
        session = MagicMock()
        session.post.return_value.content = b"<gpx>...</gpx>"

        result = cache_fetch.download_list_gpx("BMFRQXR", csrf_token="tok", session=session)

        self.assertEqual(result, b"<gpx>...</gpx>")
        args, kwargs = session.post.call_args
        self.assertEqual(
            args[0], "https://www.geocaching.com/api/live/v1/gpx/list/BMFRQXR",
        )
        self.assertEqual(kwargs["headers"]["csrf-token"], "tok")
        self.assertEqual(kwargs["headers"]["Accept"], "application/gpx+xml")
        session.post.return_value.raise_for_status.assert_called_once()


class TestFetchCachesGpx(SimpleTestCase):
    @patch("geocaches.sync.gc_web.cache_fetch.delete_list")
    @patch("geocaches.sync.gc_web.cache_fetch.download_list_gpx", return_value=b"<gpx/>")
    @patch("geocaches.sync.gc_web.cache_fetch.bulk_add_geocaches", return_value={"successes": [], "failures": []})
    @patch("geocaches.sync.gc_web.cache_fetch.create_list", return_value={"referenceCode": "BMFRQXR"})
    @patch("geocaches.sync.gc_web.trpc.fetch_csrf_token", return_value="tok")
    def test_orchestrates_create_add_download_delete_in_order(
        self, fetch_csrf, mock_create, mock_bulk_add, mock_download, mock_delete,
    ):
        session = MagicMock()

        result = cache_fetch.fetch_caches_gpx(["GC1", "GC2"], session=session)

        self.assertEqual(result, b"<gpx/>")
        fetch_csrf.assert_called_once_with(session, "https://www.geocaching.com/plan/lists")
        mock_create.assert_called_once_with("GCForge sync", csrf_token="tok", session=session)
        mock_bulk_add.assert_called_once_with(
            "BMFRQXR", ["GC1", "GC2"], csrf_token="tok", session=session,
        )
        mock_download.assert_called_once_with("BMFRQXR", csrf_token="tok", session=session)
        mock_delete.assert_called_once_with("BMFRQXR", csrf_token="tok", session=session)

    @patch("geocaches.sync.gc_web.cache_fetch.delete_list")
    @patch(
        "geocaches.sync.gc_web.cache_fetch.download_list_gpx",
        side_effect=RuntimeError("network blip"),
    )
    @patch("geocaches.sync.gc_web.cache_fetch.bulk_add_geocaches", return_value={"successes": [], "failures": []})
    @patch("geocaches.sync.gc_web.cache_fetch.create_list", return_value={"referenceCode": "BMFRQXR"})
    @patch("geocaches.sync.gc_web.trpc.fetch_csrf_token", return_value="tok")
    def test_cleans_up_the_scratch_list_even_if_download_fails(
        self, _fetch_csrf, _mock_create, _mock_bulk_add, _mock_download, mock_delete,
    ):
        session = MagicMock()

        with self.assertRaises(RuntimeError):
            cache_fetch.fetch_caches_gpx(["GC1"], session=session)

        mock_delete.assert_called_once_with("BMFRQXR", csrf_token="tok", session=session)

    @patch("geocaches.sync.gc_web.cache_fetch.delete_list")
    @patch("geocaches.sync.gc_web.cache_fetch.download_list_gpx", return_value=b"<gpx/>")
    @patch(
        "geocaches.sync.gc_web.cache_fetch.bulk_add_geocaches",
        return_value={"successes": [{"referenceCode": "GC1"}], "failures": [{"referenceCode": "GCBOGUS"}]},
    )
    @patch("geocaches.sync.gc_web.cache_fetch.create_list", return_value={"referenceCode": "BMFRQXR"})
    @patch("geocaches.sync.gc_web.trpc.fetch_csrf_token", return_value="tok")
    def test_failed_codes_are_logged_not_raised(
        self, _fetch_csrf, _mock_create, _mock_bulk_add, _mock_download, _mock_delete,
    ):
        # Should not raise despite one code failing to add - caller gets the
        # GPX for whatever did succeed.
        result = cache_fetch.fetch_caches_gpx(["GC1", "GCBOGUS"], session=MagicMock())
        self.assertEqual(result, b"<gpx/>")

    @patch("geocaches.sync.gc_web.cache_fetch.delete_list")
    @patch("geocaches.sync.gc_web.cache_fetch.download_list_gpx", return_value=b"<gpx/>")
    @patch(
        "geocaches.sync.gc_web.cache_fetch.bulk_add_geocaches",
        return_value={"successes": [], "failures": []},
    )
    @patch("geocaches.sync.gc_web.cache_fetch.create_list", return_value={"referenceCode": "BMFRQXR"})
    @patch("geocaches.sync.gc_web.trpc.fetch_csrf_token", return_value="tok")
    def test_chunks_bulk_add_calls_at_the_confirmed_server_limit(
        self, _fetch_csrf, _mock_create, mock_bulk_add, _mock_download, _mock_delete,
    ):
        codes = [f"GC{i:05d}" for i in range(750)]  # over the 500-per-call cap

        cache_fetch.fetch_caches_gpx(codes, session=MagicMock())

        self.assertEqual(mock_bulk_add.call_count, 2)
        first_chunk = mock_bulk_add.call_args_list[0].args[1]
        second_chunk = mock_bulk_add.call_args_list[1].args[1]
        self.assertEqual(len(first_chunk), 500)
        self.assertEqual(len(second_chunk), 250)

    def test_rejects_more_than_1000_codes_without_any_network_call(self):
        codes = [f"GC{i:05d}" for i in range(1001)]
        with self.assertRaises(ValueError):
            cache_fetch.fetch_caches_gpx(codes, session=MagicMock())


class TestFetchAndImportCaches(SimpleTestCase):
    @patch("geocaches.services.import_and_enrich")
    @patch("geocaches.sync.gc_web.cache_fetch.fetch_caches_gpx", return_value=b"<gpx>data</gpx>")
    def test_writes_a_temp_file_and_delegates_to_the_existing_import_pipeline(
        self, mock_fetch, mock_import,
    ):
        mock_import.return_value = "stats-sentinel"

        result = cache_fetch.fetch_and_import_caches(["GC9WVK7"], tag_names=["web-fetch"])

        self.assertEqual(result, "stats-sentinel")
        mock_fetch.assert_called_once_with(["GC9WVK7"], session=None)
        mock_import.assert_called_once()
        args, kwargs = mock_import.call_args
        self.assertEqual(args[0], "gpx")
        # The temp file path was passed and (by the time we get here) already cleaned up.
        import os
        self.assertFalse(os.path.exists(args[1]))
        self.assertTrue(args[1].endswith(".gpx"))
        self.assertEqual(kwargs.get("tag_names"), ["web-fetch"])
