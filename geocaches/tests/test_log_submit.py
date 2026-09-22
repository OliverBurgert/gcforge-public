"""
Tests for geocaches.sync.log_submit's image-attach branching — the official
API attaches each image to an already-created log ref, but the web backend
has to upload images first and attach guids via update_log() (see
geocaches.sync.gc_web.log_client.attach_images()). No live network calls.
"""

from datetime import datetime, timezone
from unittest.mock import patch

from django.test import TestCase

from geocaches.image_upload import ImageAttachment
from geocaches.models import CacheSize, CacheStatus, CacheType, Geocache
from geocaches.sync import log_submit


def _cache(gc_code="GC9WVK7", **kw):
    defaults = dict(
        name="C", cache_type=CacheType.TRADITIONAL, size=CacheSize.MICRO,
        status=CacheStatus.ACTIVE, latitude=48.0, longitude=9.0,
        difficulty=2.0, terrain=2.0,
    )
    defaults.update(kw)
    return Geocache.objects.create(gc_code=gc_code, **defaults)


def _image(name="photo.jpg"):
    return ImageAttachment(file_bytes=b"fake", filename=name, title="t", description="d")


class TestSubmitLogImageAttach(TestCase):
    @patch("geocaches.image_upload.process_image", return_value=(b"processed", "image/jpeg"))
    @patch("geocaches.sync.gc_web.log_client.attach_images")
    @patch("geocaches.sync.gc_web.log_client.upload_log_image")
    @patch("geocaches.sync.gc_access.submit_gc_log")
    def test_web_backend_uploads_then_attaches_guids(
        self, mock_submit, mock_upload, mock_attach, _process,
    ):
        cache = _cache()
        mock_submit.return_value = ("gc_web", {"referenceCode": "GL_WEB"})
        mock_upload.return_value = {"guid": "img-guid-1"}

        result = log_submit.submit_log(
            cache, "Found it", datetime(2026, 8, 14, tzinfo=timezone.utc), "nice",
            ["gc"], images=[_image()],
        )

        self.assertTrue(result.gc_success)
        self.assertEqual(result.image_errors, [])
        mock_upload.assert_called_once_with(
            "GC9WVK7", b"processed", "image/jpeg", name="t", description="d",
        )
        mock_attach.assert_called_once_with(
            "GC9WVK7", "GL_WEB", "Found it",
            "2026-08-14T00:00:00.000Z", "nice", ["img-guid-1"],
        )

    @patch("geocaches.image_upload.process_image", return_value=(b"processed", "image/jpeg"))
    @patch("geocaches.sync.gc_web.log_client.attach_images")
    @patch("geocaches.sync.gc_access.submit_gc_log")
    def test_api_backend_uses_the_official_per_image_attach(
        self, mock_submit, mock_attach, _process,
    ):
        cache = _cache()
        mock_submit.return_value = ("gc_api", {"referenceCode": "GL_API"})

        with patch("geocaches.sync.gc_access.get_gc_client") as MockClient:
            result = log_submit.submit_log(
                cache, "Found it", datetime(2026, 8, 14, tzinfo=timezone.utc), "nice",
                ["gc"], images=[_image()],
            )

        self.assertTrue(result.gc_success)
        MockClient.return_value.upload_log_image.assert_called_once_with(
            "GL_API", b"processed", "image/jpeg", name="t", description="d",
        )
        mock_attach.assert_not_called()

    @patch("geocaches.image_upload.process_image", return_value=(b"processed", "image/jpeg"))
    @patch("geocaches.sync.gc_web.log_client.attach_images")
    @patch("geocaches.sync.gc_web.log_client.upload_log_image", side_effect=RuntimeError("upload failed"))
    @patch("geocaches.sync.gc_access.submit_gc_log")
    def test_web_upload_failure_is_recorded_but_log_submission_still_succeeds(
        self, mock_submit, _mock_upload, mock_attach, _process,
    ):
        cache = _cache()
        mock_submit.return_value = ("gc_web", {"referenceCode": "GL_WEB"})

        result = log_submit.submit_log(
            cache, "Found it", datetime(2026, 8, 14, tzinfo=timezone.utc), "nice",
            ["gc"], images=[_image()],
        )

        self.assertTrue(result.gc_success)  # the log itself still went through
        self.assertEqual(len(result.image_errors), 1)
        self.assertIn("upload failed", result.image_errors[0])
        mock_attach.assert_not_called()  # nothing to attach — no guids collected

    @patch("geocaches.sync.gc_access.submit_gc_log")
    def test_no_images_does_not_touch_either_image_path(self, mock_submit):
        cache = _cache()
        mock_submit.return_value = ("gc_web", {"referenceCode": "GL_WEB"})

        with patch("geocaches.sync.gc_web.log_client.upload_log_image") as mock_upload:
            result = log_submit.submit_log(
                cache, "Found it", datetime(2026, 8, 14, tzinfo=timezone.utc), "nice", ["gc"],
            )

        self.assertTrue(result.gc_success)
        mock_upload.assert_not_called()
