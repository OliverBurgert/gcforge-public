"""
Tests for the public code that drives the official GC client through its
semantic methods (get_cache_logs_page, upload_trackable_log_image) rather than
raw requests. The client is a fake — no gcprivate dependency, no network.
"""

from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase, TestCase

from geocaches.image_upload import ImageAttachment, upload_images_to_gc_trackable_log
from geocaches.models import CacheSize, CacheStatus, CacheType, Geocache
from geocaches.sync import log_fetch


def _log(ref):
    return {
        "log_type": "Write note", "logged_date": "2026-01-02", "user_name": "u",
        "user_id": "1", "text": "t", "source_id": ref, "source": "gc",
    }


class FetchAllGcLogsPagingTests(TestCase):
    def setUp(self):
        Geocache.objects.create(
            gc_code="GC1PAGE", name="C", cache_type=CacheType.TRADITIONAL,
            size=CacheSize.MICRO, status=CacheStatus.ACTIVE,
            latitude=48.0, longitude=9.0, difficulty=2.0, terrain=2.0,
        )

    def test_pages_on_raw_count_not_normalised_length(self):
        """A full page whose undated rows were dropped must still advance to the next page."""
        client = MagicMock()
        full_page = [_log(f"GLA{i}") for i in range(log_fetch._BATCH_SIZE - 1)]
        client.get_cache_logs_page.side_effect = [
            (full_page, log_fetch._BATCH_SIZE),
            ([_log("GLB0")], 1),
        ]

        saved = log_fetch.fetch_all_gc_logs(client, "GC1PAGE")

        self.assertEqual(saved, log_fetch._BATCH_SIZE)
        self.assertEqual(client.get_cache_logs_page.call_count, 2)
        self.assertEqual(client.get_cache_logs_page.call_args.kwargs["skip"], log_fetch._BATCH_SIZE)


@patch("geocaches.image_upload.process_image", return_value=(b"processed", "image/jpeg"))
class TrackableLogImageUploadTests(SimpleTestCase):
    def _att(self):
        return ImageAttachment(file_bytes=b"raw", filename="a.jpg", title="T", description="D")

    def test_uploads_through_the_client(self, _proc):
        client = MagicMock()
        client.upload_trackable_log_image.return_value = {"url": "https://img/a.jpg"}

        [res] = upload_images_to_gc_trackable_log(client, "TL1", [self._att()])

        self.assertTrue(res.ok)
        self.assertEqual(res.gc_url, "https://img/a.jpg")
        client.upload_trackable_log_image.assert_called_once_with(
            "TL1", b"processed", "image/jpeg", name="T", description="D",
        )

    def test_web_client_without_upload_reports_an_error(self, _proc):
        client = object()  # the website trackable client has no image upload

        [res] = upload_images_to_gc_trackable_log(client, "TL1", [self._att()])

        self.assertFalse(res.ok)
        self.assertIn("not supported", res.error)

    def test_upload_exception_is_captured(self, _proc):
        client = MagicMock()
        client.upload_trackable_log_image.side_effect = RuntimeError("TB image upload failed (400)")

        [res] = upload_images_to_gc_trackable_log(client, "TL1", [self._att()])

        self.assertFalse(res.ok)
        self.assertIn("400", res.error)
