"""
Auto-detect GPX file format by inspecting the creator field in the header.

Returns a format string: "gc", "oc", or "unknown".
Sniffs only the first few KB of the file — no full parse needed.
"""

import re
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path


def detect_gpx_format(path: str) -> str:
    """
    Detect whether a GPX/zip file is from geocaching.com or opencaching.

    Returns:
        "gc"      — Groundspeak / geocaching.com Pocket Query
        "oc"      — Opencaching.de (or other OC instances)
        "unknown" — unrecognised format
    """
    p = Path(path)
    if not p.exists():
        return "unknown"

    try:
        if p.suffix.lower() == ".zip":
            return _detect_from_zip(p)
        else:
            return _detect_from_gpx(p)
    except Exception:
        return "unknown"


def _detect_from_gpx(path: Path) -> str:
    """Read the opening tag of a GPX file and detect format from creator attr."""
    # Read first 4KB — enough to get the <gpx> root element with its attributes
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        head = f.read(4096)
    return _detect_from_header(head)


def _detect_from_zip(path: Path) -> str:
    """Find the main GPX inside a zip and detect format."""
    with zipfile.ZipFile(path) as zf:
        names = [n for n in zf.namelist() if n.lower().endswith(".gpx")]
        main_name = next(
            (n for n in names if not n.lower().endswith("-wpts.gpx")), None
        )
        if main_name is None:
            return "unknown"
        head = zf.read(main_name)[:4096].decode("utf-8", errors="replace")
    return _detect_from_header(head)


def _detect_from_header(head: str) -> str:
    """Detect format from the raw XML header text."""
    # Look for creator attribute in <gpx> tag
    creator_match = re.search(r'creator\s*=\s*"([^"]*)"', head, re.IGNORECASE)
    if creator_match:
        creator = creator_match.group(1).lower()
        if "opencaching" in creator:
            return "oc"
        if "groundspeak" in creator or "geocaching.com" in creator:
            return "gc"

    # Fallback: check for OC namespace
    if "opencaching/gpx-extension" in head.lower():
        return "oc"

    # Fallback: check first <name> element for OC code pattern
    name_match = re.search(r"<name>(OC[A-Z0-9]+)</name>", head)
    if name_match:
        return "oc"
    name_match = re.search(r"<name>(GC[A-Z0-9]+)</name>", head)
    if name_match:
        return "gc"

    return "unknown"
