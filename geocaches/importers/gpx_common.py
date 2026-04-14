"""
Shared parsing and utility functions for GPX importers (GC + OC).

Contains:
  - ImportStats result type
  - XML text helpers (_text, _parse_float)
  - Base cache field parser (shared between GC and OC)
  - Log and attribute parsers
  - Shared save-preparation logic
  - Zip archive handling
"""

import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional

from geocaches.countries import name_to_iso as _country_to_iso
from .lookups import (
    NS_GPX,
    NS_GS,
    gpx,
    gpx_attrs_to_status,
    gpx_container_to_size,
    gpx_log_type_to_log_type,
    gpx_sym_to_waypoint_type,
    gpx_type_to_cache_type,
    gs,
    parse_gpx_date,
    unescape,
)


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------

@dataclass
class ImportStats:
    created: int = 0
    updated: int = 0
    locked: int = 0
    errors: list[str] = field(default_factory=list)

    def __str__(self) -> str:
        parts = [f"created={self.created}", f"updated={self.updated}", f"locked={self.locked}"]
        if self.errors:
            parts.append(f"errors={len(self.errors)}")
        return f"ImportStats({', '.join(parts)})"


# ---------------------------------------------------------------------------
# XML helpers
# ---------------------------------------------------------------------------

def _text(element: ET.Element, clark_tag: str, default: str = "") -> str:
    """Return stripped, unescaped text of a child element, or default."""
    child = element.find(clark_tag)
    if child is None or child.text is None:
        return default
    return unescape(child.text.strip())


def _parse_float(s: str) -> Optional[float]:
    try:
        return float(s)
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# Shared cache field parsing
# ---------------------------------------------------------------------------

def parse_base_cache_fields(wpt_el: ET.Element, cache_el: ET.Element) -> dict:
    """
    Extract Geocache model fields common to both GC and OC GPX caches.

    Returns a flat dict with the raw code in "code" (caller assigns to
    gc_code or oc_code) and all shared fields: name, owner, placed_by,
    cache_type, size, status, latitude, longitude, difficulty, terrain,
    short_description, long_description, hint, hidden_date, country,
    iso_country_code, state.  Includes found=True when sym="Geocache Found".

    Does NOT include: gc_code, oc_code, fav_points, has_trackable,
    owner_gc_id, primary_source, or OC transient fields.
    """
    code = _text(wpt_el, gpx("name"))
    hidden_date = parse_gpx_date(_text(wpt_el, gpx("time")))
    sym = _text(wpt_el, gpx("sym"))

    archived = cache_el.get("archived", "False")
    available = cache_el.get("available", "True")

    result = {
        "code": code,
        "name": _text(cache_el, gs("name")),
        "owner": _text(cache_el, gs("owner")),
        "placed_by": _text(cache_el, gs("placed_by")),
        "cache_type": gpx_type_to_cache_type(_text(cache_el, gs("type"))),
        "size": gpx_container_to_size(_text(cache_el, gs("container"))),
        "status": gpx_attrs_to_status(archived, available),
        "latitude": float(wpt_el.get("lat", 0)),
        "longitude": float(wpt_el.get("lon", 0)),
        "difficulty": _parse_float(_text(cache_el, gs("difficulty"))),
        "terrain": _parse_float(_text(cache_el, gs("terrain"))),
        "short_description": _text(cache_el, gs("short_description")),
        "long_description": _text(cache_el, gs("long_description")),
        "hint": _text(cache_el, gs("encoded_hints")),
        "hidden_date": hidden_date,
        "country": _text(cache_el, gs("country")),
        "iso_country_code": _country_to_iso(_text(cache_el, gs("country"))),
        "state": _text(cache_el, gs("state")),
    }

    if sym == "Geocache Found":
        result["found"] = True

    return result


# ---------------------------------------------------------------------------
# Log parsing
# ---------------------------------------------------------------------------

def parse_logs(cache_el: ET.Element, source: str = "gc") -> list[dict]:
    """
    Parse all <groundspeak:log> entries from a <groundspeak:cache> element.

    Returns a list of dicts with keys: source_id, log_type, user_name,
    user_id, logged_date, text, source.  Entries with unparseable dates
    are skipped.
    """
    logs_el = cache_el.find(gs("logs"))
    if logs_el is None:
        return []

    result = []
    for log_el in logs_el.findall(gs("log")):
        logged_date = parse_gpx_date(_text(log_el, gs("date")))
        if logged_date is None:
            continue
        finder_el = log_el.find(gs("finder"))
        finder_id = (finder_el.get("id") or "") if finder_el is not None else ""
        result.append({
            "source_id": log_el.get("id", ""),
            "log_type": gpx_log_type_to_log_type(_text(log_el, gs("type"))),
            "user_name": _text(log_el, gs("finder")),
            "user_id": finder_id,
            "logged_date": logged_date,
            "text": _text(log_el, gs("text")),
            "source": source,
        })
    return result


# ---------------------------------------------------------------------------
# Attribute parsing
# ---------------------------------------------------------------------------

def parse_attributes(cache_el: ET.Element) -> list[tuple[int, str, bool]]:
    """
    Parse all <groundspeak:attribute> entries.

    Returns a list of (attribute_id, name, is_positive) tuples.
    """
    attrs_el = cache_el.find(gs("attributes"))
    if attrs_el is None:
        return []

    result = []
    for attr_el in attrs_el.findall(gs("attribute")):
        try:
            attr_id = int(attr_el.get("id", 0))
        except ValueError:
            continue
        is_positive = attr_el.get("inc", "1") == "1"
        name = unescape(attr_el.text.strip()) if attr_el.text else ""
        result.append((attr_id, name, is_positive))
    return result


# ---------------------------------------------------------------------------
# Shared save preparation
# ---------------------------------------------------------------------------

def prepare_save_fields(
    cache_fields: dict,
    logs_data: list[dict],
    attrs_data: list[tuple[int, str, bool]],
    now: datetime,
    *,
    code_key: str,
    attr_source: str,
) -> tuple[dict, bool, date | None, list[dict]]:
    """
    Shared pre-save preparation for both GC and OC importers.

    Extracts the found flag, derives last_found_date and found_date from
    logs, sets platform_log_count and last_gpx_date, converts attribute
    tuples to dicts.

    Args:
        cache_fields: Dict from parse_cache_fields (mutated: found and code_key popped).
        logs_data:    Parsed log dicts.
        attrs_data:   List of (attr_id, name, is_positive) tuples.
        now:          Import timestamp.
        code_key:     Field name to remove from fields ("gc_code" or "oc_code").
        attr_source:  Attribute source string ("gc" or "oc").

    Returns:
        (fields, found_from_gpx, found_date, attr_dicts)
    """
    from geocaches.models import Attribute, LogType

    found_from_gpx = cache_fields.pop("found", False)

    fields = {k: v for k, v in cache_fields.items() if k != code_key}

    found_dates = [ld["logged_date"] for ld in logs_data if ld["log_type"] == LogType.FOUND]
    if found_dates:
        fields["last_found_date"] = max(found_dates)
    fields["platform_log_count"] = len(logs_data)
    fields["last_gpx_date"] = now

    found_date = min(found_dates) if found_from_gpx and found_dates else None

    source_enum = getattr(Attribute.Source, attr_source.upper(), Attribute.Source.GC)
    attr_dicts = [
        {
            "source": source_enum,
            "attribute_id": attr_id,
            "is_positive": is_positive,
            "name": attr_name,
        }
        for attr_id, attr_name, is_positive in attrs_data
    ]

    return fields, found_from_gpx, found_date, attr_dicts


# ---------------------------------------------------------------------------
# Zip handling
# ---------------------------------------------------------------------------

def load_roots_from_zip(zip_path: Path) -> tuple[ET.Element, Optional[ET.Element]]:
    """
    Open a zip archive and return (main_root, wpts_root_or_None).

    The main file is the first .gpx that does NOT end with -wpts.gpx.
    The companion wpts file is the first .gpx ending with -wpts.gpx.
    """
    with zipfile.ZipFile(zip_path) as zf:
        names = [n for n in zf.namelist() if n.lower().endswith(".gpx")]
        main_name = next((n for n in names if not n.lower().endswith("-wpts.gpx")), None)
        wpts_name = next((n for n in names if n.lower().endswith("-wpts.gpx")), None)

        if main_name is None:
            raise ValueError(f"No GPX file found inside {zip_path}")

        main_root = ET.fromstring(zf.read(main_name))
        wpts_root = ET.fromstring(zf.read(wpts_name)) if wpts_name else None

    return main_root, wpts_root
