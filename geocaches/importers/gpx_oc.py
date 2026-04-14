"""
Importer for Opencaching.de GPX files.

OC GPX files use the same Groundspeak namespace as GC, but differ in:
  - Cache codes start with "OC" (not "GC")
  - Child waypoints are embedded inline (not in a separate -wpts.gpx file)
  - Parent linkage uses <gsak:Parent> extension element
  - OC-specific metadata in <oc:cache> extension (trip_time, other_code, etc.)

Public API (also exported from geocaches.importers):
  import_oc_gpx(path, tag_names=None) -> ImportStats

Reuses shared lookups and the canonical save_geocache() service.
"""

import re
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from django.db import transaction

from .gpx_common import (
    ImportStats,
    _parse_float,
    _text,
    parse_attributes,
    parse_base_cache_fields,
    parse_logs,
    prepare_save_fields,
)
from .lookups import (
    NS_GPX,
    NS_GS,
    OC_PREFIXES,
    gpx,
    gpx_sym_to_waypoint_type,
    gs,
)

# OC-specific namespaces
NS_OC = "https://github.com/opencaching/gpx-extension-v1"
NS_GSAK = "http://www.gsak.net/xmlv1/4"


def _oc(tag: str) -> str:
    return f"{{{NS_OC}}}{tag}"


def _gsak(tag: str) -> str:
    return f"{{{NS_GSAK}}}{tag}"


# OC sym values that map to waypoint types (beyond what's in lookups.py)
_OC_SYM_MAP = {
    "Flag, Green": "Stage",
    "Diamond, Green": "Reference",
}


def _oc_sym_to_waypoint_type(sym: str) -> str:
    """Map OC waypoint sym to WaypointType, falling back to shared lookup."""
    from geocaches.models import WaypointType
    mapped = _OC_SYM_MAP.get(sym)
    if mapped:
        return getattr(WaypointType, mapped.upper(), WaypointType.OTHER)
    return gpx_sym_to_waypoint_type(sym)


# ---------------------------------------------------------------------------
# Pure parsing — no DB access
# ---------------------------------------------------------------------------

def parse_oc_cache_fields(wpt_el: ET.Element, cache_el: ET.Element) -> dict:
    """
    Extract Geocache model fields from an OC GPX <wpt> + <groundspeak:cache> pair.

    Returns a flat dict of field names -> values. OC caches use oc_code (not gc_code).
    """
    result = parse_base_cache_fields(wpt_el, cache_el)

    # Assign code to oc_code
    code = result.pop("code")
    result["oc_code"] = code

    # OC-specific: strip attribution from long_description
    raw_long_desc = result["long_description"]
    clean_long_desc, attribution = extract_oc_attribution(raw_long_desc)
    result["long_description"] = clean_long_desc

    result["primary_source"] = "oc_de"
    result["_oc_attribution"] = attribution
    result["_oc_short_description"] = result["short_description"]
    result["_oc_long_description"] = clean_long_desc

    return result


# Regex to match the OC attribution block in the long_description.
# Pattern: <p><em>© ... Opencaching.de ... CC BY-NC-ND ... </em></p>
# Optionally followed by a protected-areas block.
_OC_ATTRIBUTION_RE = re.compile(
    r'<p>\s*<em>\s*©.*?opencaching\..*?</em>\s*</p>'
    r'(?:\s*<p>This geocache is probably placed.*?</p>\s*(?:<ul>.*?</ul>)?)?'
    r'\s*(?:<br\s*/?>)?\s*$',
    re.IGNORECASE | re.DOTALL,
)


def extract_oc_attribution(long_desc: str) -> tuple[str, str]:
    """
    Split an OC long_description into (clean_description, attribution_html).

    The attribution block (copyright notice + optional protected areas info)
    is extracted from the end of the description. Returns the description
    without the attribution, and the attribution HTML separately.
    """
    match = _OC_ATTRIBUTION_RE.search(long_desc)
    if match:
        attribution = match.group(0).strip()
        clean = long_desc[:match.start()].rstrip()
        return clean, attribution
    return long_desc, ""


def parse_oc_extension(wpt_el: ET.Element) -> dict:
    """
    Extract OC-specific extension fields from <oc:cache> element.

    Returns a dict with keys: trip_time, trip_distance, req_passwd, other_code.
    Values are None/empty when not present.
    """
    oc_el = wpt_el.find(_oc("cache"))
    if oc_el is None:
        return {}

    trip_time_str = _oc_text(oc_el, "trip_time")
    trip_dist_str = _oc_text(oc_el, "trip_distance")
    req_passwd_str = _oc_text(oc_el, "requires_password")
    other_code = _oc_text(oc_el, "other_code")

    result = {}
    if trip_time_str:
        try:
            result["trip_time"] = float(trip_time_str)
        except ValueError:
            pass
    if trip_dist_str:
        try:
            result["trip_distance"] = float(trip_dist_str)
        except ValueError:
            pass
    if req_passwd_str:
        result["req_passwd"] = req_passwd_str.lower() == "true"
    if other_code:
        result["other_code"] = other_code

    return result


def _oc_text(element: ET.Element, tag: str, default: str = "") -> str:
    """Return stripped text of an <oc:*> child element."""
    child = element.find(_oc(tag))
    if child is None or child.text is None:
        return default
    return child.text.strip()


def parse_oc_inline_waypoints(root: ET.Element) -> dict[str, list[dict]]:
    """
    Parse inline child waypoints from an OC GPX file.

    OC embeds waypoints as sibling <wpt> elements (no groundspeak:cache child)
    with a <gsak:Parent> element linking to the parent OC code.

    Returns a dict mapping parent OC code -> list of waypoint field dicts.
    """
    result: dict[str, list[dict]] = {}

    for wpt_el in root.findall(f"{{{NS_GPX}}}wpt"):
        # Skip cache entries (they have groundspeak:cache)
        if wpt_el.find(f"{{{NS_GS}}}cache") is not None:
            continue

        # Look for gsak:Parent to identify as child waypoint
        gsak_ext = wpt_el.find(_gsak("wptExtension"))
        if gsak_ext is None:
            continue
        parent_code = ""
        parent_el = gsak_ext.find(_gsak("Parent"))
        if parent_el is not None and parent_el.text:
            parent_code = parent_el.text.strip()
        if not parent_code:
            continue

        lookup = _text(wpt_el, gpx("name"))
        sym = _text(wpt_el, gpx("sym"))
        cmt = _text(wpt_el, gpx("cmt"))
        desc = _text(wpt_el, gpx("desc"))

        lat_str = wpt_el.get("lat")
        lon_str = wpt_el.get("lon")
        lat = float(lat_str) if lat_str else None
        lon = float(lon_str) if lon_str else None

        from preferences.models import UserPreference
        if UserPreference.get("drop_zero_waypoints", True) and lat == 0.0 and lon == 0.0:
            continue

        # Derive prefix from lookup (e.g. "OC1485B-1" -> "OC1485B-")
        prefix = lookup[:2] if len(lookup) >= 2 else ""

        wpt_data = {
            "lookup": lookup,
            "prefix": prefix,
            "name": desc or cmt.split("\n")[0] if cmt else "",
            "waypoint_type": _oc_sym_to_waypoint_type(sym),
            "latitude": lat,
            "longitude": lon,
            "note": cmt,
            "is_user_created": False,
        }
        result.setdefault(parent_code, []).append(wpt_data)

    return result


# ---------------------------------------------------------------------------
# DB save layer (adapter to services.save_geocache)
# ---------------------------------------------------------------------------

def _save_oc_geocache(
    cache_fields: dict,
    logs_data: list[dict],
    attrs_data: list[tuple[int, str, bool]],
    wpts_data: list[dict],
    oc_ext: dict,
    tags=None,
    now: Optional[datetime] = None,
) -> tuple:
    """
    Persist a single OC geocache to the database via services.save_geocache().

    Also creates/updates the OCExtension record for OC-specific fields.

    Returns (geocache, created, locked).
    """
    from geocaches.services import save_geocache as _save

    if now is None:
        now = datetime.now(timezone.utc)

    oc_code = cache_fields["oc_code"]

    # Pop OC-specific transient fields before prepare_save_fields
    oc_attribution = cache_fields.pop("_oc_attribution", "")
    oc_short_desc = cache_fields.pop("_oc_short_description", "")
    oc_long_desc = cache_fields.pop("_oc_long_description", "")

    fields, found_from_gpx, found_date, attr_dicts = prepare_save_fields(
        cache_fields, logs_data, attrs_data, now,
        code_key="oc_code", attr_source="oc",
    )

    # If OC file includes a GC cross-reference code, look up by gc_code too
    # and ensure oc_code is stored in fields for dual-listed caches
    other_code = oc_ext.get("other_code", "")
    if other_code and other_code.upper().startswith("GC"):
        fields["gc_code"] = other_code
        fields["oc_code"] = oc_code
        oc_ext["related_gc_code"] = other_code  # persist owner-confirmed cross-reference

    # Propagate OC descriptions/attribution to oc_ext for OCExtension storage
    oc_ext["attribution_html"] = oc_attribution
    oc_ext["long_description"] = oc_long_desc
    oc_ext["short_description"] = oc_short_desc

    result = _save(
        oc_code=oc_code,
        gc_code=other_code if other_code.upper().startswith("GC") else "",
        fields=fields,
        found=found_from_gpx or None,
        found_date=found_date,
        tags=tags,
        logs=logs_data,
        waypoints=wpts_data,
        attributes=attr_dicts,
        update_source="oc",
    )

    # Save OC extension data
    if oc_ext and not result.locked:
        _save_oc_extension(result.geocache, oc_ext)

    return result.geocache, result.created, result.locked


def _save_oc_extension(geocache, oc_ext: dict):
    """Create or update the OCExtension record for OC-specific metadata."""
    from geocaches.models import OCExtension
    ext_fields = {}
    for key in ("trip_time", "trip_distance", "req_passwd",
                "attribution_html", "long_description", "short_description",
                "related_gc_code"):
        if key in oc_ext:
            ext_fields[key] = oc_ext[key]

    if ext_fields:
        OCExtension.objects.update_or_create(
            geocache=geocache,
            defaults=ext_fields,
        )


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def import_oc_gpx(
    path: str,
    tag_names: Optional[list[str]] = None,
) -> ImportStats:
    """
    Import an Opencaching.de GPX file into the database.

    OC GPX files contain both caches and their child waypoints inline
    (no separate -wpts.gpx companion file).

    Args:
        path:       Path to a .gpx file.
        tag_names:  List of tag names to apply to all imported caches.

    Returns:
        ImportStats with counts of created / updated / locked / errors.
    """
    from geocaches.models import Tag

    p = Path(path)
    stats = ImportStats()
    tags = [Tag.objects.get_or_create(name=n)[0] for n in (tag_names or [])]

    # --- Load XML root (zip or plain gpx) ---
    if p.suffix.lower() == ".zip":
        with zipfile.ZipFile(p) as zf:
            names = [n for n in zf.namelist() if n.lower().endswith(".gpx")]
            main_name = next((n for n in names if not n.lower().endswith("-wpts.gpx")), None)
            if main_name is None:
                raise ValueError(f"No GPX file found inside {p}")
            root = ET.fromstring(zf.read(main_name))
    else:
        tree = ET.parse(path)
        root = tree.getroot()

    now = datetime.now(timezone.utc)

    # Pre-parse all inline waypoints keyed by parent OC code
    wpts_by_parent = parse_oc_inline_waypoints(root)

    for wpt_el in root.findall(f"{{{NS_GPX}}}wpt"):
        oc_code = _text(wpt_el, gpx("name"))

        # Skip non-cache entries (child waypoints don't have groundspeak:cache)
        cache_el = wpt_el.find(f"{{{NS_GS}}}cache")
        if cache_el is None:
            continue

        # Accept known OC code prefixes
        if oc_code[:2].upper() not in OC_PREFIXES:
            continue

        try:
            with transaction.atomic():
                cache_fields = parse_oc_cache_fields(wpt_el, cache_el)
                logs_data = parse_logs(cache_el, source="oc_de")
                attrs_data = parse_attributes(cache_el)
                oc_ext = parse_oc_extension(wpt_el)
                wpts_data = wpts_by_parent.get(oc_code, [])

                _, created, locked = _save_oc_geocache(
                    cache_fields, logs_data, attrs_data, wpts_data, oc_ext, tags, now
                )

            if locked:
                stats.locked += 1
            elif created:
                stats.created += 1
            else:
                stats.updated += 1

        except Exception as exc:
            stats.errors.append(f"{oc_code}: {exc}")

    return stats


def parse_oc_logs(cache_el: ET.Element) -> list[dict]:
    """
    Parse logs from an OC GPX cache element.

    Backward-compatible wrapper — calls parse_logs(source="oc_de").
    """
    return parse_logs(cache_el, source="oc_de")
