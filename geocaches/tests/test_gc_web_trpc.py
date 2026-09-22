"""
Tests for geocaches.sync.gc_web.trpc — envelope encode/decode plus the
query()/mutate() request wrappers against a mocked session (no live GC calls).

Shapes exercised here are pinned to a real, live-captured
web.logs.createGeocacheLog request/response (HAR capture, 2026-08-13) — see
the request/response bodies quoted in trpc.py's module docstring.
"""

from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from geocaches.sync.gc_web import trpc


class TestEncodeInput(SimpleTestCase):
    def test_wraps_data_in_single_call_envelope(self):
        self.assertEqual(trpc._encode_input({"a": 1}), {"0": {"a": 1}})

    def test_none_becomes_empty_dict(self):
        self.assertEqual(trpc._encode_input(None), {"0": {}})


class TestDecodeResponse(SimpleTestCase):
    def test_unwraps_batch_array_result_data(self):
        # Real shape captured live: top-level array, no "json" sub-key.
        payload = [{"result": {"data": {"logReferenceCode": "GL1H4NGRR"}}}]
        self.assertEqual(trpc._decode_response(payload), {"logReferenceCode": "GL1H4NGRR"})

    def test_missing_data_key_returns_none(self):
        # Real deleteGeocacheLog response: {"result": {}}, no "data" key at all.
        payload = [{"result": {}}]
        self.assertIsNone(trpc._decode_response(payload))

    def test_raises_trpc_error_on_unexpected_shape(self):
        with self.assertRaises(trpc.TRPCError):
            trpc._decode_response({"error": {"message": "nope"}})

    def test_raises_trpc_error_on_non_list(self):
        with self.assertRaises(trpc.TRPCError):
            trpc._decode_response(None)

    def test_raises_trpc_error_on_empty_list(self):
        with self.assertRaises(trpc.TRPCError):
            trpc._decode_response([])


class TestFetchCsrfToken(SimpleTestCase):
    def test_extracts_token_from_next_data(self):
        session = MagicMock()
        session.get.return_value.text = '...{"csrfToken":"abc123"}...'
        token = trpc.fetch_csrf_token(session, "https://www.geocaching.com/live/geocache/GC123/log")
        self.assertEqual(token, "abc123")
        session.get.return_value.raise_for_status.assert_called_once()

    def test_raises_when_token_missing(self):
        session = MagicMock()
        session.get.return_value.text = "<html>no token here</html>"
        with self.assertRaises(trpc.TRPCError):
            trpc.fetch_csrf_token(session, "https://www.geocaching.com/live/geocache/GC123/log")


class TestQuery(SimpleTestCase):
    @patch("geocaches.sync.gc_web.trpc.polite_delay")
    def test_issues_get_with_batch_and_url_encoded_envelope(self, _delay):
        session = MagicMock()
        session.get.return_value.json.return_value = [{"result": {"data": {"x": 1}}}]

        result = trpc.query(session, "web.geocache.get", {"code": "GC123"})

        self.assertEqual(result, {"x": 1})
        session.get.assert_called_once()
        (url,), kwargs = session.get.call_args
        self.assertTrue(url.startswith(f"{trpc.BASE_URL}/web.geocache.get?batch=1&input="))
        self.assertEqual(kwargs["headers"]["Accept"], "application/json")
        session.get.return_value.raise_for_status.assert_called_once()

    @patch("geocaches.sync.gc_web.trpc.polite_delay")
    def test_waits_politely_before_request(self, delay):
        session = MagicMock()
        session.get.return_value.json.return_value = [{"result": {"data": None}}]
        trpc.query(session, "web.geocache.get")
        delay.assert_called_once()


class TestMutate(SimpleTestCase):
    @patch("geocaches.sync.gc_web.trpc.polite_delay")
    def test_issues_post_with_batch_and_envelope_body(self, _delay):
        session = MagicMock()
        session.post.return_value.json.return_value = [
            {"result": {"data": {"logReferenceCode": "GL1234"}}}
        ]

        result = trpc.mutate(session, "web.logs.createGeocacheLog", {"logText": "hi"})

        self.assertEqual(result, {"logReferenceCode": "GL1234"})
        session.post.assert_called_once()
        args, kwargs = session.post.call_args
        self.assertEqual(args[0], f"{trpc.BASE_URL}/web.logs.createGeocacheLog?batch=1")
        self.assertEqual(kwargs["json"], {"0": {"logText": "hi"}})
        session.post.return_value.raise_for_status.assert_called_once()

    @patch("geocaches.sync.gc_web.trpc.polite_delay")
    def test_csrf_token_sets_confirmed_header(self, _delay):
        session = MagicMock()
        session.post.return_value.json.return_value = [{"result": {"data": None}}]

        trpc.mutate(session, "web.logs.deleteGeocacheLog", csrf_token="tok123")

        _, kwargs = session.post.call_args
        self.assertEqual(kwargs["headers"][trpc.CSRF_HEADER], "tok123")

    @patch("geocaches.sync.gc_web.trpc.polite_delay")
    def test_extra_headers_are_merged_in(self, _delay):
        session = MagicMock()
        session.post.return_value.json.return_value = [{"result": {"data": None}}]

        trpc.mutate(session, "web.logs.deleteGeocacheLog", extra_headers={"X-Custom": "abc"})

        _, kwargs = session.post.call_args
        self.assertEqual(kwargs["headers"]["X-Custom"], "abc")
        self.assertEqual(kwargs["headers"]["Content-Type"], "application/json")
