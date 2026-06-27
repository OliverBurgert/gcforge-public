"""Tests for corrected coordinates save view."""
from datetime import date

from django.test import TestCase, RequestFactory
from django.contrib.messages.storage.fallback import FallbackStorage

from geocaches.models import (
    CacheSize, CacheStatus, CacheType, CorrectedCoordinates, Geocache,
)
from geocaches.views.notes_logs import corrected_coords_save


def _make_cache(gc_code="GC00001"):
    return Geocache.objects.create(
        gc_code=gc_code, name="Test",
        cache_type=CacheType.TRADITIONAL, size=CacheSize.SMALL,
        status=CacheStatus.ACTIVE, latitude=48.0, longitude=9.0,
        difficulty=2.0, terrain=2.0, hidden_date=date(2020, 1, 1),
        owner="test",
    )


def _post(factory, path, data):
    req = factory.post(path, data)
    req.session = "session"
    req._messages = FallbackStorage(req)
    return req


class TestCorrectedCoordsSave(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.cache = _make_cache()

    def test_saves_valid_coords(self):
        req = _post(self.factory, "/", {"latitude": "48.30315", "longitude": "9.12345"})
        resp = corrected_coords_save(req, "GC00001")
        self.assertEqual(resp.status_code, 302)
        cc = CorrectedCoordinates.objects.get(geocache=self.cache)
        self.assertAlmostEqual(cc.latitude, 48.30315, places=4)

    def test_invalid_coords_shows_error(self):
        req = _post(self.factory, "/", {"latitude": "not a coord", "longitude": "also bad"})
        resp = corrected_coords_save(req, "GC00001")
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(CorrectedCoordinates.objects.filter(geocache=self.cache).exists())
        msgs = list(req._messages)
        self.assertEqual(len(msgs), 1)
        self.assertIn("Could not parse", str(msgs[0]))

    def test_clear_removes_coords(self):
        CorrectedCoordinates.objects.create(
            geocache=self.cache, latitude=48.0, longitude=9.0,
        )
        self.cache.has_corrected_coordinates = True
        self.cache.save(update_fields=["has_corrected_coordinates"])
        req = _post(self.factory, "/", {"clear": "1"})
        corrected_coords_save(req, "GC00001")
        self.assertFalse(CorrectedCoordinates.objects.filter(geocache=self.cache).exists())
