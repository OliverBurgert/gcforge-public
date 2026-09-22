"""
Tests for geocaches.sync.gc_web.log_client against a mocked session — pinned
to real, live-captured web.logs.createGeocacheLog / web.logs.updateGeocacheLog
requests/responses (HAR captures, 2026-08-13).
"""

from datetime import date
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from geocaches.sync.gc_web import log_client


class TestTrackableEntry(SimpleTestCase):
    def test_visited_uses_the_har_confirmed_id(self):
        # Independently HAR-confirmed on this path (2026-08-13): 75.
        self.assertEqual(
            log_client.trackable_entry("TB91ENV", "Visited"),
            {"trackableCode": "TB91ENV", "trackableLogTypeId": 75},
        )

    def test_other_actions_use_the_shared_official_api_ids(self):
        self.assertEqual(
            log_client.trackable_entry("TB123", "Discovered It")["trackableLogTypeId"], 48,
        )
        self.assertEqual(
            log_client.trackable_entry("TB123", "Dropped Off")["trackableLogTypeId"], 14,
        )

    def test_unknown_action_raises(self):
        with self.assertRaises(ValueError):
            log_client.trackable_entry("TB123", "Not A Real Action")


class TestCreateLog(SimpleTestCase):
    @patch("geocaches.sync.gc_web.trpc.polite_delay")
    def test_sends_the_live_captured_request_shape(self, _delay):
        session = MagicMock()
        session.get.return_value.text = '{"props":{"pageProps":{"csrfToken":"tok-abc"}}}'
        session.post.return_value.json.return_value = [
            {
                "result": {
                    "data": {
                        "guid": "4d487cbd-da04-4da8-aa41-5fad2f00cb12",
                        "logReferenceCode": "GL1H4NGRR",
                        "dateTimeCreatedUtc": "2026-08-12T15:16:49",
                        "dateTimeLastUpdatedUtc": "2026-08-12T15:16:49",
                        "logDate": "2026-08-12T12:00:00",
                        "logType": 4,
                        "images": [],
                        "trackables": [],
                        "cannotDelete": False,
                        "usedFavoritePoint": False,
                        "statusCode": 200,
                    }
                }
            }
        ]

        result = log_client.create_log(
            "GC9WVK7",
            log_type_id=4,
            log_text="test",
            log_date=date(2026, 8, 12),
            session=session,
        )

        self.assertEqual(result["logReferenceCode"], "GL1H4NGRR")

        # CSRF fetched from the /live log page first.
        session.get.assert_called_once_with(
            "https://www.geocaching.com/live/geocache/GC9WVK7/log", timeout=20
        )

        # POST body matches the live-captured shape exactly.
        _, kwargs = session.post.call_args
        self.assertEqual(
            kwargs["json"],
            {
                "0": {
                    "referenceCode": "GC9WVK7",
                    "body": {
                        "images": [],
                        "logDate": "2026-08-12T12:00:00.000Z",
                        "logText": "test",
                        "logType": 4,
                        "trackables": [],
                        "geocacheReferenceCode": "",
                    },
                }
            },
        )
        self.assertEqual(kwargs["headers"]["csrf-token"], "tok-abc")

    @patch("geocaches.sync.gc_web.trpc.polite_delay")
    def test_posts_to_the_confirmed_procedure_url(self, _delay):
        session = MagicMock()
        session.get.return_value.text = '{"csrfToken":"tok"}'
        session.post.return_value.json.return_value = [{"result": {"data": {}}}]

        log_client.create_log(
            "GC9WVK7", log_type_id=2, log_text="hi", log_date=date(2026, 1, 1), session=session,
        )

        args, _ = session.post.call_args
        self.assertEqual(
            args[0],
            "https://www.geocaching.com/api/live/v1/trpc/web.logs.createGeocacheLog?batch=1",
        )


class TestUpdateLog(SimpleTestCase):
    @patch("geocaches.sync.gc_web.trpc.polite_delay")
    def test_sends_the_live_captured_request_shape(self, _delay):
        session = MagicMock()
        session.get.return_value.text = '{"props":{"pageProps":{"csrfToken":"tok-edit"}}}'
        session.post.return_value.json.return_value = [
            {
                "result": {
                    "data": {
                        "guid": "285f0a47-4a09-4321-81be-d1530ec2330b",
                        "logReferenceCode": "GL1H4NTT8",
                        "dateTimeCreatedUtc": "2026-08-12T15:54:20",
                        "dateTimeLastUpdatedUtc": "2026-08-12T15:55:12.466Z",
                        "logDate": "2026-08-12T12:00:00",
                        "logType": 4,
                        "cannotDelete": False,
                        "usedFavoritePoint": False,
                        "statusCode": 200,
                    }
                }
            }
        ]

        result = log_client.update_log(
            "GC9WVK7",
            "GL1H4NTT8",
            log_type_id=4,
            log_text="test edit",
            log_date=date(2026, 8, 12),
            session=session,
        )

        self.assertEqual(result["logReferenceCode"], "GL1H4NTT8")

        # CSRF fetched from the edit page (Referer-confirmed URL pattern).
        session.get.assert_called_once_with(
            "https://www.geocaching.com/live/geocache/GC9WVK7/log/GL1H4NTT8/edit?logType=4",
            timeout=20,
        )

        # POST body matches the live-captured shape exactly: referenceCode is
        # the LOG's own ref here, not the cache code.
        _, kwargs = session.post.call_args
        self.assertEqual(
            kwargs["json"],
            {
                "0": {
                    "referenceCode": "GL1H4NTT8",
                    "body": {
                        "images": [],
                        "logDate": "2026-08-12T12:00:00.000Z",
                        "logText": "test edit",
                        "logType": 4,
                        "trackables": [],
                        "geocacheReferenceCode": "",
                    },
                }
            },
        )
        self.assertEqual(kwargs["headers"]["csrf-token"], "tok-edit")

    @patch("geocaches.sync.gc_web.trpc.polite_delay")
    def test_used_favorite_point_is_included_only_when_explicitly_set(self, _delay):
        # Live-confirmed 2026-08-14: sending "usedFavoritePoint": true on an
        # update echoed back true — see module docstring for the real
        # find (GCBPNTE) this was confirmed against.
        session = MagicMock()
        session.get.return_value.text = '{"csrfToken":"tok"}'
        session.post.return_value.json.return_value = [
            {"result": {"data": {"logReferenceCode": "GL1", "usedFavoritePoint": True}}}
        ]

        log_client.update_log(
            "GC9WVK7", "GL1", log_type_id=2, log_text="x", log_date=date(2026, 8, 14),
            used_favorite_point=True, session=session,
        )

        _, kwargs = session.post.call_args
        self.assertTrue(kwargs["json"]["0"]["body"]["usedFavoritePoint"])

    @patch("geocaches.sync.gc_web.trpc.polite_delay")
    def test_posts_to_the_confirmed_procedure_url(self, _delay):
        session = MagicMock()
        session.get.return_value.text = '{"csrfToken":"tok"}'
        session.post.return_value.json.return_value = [{"result": {"data": {}}}]

        log_client.update_log(
            "GC9WVK7", "GL1H4NTT8", log_type_id=4, log_text="hi",
            log_date=date(2026, 1, 1), session=session,
        )

        args, _ = session.post.call_args
        self.assertEqual(
            args[0],
            "https://www.geocaching.com/api/live/v1/trpc/web.logs.updateGeocacheLog?batch=1",
        )


class TestDeleteLog(SimpleTestCase):
    @patch("geocaches.sync.gc_web.trpc.polite_delay")
    def test_sends_the_live_captured_request_shape(self, _delay):
        session = MagicMock()
        session.get.return_value.text = '{"props":{"pageProps":{"csrfToken":"tok-delete"}}}'
        # Real captured response: {"result": {}}, no "data" key at all.
        session.post.return_value.json.return_value = [{"result": {}}]

        result = log_client.delete_log("GL1H4P2FT", session=session)

        self.assertIsNone(result)

        # CSRF fetched from the log's own view page (Referer-confirmed URL).
        session.get.assert_called_once_with(
            "https://www.geocaching.com/live/log/GL1H4P2FT", timeout=20
        )

        # POST body matches the live-captured shape exactly: flat, no nested
        # "body" object, with the always-present (empty in the capture)
        # reasonText field.
        _, kwargs = session.post.call_args
        self.assertEqual(
            kwargs["json"],
            {"0": {"referenceCode": "GL1H4P2FT", "reasonText": ""}},
        )
        self.assertEqual(kwargs["headers"]["csrf-token"], "tok-delete")

    @patch("geocaches.sync.gc_web.trpc.polite_delay")
    def test_posts_to_the_confirmed_procedure_url(self, _delay):
        session = MagicMock()
        session.get.return_value.text = '{"csrfToken":"tok"}'
        session.post.return_value.json.return_value = [{"result": {}}]

        log_client.delete_log("GL1H4P2FT", session=session)

        args, _ = session.post.call_args
        self.assertEqual(
            args[0],
            "https://www.geocaching.com/api/live/v1/trpc/web.logs.deleteGeocacheLog?batch=1",
        )

    @patch("geocaches.sync.gc_web.trpc.polite_delay")
    def test_reason_text_is_forwarded(self, _delay):
        session = MagicMock()
        session.get.return_value.text = '{"csrfToken":"tok"}'
        session.post.return_value.json.return_value = [{"result": {}}]

        log_client.delete_log("GL1H4P2FT", reason_text="posted by mistake", session=session)

        _, kwargs = session.post.call_args
        self.assertEqual(kwargs["json"]["0"]["reasonText"], "posted by mistake")


class TestSubmitLog(SimpleTestCase):
    """submit_log() wraps create_log() to match GCClient.submit_log()'s call shape."""

    @patch("geocaches.sync.gc_web.trpc.polite_delay")
    def test_looks_up_log_type_id_and_aliases_reference_code(self, _delay):
        session = MagicMock()
        session.get.return_value.text = '{"csrfToken":"tok"}'
        session.post.return_value.json.return_value = [
            {"result": {"data": {"logReferenceCode": "GL1H4NGRR"}}}
        ]

        result = log_client.submit_log(
            "GC9WVK7", "Found it", "2026-08-12T14:23:05.000Z", "great cache!", session=session,
        )

        self.assertEqual(result, {"referenceCode": "GL1H4NGRR"})
        _, kwargs = session.post.call_args
        # "Found it" -> log type id 2, per geocaches.sync.gc_reference._REVERSE_LOG_TYPE_MAP.
        self.assertEqual(kwargs["json"]["0"]["body"]["logType"], 2)
        # Only the date survives — time-of-day is dropped (noon UTC always sent).
        self.assertEqual(kwargs["json"]["0"]["body"]["logDate"], "2026-08-12T12:00:00.000Z")
        self.assertEqual(kwargs["json"]["0"]["body"]["logText"], "great cache!")

    def test_unknown_log_type_raises(self):
        with self.assertRaises(ValueError):
            log_client.submit_log(
                "GC9WVK7", "Not A Real Log Type", "2026-08-12T14:23:05.000Z", "text",
                session=MagicMock(),
            )

    @patch("geocaches.sync.gc_web.trpc.polite_delay")
    def test_favourite_point_makes_a_followup_update_call(self, _delay):
        # Live-confirmed 2026-08-14 (a real find, GCBPNTE): usedFavoritePoint
        # only works as a follow-up update_log() edit, not on create itself —
        # submit_log() makes both calls in sequence when requested.
        session = MagicMock()
        session.get.return_value.text = '{"csrfToken":"tok"}'
        session.post.return_value.json.side_effect = [
            [{"result": {"data": {"logReferenceCode": "GL1H54C2W"}}}],
            [{"result": {"data": {"logReferenceCode": "GL1H54C2W", "usedFavoritePoint": True}}}],
        ]

        result = log_client.submit_log(
            "GCBPNTE", "Found it", "2026-08-14T12:00:00.000Z", "nice",
            use_favourite_point=True, session=session,
        )

        self.assertEqual(result, {"referenceCode": "GL1H54C2W"})
        self.assertEqual(session.post.call_count, 2)
        create_kwargs = session.post.call_args_list[0].kwargs
        self.assertNotIn("usedFavoritePoint", create_kwargs["json"]["0"]["body"])
        update_kwargs = session.post.call_args_list[1].kwargs
        self.assertEqual(update_kwargs["json"]["0"]["referenceCode"], "GL1H54C2W")
        self.assertTrue(update_kwargs["json"]["0"]["body"]["usedFavoritePoint"])

    @patch("geocaches.sync.gc_web.trpc.polite_delay")
    def test_no_favourite_point_makes_only_one_call(self, _delay):
        session = MagicMock()
        session.get.return_value.text = '{"csrfToken":"tok"}'
        session.post.return_value.json.return_value = [
            {"result": {"data": {"logReferenceCode": "GL1"}}}
        ]

        log_client.submit_log(
            "GC9WVK7", "Found it", "2026-08-12T14:23:05.000Z", "text", session=session,
        )

        self.assertEqual(session.post.call_count, 1)


class TestAttachImages(SimpleTestCase):
    """attach_images() wraps update_log() to associate already-uploaded guids
    with a just-created log — see its docstring for why (2026-08-14)."""

    @patch("geocaches.sync.gc_web.trpc.polite_delay")
    def test_calls_update_log_with_the_image_guids(self, _delay):
        session = MagicMock()
        session.get.return_value.text = '{"props":{"pageProps":{"csrfToken":"tok"}}}'
        session.post.return_value.json.return_value = [
            {"result": {"data": {"logReferenceCode": "GL1H4NTT8", "images": ["img-guid-1"]}}}
        ]

        result = log_client.attach_images(
            "GC9WVK7", "GL1H4NTT8", "Found it", "2026-08-14T14:23:05.000Z", "nice",
            ["img-guid-1"], session=session,
        )

        self.assertEqual(result["logReferenceCode"], "GL1H4NTT8")
        _, kwargs = session.post.call_args
        body = kwargs["json"]["0"]["body"]
        self.assertEqual(body["images"], ["img-guid-1"])
        self.assertEqual(body["logType"], 2)  # "Found it" -> 2
        self.assertEqual(body["logText"], "nice")
        # referenceCode addresses the LOG, same as update_log()'s own shape.
        self.assertEqual(kwargs["json"]["0"]["referenceCode"], "GL1H4NTT8")

    def test_unknown_log_type_raises(self):
        with self.assertRaises(ValueError):
            log_client.attach_images(
                "GC9WVK7", "GL1H4NTT8", "Not A Real Log Type",
                "2026-08-14T14:23:05.000Z", "nice", ["img-guid-1"], session=MagicMock(),
            )


class TestUploadLogImage(SimpleTestCase):
    """Pinned to the real two-call flow, live-verified 2026-08-13."""

    @patch("geocaches.sync.gc_web.trpc.polite_delay")
    def test_upload_without_caption_skips_the_replace_call(self, _delay):
        session = MagicMock()
        session.get.return_value.text = '{"csrfToken":"tok"}'
        session.post.return_value.json.return_value = {
            "guid": "7a71851a-39e0-452a-80b6-0efaa45e5bc8",
            "url": "https://img.geocaching.com/7a71851a-39e0-452a-80b6-0efaa45e5bc8.png",
            "thumbnailUrl": "https://img.geocaching.com/large/7a71851a-39e0-452a-80b6-0efaa45e5bc8.png",
            "success": True,
        }

        result = log_client.upload_log_image("GC9WVK7", b"\x89PNG", "image/png", session=session)

        self.assertEqual(result["guid"], "7a71851a-39e0-452a-80b6-0efaa45e5bc8")
        session.post.assert_called_once()
        args, kwargs = session.post.call_args
        self.assertEqual(args[0], "https://www.geocaching.com/api/live/v1/logdrafts/images")
        self.assertEqual(kwargs["files"]["file"], ("image.jpg", b"\x89PNG", "image/png"))
        self.assertEqual(kwargs["headers"]["csrf-token"], "tok")
        session.put.assert_not_called()  # no caption -> no second call

    @patch("geocaches.sync.gc_web.trpc.polite_delay")
    def test_upload_with_caption_calls_replace_as_multipart(self, _delay):
        session = MagicMock()
        session.get.return_value.text = '{"csrfToken":"tok"}'
        session.post.return_value.json.return_value = {"guid": "abc-123", "url": "u", "thumbnailUrl": "t"}
        session.put.return_value.json.return_value = {
            "id": 0, "name": "GCForge test", "description": "safe to ignore",
            "guid": "abc-123", "url": "u", "orderId": 0,
            "dateTaken": "2026-08-13T07:37:36.836", "createdDateUtc": "0001-01-01T00:00:00",
        }

        result = log_client.upload_log_image(
            "GC9WVK7", b"\x89PNG", "image/png",
            name="GCForge test", description="safe to ignore", session=session,
        )

        self.assertEqual(result["name"], "GCForge test")
        session.put.assert_called_once()
        args, kwargs = session.put.call_args
        self.assertEqual(args[0], "https://www.geocaching.com/api/live/v1/images/abc-123/replace")
        # Must go through `files=`, not `data=` — a bare data dict would send
        # application/x-www-form-urlencoded instead of the real multipart shape.
        self.assertNotIn("data", kwargs)
        self.assertEqual(kwargs["files"]["name"], (None, "GCForge test"))
        self.assertEqual(kwargs["files"]["description"], (None, "safe to ignore"))


class TestGCWebLogClient(SimpleTestCase):
    """The class wrapper is a thin delegator — one test per method is enough,
    the module-level functions already carry the real shape-pinned tests."""

    def test_satisfies_the_gc_log_client_protocol(self):
        from geocaches.sync.base import GCLogClient
        self.assertIsInstance(log_client.GCWebLogClient(), GCLogClient)

    @patch("geocaches.sync.gc_web.log_client.submit_log")
    def test_submit_log_delegates(self, mock_submit):
        mock_submit.return_value = {"referenceCode": "GL1"}
        session = MagicMock()

        result = log_client.GCWebLogClient(session=session).submit_log(
            "GC9WVK7", "Found it", "2026-08-12T12:00:00.000Z", "hi", use_favourite_point=True,
        )

        self.assertEqual(result, {"referenceCode": "GL1"})
        mock_submit.assert_called_once_with(
            "GC9WVK7", "Found it", "2026-08-12T12:00:00.000Z", "hi",
            use_favourite_point=True, session=session,
        )

    @patch("geocaches.sync.gc_web.log_client.upload_log_image")
    def test_upload_log_image_delegates(self, mock_upload):
        mock_upload.return_value = {"guid": "abc"}
        session = MagicMock()

        result = log_client.GCWebLogClient(session=session).upload_log_image(
            "GC9WVK7", b"data", "image/png", name="n", description="d",
        )

        self.assertEqual(result, {"guid": "abc"})
        mock_upload.assert_called_once_with(
            "GC9WVK7", b"data", "image/png",
            name="n", description="d", filename="image.jpg", session=session,
        )

    @patch("geocaches.sync.gc_web.log_client.delete_log")
    def test_delete_log_delegates(self, mock_delete):
        session = MagicMock()

        log_client.GCWebLogClient(session=session).delete_log("GL1", reason_text="oops")

        mock_delete.assert_called_once_with("GL1", reason_text="oops", session=session)

    @patch("geocaches.sync.gc_web.log_client.attach_images")
    def test_attach_images_delegates(self, mock_attach):
        mock_attach.return_value = {"logReferenceCode": "GL1"}
        session = MagicMock()

        result = log_client.GCWebLogClient(session=session).attach_images(
            "GC9WVK7", "GL1", "Found it", "2026-08-14T12:00:00.000Z", "nice", ["img-guid-1"],
        )

        self.assertEqual(result, {"logReferenceCode": "GL1"})
        mock_attach.assert_called_once_with(
            "GC9WVK7", "GL1", "Found it", "2026-08-14T12:00:00.000Z", "nice",
            ["img-guid-1"], session=session,
        )
