"""
Tests for ``geocaches.services.gps_device`` — Garmin manifest parsing and
device detection from a mounted root path.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

from django.test import SimpleTestCase

from geocaches.services.gps_device import (
    DEFAULT_GPX_FOLDER,
    GarminDevice,
    candidate_mount_paths,
    detect_garmin_at_path,
    detect_garmin_devices,
    parse_garmin_device_xml,
)
from geocaches.tests.fixtures.gps import make_fake_garmin


# A representative GarminDevice.xml from a modern Oregon-class handheld.
# Only the fields we read are populated; layout matches what real devices emit.
FULL_MANIFEST_XML = """<?xml version="1.0" encoding="UTF-8"?>
<Device xmlns="http://www.garmin.com/xmlschemas/GarminDevice/v2">
  <Model>
    <PartNumber>006-B1853-00</PartNumber>
    <SoftwareVersion>510</SoftwareVersion>
    <Description>Oregon 700</Description>
  </Model>
  <Id>3979634841</Id>
  <DisplayName>Oregon 700</DisplayName>
  <MassStorageMode>
    <DataType>
      <Name>GPSData</Name>
      <File>
        <Specification>
          <Identifier>http://www.topografix.com/GPX/1/1</Identifier>
        </Specification>
        <Location>
          <Path>Garmin/GPX</Path>
          <FileExtension>gpx</FileExtension>
        </Location>
        <TransferDirection>InputToUnit</TransferDirection>
      </File>
    </DataType>
    <DataType>
      <Name>GGZ</Name>
      <File>
        <Location>
          <Path>Garmin/GGZ</Path>
        </Location>
      </File>
    </DataType>
    <DataType>
      <Name>FieldNotes</Name>
      <File>
        <Location>
          <Path>Garmin/geocache_visits.txt</Path>
        </Location>
        <TransferDirection>OutputFromUnit</TransferDirection>
      </File>
    </DataType>
  </MassStorageMode>
</Device>
"""

MINIMAL_MANIFEST_XML = """<?xml version="1.0" encoding="UTF-8"?>
<Device xmlns="http://www.garmin.com/xmlschemas/GarminDevice/v2">
  <Model>
    <Description>eTrex Legacy</Description>
  </Model>
</Device>
"""

MISSING_MODEL_XML = """<?xml version="1.0" encoding="UTF-8"?>
<Device xmlns="http://www.garmin.com/xmlschemas/GarminDevice/v2">
  <Id>123</Id>
  <DisplayName>Some Garmin</DisplayName>
</Device>
"""

EMPTY_DESCRIPTION_XML = """<?xml version="1.0" encoding="UTF-8"?>
<Device xmlns="http://www.garmin.com/xmlschemas/GarminDevice/v2">
  <Model>
    <Description></Description>
    <SoftwareVersion>200</SoftwareVersion>
  </Model>
</Device>
"""

MALFORMED_XML = "<Device><Model><Description>Oregon</Description"  # truncated


class TestParseGarminDeviceXml(SimpleTestCase):
    """Direct unit tests on the XML parser."""

    def _write_tmp(self, body: str) -> Path:
        f = tempfile.NamedTemporaryFile(mode="w", suffix=".xml", delete=False, encoding="utf-8")
        f.write(body)
        f.close()
        path = Path(f.name)
        self.addCleanup(path.unlink, missing_ok=True)
        return path

    def test_full_manifest_extracts_all_fields(self):
        path = self._write_tmp(FULL_MANIFEST_XML)
        device = parse_garmin_device_xml(path)
        self.assertIsNotNone(device)
        assert device is not None  # for type-checkers
        self.assertEqual(device.model, "Oregon 700")
        self.assertEqual(device.software_version, "510")
        self.assertEqual(device.part_number, "006-B1853-00")
        self.assertEqual(device.display_name, "Oregon 700")
        self.assertEqual(device.gpx_folder, "Garmin/GPX")
        self.assertEqual(device.fieldnotes_path, "Garmin/geocache_visits.txt")
        self.assertTrue(device.supports_ggz)
        self.assertIn("GPSData", device.raw_data_types)
        self.assertIn("FieldNotes", device.raw_data_types)
        self.assertIn("GGZ", device.raw_data_types)

    def test_minimal_manifest_uses_defaults(self):
        # Older devices may not declare a MassStorageMode block at all —
        # we should still identify them by Model/Description and fall back
        # to Garmin/GPX as the canonical folder.
        path = self._write_tmp(MINIMAL_MANIFEST_XML)
        device = parse_garmin_device_xml(path)
        self.assertIsNotNone(device)
        assert device is not None
        self.assertEqual(device.model, "eTrex Legacy")
        self.assertEqual(device.gpx_folder, DEFAULT_GPX_FOLDER)
        self.assertIsNone(device.fieldnotes_path)
        self.assertFalse(device.supports_ggz)

    def test_missing_model_returns_none(self):
        # Without a Model/Description we can't identify the device.
        path = self._write_tmp(MISSING_MODEL_XML)
        self.assertIsNone(parse_garmin_device_xml(path))

    def test_empty_description_returns_none(self):
        path = self._write_tmp(EMPTY_DESCRIPTION_XML)
        self.assertIsNone(parse_garmin_device_xml(path))

    def test_malformed_xml_returns_none(self):
        path = self._write_tmp(MALFORMED_XML)
        self.assertIsNone(parse_garmin_device_xml(path))

    def test_missing_file_returns_none(self):
        # Path that does not exist — must not raise.
        self.assertIsNone(parse_garmin_device_xml("/no/such/file/GarminDevice.xml"))

    def test_label_prefers_display_name(self):
        d = GarminDevice(model="Oregon 700", display_name="My Oregon")
        self.assertEqual(d.label, "My Oregon")

    def test_label_falls_back_to_model(self):
        d = GarminDevice(model="Oregon 700")
        self.assertEqual(d.label, "Oregon 700")


class TestDetectGarminAtPath(SimpleTestCase):
    """Tests for the path-rooted detector that mirrors how a mounted device
    is laid out: ``<root>/Garmin/GarminDevice.xml``."""

    def _make_device_root(self, manifest_xml: str | None) -> Path:
        """Create a temp root with optional Garmin/GarminDevice.xml inside."""
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        root = Path(td.name)
        if manifest_xml is not None:
            (root / "Garmin").mkdir()
            (root / "Garmin" / "GarminDevice.xml").write_text(manifest_xml, encoding="utf-8")
        return root

    def test_valid_garmin_root_returns_device(self):
        root = self._make_device_root(FULL_MANIFEST_XML)
        device = detect_garmin_at_path(root)
        self.assertIsNotNone(device)
        assert device is not None
        self.assertEqual(device.model, "Oregon 700")

    def test_missing_garmin_subfolder_returns_none(self):
        root = self._make_device_root(None)
        self.assertIsNone(detect_garmin_at_path(root))

    def test_garmin_subfolder_without_manifest_returns_none(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        root = Path(td.name)
        (root / "Garmin").mkdir()  # no GarminDevice.xml inside
        self.assertIsNone(detect_garmin_at_path(root))

    def test_nonexistent_path_returns_none(self):
        self.assertIsNone(detect_garmin_at_path("/no/such/root/at/all"))

    def test_malformed_manifest_returns_none(self):
        root = self._make_device_root(MALFORMED_XML)
        self.assertIsNone(detect_garmin_at_path(root))

    def test_mount_path_set_on_returned_device(self):
        root = self._make_device_root(FULL_MANIFEST_XML)
        device = detect_garmin_at_path(root)
        assert device is not None
        self.assertEqual(device.mount_path, str(root))


class TestCandidateMountPaths(SimpleTestCase):
    """Platform-specific mount-path enumeration. Patches sys.platform to
    exercise each branch deterministically."""

    def test_windows_returns_drive_letters(self):
        with patch.object(sys, "platform", "win32"):
            paths = candidate_mount_paths()
        # All 26 letters, with trailing colon-slash
        self.assertEqual(len(paths), 26)
        self.assertEqual(paths[0], Path("A:/"))
        self.assertEqual(paths[-1], Path("Z:/"))

    def test_macos_scans_volumes(self):
        # Patch /Volumes to a fake structure with two subfolders
        with tempfile.TemporaryDirectory() as td:
            volumes = Path(td) / "Volumes"
            volumes.mkdir()
            (volumes / "GARMIN").mkdir()
            (volumes / "Macintosh HD").mkdir()
            (volumes / "not-a-dir.txt").write_text("x")  # non-dir is filtered
            with patch.object(sys, "platform", "darwin"), \
                 patch("geocaches.services.gps_device.Path") as MockPath:
                # Make Path("/Volumes") resolve to our temp /Volumes
                def _path(arg):
                    return volumes if str(arg) == "/Volumes" else Path(arg)
                MockPath.side_effect = _path
                paths = candidate_mount_paths()
        names = sorted(p.name for p in paths)
        self.assertEqual(names, ["GARMIN", "Macintosh HD"])

    def test_linux_scans_media_and_run_media(self):
        # /media/<user>/<vol> + /run/media/<user>/<vol>
        with tempfile.TemporaryDirectory() as td:
            tdp = Path(td)
            (tdp / "media" / "alice" / "GARMIN").mkdir(parents=True)
            (tdp / "run" / "media" / "alice" / "USB").mkdir(parents=True)
            with patch.object(sys, "platform", "linux"), \
                 patch.dict("os.environ", {"USER": "alice"}, clear=False), \
                 patch("geocaches.services.gps_device.Path") as MockPath:
                def _path(arg):
                    s = str(arg)
                    if s == "/media":
                        return tdp / "media"
                    if s == "/run/media":
                        return tdp / "run" / "media"
                    if s.startswith("/media/"):
                        return tdp / "media" / s[len("/media/"):]
                    if s.startswith("/run/media/"):
                        return tdp / "run" / "media" / s[len("/run/media/"):]
                    return Path(arg)
                MockPath.side_effect = _path
                paths = candidate_mount_paths()
        names = sorted(p.name for p in paths)
        self.assertEqual(names, ["GARMIN", "USB"])

    def test_linux_no_media_returns_empty(self):
        with patch.object(sys, "platform", "linux"), \
             patch("geocaches.services.gps_device.Path") as MockPath:
            # All probed Path objects say is_dir() = False
            class _FakePath:
                def __init__(self, *a, **k): pass
                def is_dir(self): return False
                def __truediv__(self, other): return _FakePath()
            MockPath.side_effect = lambda *a, **k: _FakePath()
            paths = candidate_mount_paths()
        self.assertEqual(paths, [])


class TestDetectGarminDevices(SimpleTestCase):
    """Cross-platform detector that walks candidate mount paths."""

    def test_returns_devices_at_given_paths(self):
        # Two fake devices, one valid path that's not a Garmin
        d1 = make_fake_garmin("oregon_700", register_cleanup=self.addCleanup)
        d2 = make_fake_garmin("etrex_legacy", register_cleanup=self.addCleanup)
        bogus = tempfile.TemporaryDirectory()
        self.addCleanup(bogus.cleanup)

        devices = detect_garmin_devices(mount_paths=[d1, d2, Path(bogus.name)])
        self.assertEqual(len(devices), 2)
        labels = sorted(d.label for d in devices)
        self.assertEqual(labels, ["Oregon 700", "eTrex Legacy"])
        # mount_path is populated for each
        self.assertEqual(devices[0].mount_path, str(d1) if devices[0].label == "Oregon 700" else str(d2))

    def test_empty_paths_returns_empty(self):
        self.assertEqual(detect_garmin_devices(mount_paths=[]), [])

    def test_no_devices_among_candidates(self):
        bogus = tempfile.TemporaryDirectory()
        self.addCleanup(bogus.cleanup)
        self.assertEqual(detect_garmin_devices(mount_paths=[Path(bogus.name)]), [])

    def test_skips_paths_that_dont_exist(self):
        # Non-existent paths must not raise
        d1 = make_fake_garmin("oregon_700", register_cleanup=self.addCleanup)
        devices = detect_garmin_devices(mount_paths=[Path("/no/such/path"), d1])
        self.assertEqual(len(devices), 1)
        self.assertEqual(devices[0].label, "Oregon 700")
