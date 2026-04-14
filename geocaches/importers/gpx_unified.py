"""
Unified GPX importer — single pass over <wpt> elements, dispatching by code prefix.

Handles both GC and OC caches in a single file. LC-prefixed entries are skipped
(lab2gpx files use a fundamentally different structure and their own import path).

Public API:
  import_gpx(main_path, wpts_path=None, tag_names=None) -> ImportStats
"""

import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from django.db import transaction

from .gpx_common import (
    ImportStats,
    _text,
    load_roots_from_zip,
    parse_attributes,
    parse_logs,
)
from .gpx_gc import (
    _parse_wpts_root,
    parse_cache_fields as parse_gc_cache_fields,
    parse_wpts_gpx,
    save_geocache as save_gc_geocache,
)
from .gpx_oc import (
    _save_oc_geocache,
    parse_oc_cache_fields,
    parse_oc_extension,
    parse_oc_inline_waypoints,
)
from .lookups import NS_GPX, NS_GS, OC_PREFIXES, gpx


def import_gpx(
    main_path: str,
    wpts_path: Optional[str] = None,
    tag_names: Optional[list[str]] = None,
) -> ImportStats:
    """
    Unified GPX importer: single pass, dispatches by code prefix.

    Handles GC, OC, and mixed-source GPX files.  LC-prefixed entries
    are skipped (use import_lab2gpx for Adventure Labs).

    Args:
        main_path:  Path to a .gpx file or .zip archive.
        wpts_path:  Optional companion -wpts.gpx (GC waypoints only).
        tag_names:  Tag names to apply to all imported caches.

    Returns:
        ImportStats with counts of created / updated / locked / errors.
    """
    from geocaches.models import Tag

    path = Path(main_path)
    stats = ImportStats()
    tags = [Tag.objects.get_or_create(name=n)[0] for n in (tag_names or [])]

    # --- Load XML root(s) ---
    if path.suffix.lower() == ".zip":
        root, wpts_root = load_roots_from_zip(path)
        gc_wpts = _parse_wpts_root(wpts_root) if wpts_root else {}
    else:
        if wpts_path is None:
            candidate = str(main_path).replace(".gpx", "-wpts.gpx")
            if Path(candidate).exists():
                wpts_path = candidate
        gc_wpts = parse_wpts_gpx(wpts_path) if wpts_path else {}
        root = ET.parse(main_path).getroot()

    now = datetime.now(timezone.utc)

    # Pre-parse OC inline waypoints (sibling <wpt> without groundspeak:cache)
    oc_wpts = parse_oc_inline_waypoints(root)

    # --- Single pass over all <wpt> elements ---
    for wpt_el in root.findall(f"{{{NS_GPX}}}wpt"):
        code = _text(wpt_el, gpx("name"))
        prefix = code[:2].upper() if len(code) >= 2 else ""

        # Skip non-cache wpts (child waypoints have no groundspeak:cache)
        cache_el = wpt_el.find(f"{{{NS_GS}}}cache")
        if cache_el is None:
            continue

        if prefix == "GC":
            _import_gc_wpt(wpt_el, cache_el, code, gc_wpts, tags, now, stats)
        elif prefix in OC_PREFIXES:
            _import_oc_wpt(wpt_el, cache_el, code, oc_wpts, tags, now, stats)
        # LC (Adventure Lab) and unknown prefixes are silently skipped

    return stats


def _import_gc_wpt(wpt_el, cache_el, gc_code, wpts_by_parent, tags, now, stats):
    """Process a single GC-prefixed <wpt> element."""
    try:
        with transaction.atomic():
            cache_fields = parse_gc_cache_fields(wpt_el, cache_el)
            logs_data = parse_logs(cache_el, source="gc")
            attrs_data = parse_attributes(cache_el)
            wpts_data = wpts_by_parent.get(gc_code, [])
            _, created, locked = save_gc_geocache(
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


def _import_oc_wpt(wpt_el, cache_el, oc_code, wpts_by_parent, tags, now, stats):
    """Process a single OC-prefixed <wpt> element."""
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
