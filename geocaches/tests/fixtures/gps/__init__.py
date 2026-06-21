"""
Mocked Garmin device fixtures for tests.

Each manifest in ``manifests/*.xml`` is a fresh copy of (or representative
sample for) a real GarminDevice.xml. Use ``make_fake_garmin(manifest_name)``
to build a tempdir on disk that mimics the layout of a mounted Garmin
device:

    <tempdir>/Garmin/GarminDevice.xml          ← copied from manifests/
    <tempdir>/Garmin/GPX/                      ← created empty

The temp directory is registered for cleanup with the calling test case.

The same manifest files double as a manual-testing aid: copy any of them
into a real folder named ``Garmin/`` somewhere on disk and that folder
will pass GCForge's Garmin-device check, letting you exercise the
Send-to-GPS flow without an actual device.
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

MANIFESTS_DIR = Path(__file__).parent / "manifests"


def manifest_path(name: str) -> Path:
    """Absolute path to a fixture manifest by short name (e.g. ``oregon_700``)."""
    p = MANIFESTS_DIR / f"{name}.xml"
    if not p.is_file():
        raise FileNotFoundError(
            f"No fixture manifest named '{name}' under {MANIFESTS_DIR}"
        )
    return p


def make_fake_garmin(manifest_name: str = "oregon_700", *, register_cleanup=None) -> Path:
    """Materialise a tempdir that looks like a mounted Garmin device.

    Parameters
    ----------
    manifest_name :
        Short name of a manifest file under ``manifests/`` (without the
        ``.xml`` extension). Defaults to ``oregon_700``.
    register_cleanup :
        Optional callable that takes a zero-arg cleanup function. Pass
        ``self.addCleanup`` from a TestCase to have the tempdir torn down
        automatically. If omitted, the caller is responsible for cleanup.

    Returns
    -------
    Path
        The root path of the fake device.
    """
    src = manifest_path(manifest_name)
    td = tempfile.TemporaryDirectory()
    if register_cleanup is not None:
        register_cleanup(td.cleanup)
    root = Path(td.name)
    garmin_dir = root / "Garmin"
    garmin_dir.mkdir()
    (garmin_dir / "GPX").mkdir()
    shutil.copy(src, garmin_dir / "GarminDevice.xml")
    return root
