"""
Importer for Groundspeak / geocaching.com Pocket Query GPX files.

Supported input formats:
  <name>.gpx           — main file with one <wpt> per geocache
  <name>-wpts.gpx      — companion file with additional waypoints
  <name>.zip           — zip archive containing one or both of the above

Public API (also exported from geocaches.importers):
  import_gc_gpx(main_path, wpts_path=None, tag_name=None) -> ImportStats

The parsing layer (parse_* functions) is pure — no DB access — so it can be
called from tests without a database, and reused by future API importers.
The save layer (save_geocache, import_gc_gpx) touches the database.
"""

import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from django.db import transaction

from .gpx_common import (
    ImportStats,
    _parse_float,
    _text,
    load_roots_from_zip,
    parse_attributes,
    parse_base_cache_fields,
    parse_logs,
    prepare_save_fields,
)
from .lookups import (
    NS_GPX,
    NS_GS,
    gpx,
    gpx_sym_to_waypoint_type,
    gs,
)

# Backward-compatible re-exports (used by tests and gpx_oc.py)
__all__ = [
    "ImportStats", "_text", "_parse_float", "parse_logs", "parse_attributes",
    "parse_cache_fields", "save_geocache", "import_gc_gpx",
    "parse_wpts_gpx", "_parse_wpts_root", "_load_roots_from_zip",
]

# Keep old name available for any code importing _load_roots_from_zip
_load_roots_from_zip = load_roots_from_zip


# ---------------------------------------------------------------------------
# Pure parsing — no DB access
# ---------------------------------------------------------------------------

def parse_cache_fields(wpt_el: ET.Element, cache_el: ET.Element) -> dict:
    """
    Extract Geocache model fields from a <wpt> + <groundspeak:cache> element pair.

    Returns a flat dict of field names -> values (no logs, attributes, or waypoints).
    When sym="Geocache Found" the dict includes found=True, signalling that this
    cache was found by the requesting user (e.g. from a My Finds pocket query).
    """
    result = parse_base_cache_fields(wpt_el, cache_el)

    # Assign code to gc_code
    code = result.pop("code")
    result["gc_code"] = code

    # GC-specific fields
    fav_str = _text(cache_el, gs("favorite_points"))
    result["fav_points"] = int(fav_str) if fav_str.isdigit() else 0

    tb_el = cache_el.find(gs("travelbugs"))
    result["has_trackable"] = tb_el is not None and len(list(tb_el)) > 0

    owner_el = cache_el.find(gs("owner"))
    owner_gc_id = None
    if owner_el is not None:
        try:
            owner_gc_id = int(owner_el.get("id") or "")
        except (ValueError, TypeError):
            pass
    result["owner_gc_id"] = owner_gc_id
    result["primary_source"] = "gc"

    return result


def _parse_wpts_root(root: ET.Element) -> dict[str, list[dict]]:
    """
    Parse waypoints from a -wpts.gpx root element.

    Returns a dict mapping parent GC code -> list of waypoint field dicts.
    """
    result: dict[str, list[dict]] = {}

    for wpt_el in root.findall(f"{{{NS_GPX}}}wpt"):
        lookup = _text(wpt_el, gpx("name"))
        if len(lookup) < 3:
            continue

        parent_gc = "GC" + lookup[2:]
        sym = _text(wpt_el, gpx("sym"))
        cmt = _text(wpt_el, gpx("cmt"))

        lat_str = wpt_el.get("lat")
        lon_str = wpt_el.get("lon")

        lat = float(lat_str) if lat_str else None
        lon = float(lon_str) if lon_str else None

        from preferences.models import UserPreference
        if UserPreference.get("drop_zero_waypoints", True) and lat == 0.0 and lon == 0.0:
            continue

        wpt_data = {
            "lookup": lookup,
            "prefix": lookup[:2],
            "name": _text(wpt_el, gpx("desc")),
            "waypoint_type": gpx_sym_to_waypoint_type(sym),
            "latitude": lat,
            "longitude": lon,
            "note": cmt,
            "is_user_created": False,
        }
        result.setdefault(parent_gc, []).append(wpt_data)

    return result


def parse_wpts_gpx(path: str) -> dict[str, list[dict]]:
    """
    Parse a Groundspeak -wpts.gpx companion file.

    Returns a dict mapping parent GC code -> list of waypoint field dicts.

    Linking convention: waypoint lookup code = 2-char prefix + GC code
    without the "GC" prefix.  E.g. "S1NNXM" -> parent "GCNNXM".
    """
    tree = ET.parse(path)
    return _parse_wpts_root(tree.getroot())


# ---------------------------------------------------------------------------
# DB save layer
# ---------------------------------------------------------------------------

def save_geocache(
    cache_fields: dict,
    logs_data: list[dict],
    attrs_data: list[tuple[int, str, bool]],
    wpts_data: list[dict],
    tags=None,
    now: Optional[datetime] = None,
) -> tuple:
    """
    Persist a single GC geocache (and related objects) to the database.

    Adapter that translates GPX-parsed data into the canonical
    services.save_geocache() call.

    Returns (geocache, created, locked).
    """
    from geocaches.services import save_geocache as _save

    if now is None:
        now = datetime.now(timezone.utc)

    gc_code = cache_fields["gc_code"]

    fields, found_from_gpx, found_date, attr_dicts = prepare_save_fields(
        cache_fields, logs_data, attrs_data, now,
        code_key="gc_code", attr_source="gc",
    )

    result = _save(
        gc_code=gc_code,
        fields=fields,
        found=found_from_gpx or None,
        found_date=found_date,
        tags=tags,
        logs=logs_data,
        waypoints=wpts_data,
        attributes=attr_dicts,
        update_source="gc",
    )
    return result.geocache, result.created, result.locked


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def import_gc_gpx(
    main_path: str,
    wpts_path: Optional[str] = None,
    tag_names: Optional[list[str]] = None,
) -> ImportStats:
    """
    Import a Groundspeak PQ GPX file (or zip archive) into the database.

    Args:
        main_path:  Path to a .gpx file or a .zip archive.
                    For zip files, the archive is inspected automatically for
                    the main GPX and an optional -wpts.gpx companion.
        wpts_path:  Path to the companion -wpts.gpx file (optional, .gpx only).
                    If not given, auto-detected by replacing .gpx with -wpts.gpx.
        tag_names:  List of tag names to apply to all imported caches.

    Returns:
        ImportStats with counts of created / updated / locked / errors.
    """
    from geocaches.models import Tag

    path = Path(main_path)
    stats = ImportStats()
    tags = [Tag.objects.get_or_create(name=n)[0] for n in (tag_names or [])]

    # --- Load XML roots ---
    if path.suffix.lower() == ".zip":
        root, wpts_root = load_roots_from_zip(path)
        wpts_by_parent = _parse_wpts_root(wpts_root) if wpts_root else {}
    else:
        if wpts_path is None:
            candidate = str(main_path).replace(".gpx", "-wpts.gpx")
            if Path(candidate).exists():
                wpts_path = candidate
        wpts_by_parent = parse_wpts_gpx(wpts_path) if wpts_path else {}
        tree = ET.parse(main_path)
        root = tree.getroot()

    now = datetime.now(timezone.utc)

    for wpt_el in root.findall(f"{{{NS_GPX}}}wpt"):
        gc_code = _text(wpt_el, gpx("name"))
        if not gc_code.upper().startswith("GC"):
            continue

        cache_el = wpt_el.find(f"{{{NS_GS}}}cache")
        if cache_el is None:
            continue

        try:
            with transaction.atomic():
                cache_fields = parse_cache_fields(wpt_el, cache_el)
                logs_data = parse_logs(cache_el)
                attrs_data = parse_attributes(cache_el)
                wpts_data = wpts_by_parent.get(gc_code, [])

                _, created, locked = save_geocache(
                    cache_fields, logs_data, attrs_data, wpts_data, tags, now
                )

            if locked:
                stats.locked += 1
            elif created:
                stats.created += 1
            else:
                stats.updated += 1

        except Exception as exc:
            stats.errors.append(f"{gc_code}: {exc}")

    return stats
