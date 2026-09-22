"""
Tests for ``geocaches.views.gps.send_to_gps`` — the Phase 1 endpoint that
writes the active filter+target queryset to a Garmin device folder.
"""

from __future__ import annotations

import json
import tempfile
from datetime import date
from pathlib import Path

from django.test import TestCase, override_settings
from django.urls import reverse

from geocaches.models import (
    CacheSize,
    CacheStatus,
    CacheType,
    Geocache,
)
from geocaches.tests.fixtures.gps import make_fake_garmin


def _cache(gc_code, lat=48.0, lon=9.0):
    return Geocache.objects.create(
        gc_code=gc_code,
        name=f"Cache {gc_code}",
        cache_type=CacheType.TRADITIONAL,
        size=CacheSize.SMALL,
        status=CacheStatus.ACTIVE,
        latitude=lat,
        longitude=lon,
        difficulty=2.0,
        terrain=2.0,
        hidden_date=date(2020, 1, 1),
        owner="testowner",
    )


@override_settings(KEYRING_ENABLED=False)
class SendToGpsTests(TestCase):
    """Cover the happy path + every error branch in send_to_gps."""

    def setUp(self):
        self.url = reverse("geocaches:send_to_gps")
        self.device_root = make_fake_garmin("oregon_700", register_cleanup=self.addCleanup)

        self.c1 = _cache("GC10001", lat=1.0, lon=1.0)
        self.c2 = _cache("GC10002", lat=2.0, lon=2.0)
        self.c3 = _cache("GC10003", lat=3.0, lon=3.0)

    # --- happy path ---------------------------------------------------------

    def test_writes_gpx_to_garmin_gpx_folder(self):
        response = self.client.post(self.url, {"device_root": str(self.device_root)})
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertTrue(data["ok"])
        self.assertEqual(data["model"], "Oregon 700")
        self.assertEqual(data["count"], 3)
        out_path = Path(data["path"])
        self.assertTrue(out_path.exists())
        self.assertTrue(str(out_path).startswith(str(self.device_root / "Garmin" / "GPX")))
        # Content sanity — exporter should emit the GC codes.
        body = out_path.read_text(encoding="utf-8")
        self.assertIn("GC10001", body)
        self.assertIn("GC10002", body)
        self.assertIn("GC10003", body)

    def test_respects_target_viewport(self):
        # bbox covering only c2
        response = self.client.post(
            self.url + "?target=viewport&vbox=1.5,1.5,2.5,2.5",
            {"device_root": str(self.device_root)},
        )
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertEqual(data["count"], 1)
        body = Path(data["path"]).read_text(encoding="utf-8")
        self.assertIn("GC10002", body)
        self.assertNotIn("GC10001", body)
        self.assertNotIn("GC10003", body)

    def test_respects_target_page_ids(self):
        params = f"?target=page&ids={self.c1.pk},{self.c3.pk}"
        response = self.client.post(self.url + params, {"device_root": str(self.device_root)})
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertEqual(data["count"], 2)
        body = Path(data["path"]).read_text(encoding="utf-8")
        self.assertIn("GC10001", body)
        self.assertIn("GC10003", body)
        self.assertNotIn("GC10002", body)

    def test_custom_filename_honoured(self):
        response = self.client.post(self.url, {
            "device_root": str(self.device_root),
            "filename": "my-trip-2026.gpx",
        })
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertEqual(data["filename"], "my-trip-2026.gpx")
        self.assertTrue(Path(data["path"]).exists())

    def test_filename_appended_with_gpx_extension(self):
        response = self.client.post(self.url, {
            "device_root": str(self.device_root),
            "filename": "trip",  # no extension
        })
        data = json.loads(response.content)
        self.assertEqual(data["filename"], "trip.gpx")

    def test_filename_path_traversal_rejected(self):
        # Attempted ../ injection must NOT escape the GPX folder.
        response = self.client.post(self.url, {
            "device_root": str(self.device_root),
            "filename": "../../evil.gpx",
        })
        data = json.loads(response.content)
        # Falls back to the timestamped default — still inside Garmin/GPX.
        self.assertEqual(response.status_code, 200)
        out = Path(data["path"])
        self.assertTrue(str(out).startswith(str(self.device_root / "Garmin" / "GPX")))

    # --- error branches -----------------------------------------------------

    def test_get_request_rejected(self):
        # Endpoint is POST-only.
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 405)

    def test_missing_device_root_returns_400(self):
        response = self.client.post(self.url, {})
        self.assertEqual(response.status_code, 400)
        self.assertFalse(json.loads(response.content)["ok"])

    def test_nonexistent_device_root_returns_400(self):
        response = self.client.post(self.url, {"device_root": "/no/such/path"})
        self.assertEqual(response.status_code, 400)
        self.assertIn("does not exist", json.loads(response.content)["error"])

    def test_non_garmin_folder_returns_400(self):
        # Folder exists but lacks Garmin/GarminDevice.xml.
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        response = self.client.post(self.url, {"device_root": td.name})
        self.assertEqual(response.status_code, 400)
        self.assertIn("Garmin", json.loads(response.content)["error"])

    def test_empty_qs_returns_400(self):
        # Filter to nothing — bbox far from any test cache.
        response = self.client.post(
            self.url + "?target=viewport&vbox=80,80,90,90",
            {"device_root": str(self.device_root)},
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("No caches", json.loads(response.content)["error"])

    # --- recent-devices side effects ---------------------------------------

    def test_successful_send_records_recent_device(self):
        from preferences.models import UserPreference
        UserPreference.set("recent_gps_devices", [])
        self.client.post(self.url, {"device_root": str(self.device_root)})
        recent = UserPreference.get("recent_gps_devices", [])
        self.assertEqual(len(recent), 1)
        self.assertEqual(recent[0]["path"], str(self.device_root))
        self.assertEqual(recent[0]["label"], "Oregon 700")

    def test_recent_device_is_deduplicated_on_repeat_send(self):
        from preferences.models import UserPreference
        UserPreference.set("recent_gps_devices", [])
        self.client.post(self.url, {"device_root": str(self.device_root)})
        self.client.post(self.url, {"device_root": str(self.device_root)})
        recent = UserPreference.get("recent_gps_devices", [])
        self.assertEqual(len(recent), 1)


class GpsDetectDevicesEndpointTests(TestCase):
    """The JSON endpoint that powers the dropdown's auto-detect entry."""

    def setUp(self):
        self.url = reverse("geocaches:gps_detect_devices")

    def test_no_devices_returns_empty_list(self):
        from unittest.mock import patch
        with patch("geocaches.views.gps.detect_garmin_devices", return_value=[]):
            response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(json.loads(response.content), {"devices": []})

    def test_returns_detected_devices_with_metadata(self):
        from unittest.mock import patch

        from geocaches.services.gps_device import GarminDevice
        fake = [
            GarminDevice(
                model="Oregon 700",
                software_version="510",
                display_name="Oregon 700",
                mount_path="G:\\",
            ),
            GarminDevice(
                model="GPSMap 64sx",
                software_version="6.30",
                mount_path="H:\\",
            ),
        ]
        with patch("geocaches.views.gps.detect_garmin_devices", return_value=fake):
            response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertEqual(len(data["devices"]), 2)
        self.assertEqual(data["devices"][0]["label"], "Oregon 700")
        self.assertEqual(data["devices"][0]["path"], "G:\\")
        self.assertEqual(data["devices"][0]["software_version"], "510")
        self.assertEqual(data["devices"][1]["label"], "GPSMap 64sx")


class GpsRecentDevicesEndpointTests(TestCase):
    """The JSON endpoint that powers the dropdown's recent list."""

    def setUp(self):
        self.url = reverse("geocaches:gps_recent_devices")

    def test_empty_pref_returns_empty_list(self):
        from preferences.models import UserPreference
        UserPreference.set("recent_gps_devices", [])
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(json.loads(response.content), {"devices": []})

    def test_returns_persisted_devices(self):
        from preferences.models import UserPreference
        UserPreference.set("recent_gps_devices", [
            {"path": "G:\\", "label": "Oregon 700", "date": "2026-01-01 12:00"},
            {"path": "/Volumes/GARMIN", "label": "GPSMap 64", "date": "2026-01-02 09:30"},
        ])
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertEqual(len(data["devices"]), 2)
        self.assertEqual(data["devices"][0]["label"], "Oregon 700")
