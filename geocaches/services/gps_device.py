"""
Garmin device detection and manifest parsing.

Garmin handhelds in mass-storage mode expose a manifest at
``<root>/Garmin/GarminDevice.xml`` describing the device model and the file
paths it expects (GPX folder, field notes path, etc.). This module reads
that manifest and returns a structured representation that the Send-to-GPS
feature uses to write files to the right place.

See ``docs/reference/gsak-send-to-gps.md`` for the source of these design
choices.
"""

from __future__ import annotations

import os
import string
import sys
from dataclasses import dataclass, field
from pathlib import Path
from xml.etree import ElementTree

NS = "{http://www.garmin.com/xmlschemas/GarminDevice/v2}"
DEFAULT_GPX_FOLDER = "Garmin/GPX"


@dataclass
class GarminDevice:
    """Parsed contents of a ``GarminDevice.xml`` manifest.

    All paths in this dataclass are *relative to the device root* — exactly
    as the manifest declares them. Callers join them with the mounted root
    path to get the absolute filesystem path.
    """

    model: str
    software_version: str = ""
    part_number: str = ""
    display_name: str = ""
    gpx_folder: str = DEFAULT_GPX_FOLDER
    fieldnotes_path: str | None = None
    supports_ggz: bool = False
    raw_data_types: list[str] = field(default_factory=list)
    mount_path: str = ""

    @property
    def label(self) -> str:
        """Human-readable name for UI display."""
        return self.display_name or self.model


def parse_garmin_device_xml(xml_path: str | Path) -> GarminDevice | None:
    """Parse a ``GarminDevice.xml`` file at ``xml_path``.

    Returns ``None`` for any failure (file missing, malformed XML, missing
    required fields). Callers can rely on the truthiness of the result —
    no exceptions propagate.
    """
    try:
        tree = ElementTree.parse(xml_path)
    except (FileNotFoundError, ElementTree.ParseError, OSError):
        return None

    root = tree.getroot()
    model_el = root.find(f"{NS}Model")
    if model_el is None:
        return None
    description = (model_el.findtext(f"{NS}Description") or "").strip()
    if not description:
        # Without a model name we can't identify the device — treat as invalid.
        return None

    software_version = (model_el.findtext(f"{NS}SoftwareVersion") or "").strip()
    part_number = (model_el.findtext(f"{NS}PartNumber") or "").strip()
    display_name = (root.findtext(f"{NS}DisplayName") or "").strip()

    # Walk MassStorageMode/DataType entries to find file paths.
    gpx_folder = DEFAULT_GPX_FOLDER
    fieldnotes_path: str | None = None
    supports_ggz = False
    raw_data_types: list[str] = []

    for dt in root.iterfind(f"{NS}MassStorageMode/{NS}DataType"):
        name = (dt.findtext(f"{NS}Name") or "").strip()
        if name:
            raw_data_types.append(name)
        if name in ("GPSData", "Geocaches"):
            path = dt.findtext(f"{NS}File/{NS}Location/{NS}Path")
            if path:
                gpx_folder = path.strip()
        elif name == "FieldNotes":
            path = dt.findtext(f"{NS}File/{NS}Location/{NS}Path")
            if path:
                fieldnotes_path = path.strip()
        elif name == "GGZ":
            supports_ggz = True

    return GarminDevice(
        model=description,
        software_version=software_version,
        part_number=part_number,
        display_name=display_name,
        gpx_folder=gpx_folder,
        fieldnotes_path=fieldnotes_path,
        supports_ggz=supports_ggz,
        raw_data_types=raw_data_types,
    )


def detect_garmin_at_path(root_path: str | Path) -> GarminDevice | None:
    """Return a ``GarminDevice`` if ``root_path`` looks like a mounted Garmin.

    Looks for ``<root>/Garmin/GarminDevice.xml``. Returns ``None`` if the
    folder doesn't exist, the manifest is missing, or the manifest is
    invalid. Sets ``mount_path`` on the returned object.
    """
    root = Path(root_path)
    manifest = root / "Garmin" / "GarminDevice.xml"
    if not manifest.is_file():
        return None
    device = parse_garmin_device_xml(manifest)
    if device is not None:
        device.mount_path = str(root)
    return device


def candidate_mount_paths() -> list[Path]:
    """Return platform-specific list of paths likely to host removable media.

    Windows: drive letters A: through Z:.
    macOS:   subdirectories of /Volumes.
    Linux:   subdirectories of /media/<user>/ and /run/media/<user>/.

    Symlinks and non-directories are filtered out. The list is best-effort
    — it may include paths that aren't actually mounted (Windows) or miss
    unusual mount points (custom fstab entries on Linux).
    """
    if sys.platform == "win32":
        return [Path(f"{letter}:/") for letter in string.ascii_uppercase]
    if sys.platform == "darwin":
        vols = Path("/Volumes")
        if not vols.is_dir():
            return []
        return [p for p in vols.iterdir() if p.is_dir()]
    # Linux + other Unix
    paths: list[Path] = []
    for base in (Path("/media"), Path("/run/media")):
        if not base.is_dir():
            continue
        try:
            for user_dir in base.iterdir():
                if not user_dir.is_dir():
                    continue
                try:
                    paths.extend(p for p in user_dir.iterdir() if p.is_dir())
                except OSError:
                    continue
        except OSError:
            continue
    # Also probe USER's own /media/<user>/* if running as that user
    user = os.environ.get("USER") or os.environ.get("USERNAME")
    if user:
        for base in (Path("/media") / user, Path("/run/media") / user):
            if base.is_dir() and base not in paths:
                try:
                    paths.extend(p for p in base.iterdir() if p.is_dir() and p not in paths)
                except OSError:
                    continue
    return paths


def detect_garmin_devices(*, mount_paths=None) -> list[GarminDevice]:
    """Scan likely mount points for connected Garmin handhelds.

    Returns a list of ``GarminDevice`` objects, each with ``mount_path``
    populated. Empty list when nothing is found. Tests can pass an
    explicit ``mount_paths`` iterable to bypass the platform scan.
    """
    paths = list(mount_paths) if mount_paths is not None else candidate_mount_paths()
    found: list[GarminDevice] = []
    for path in paths:
        device = detect_garmin_at_path(path)
        if device is not None:
            found.append(device)
    return found
