"""
Importer for lab2gpx GPX files (Adventure Lab Caches).

lab2gpx (https://gcutils.de/lab2gpx/) exports Adventure Lab adventures as GPX.
Two export formats are supported:

  Format A (single-stage, default lab2gpx export):
    One <wpt> per stage with code LC{base}-{n}.
    Adventure metadata in <lab2gpx:adventureLab> extension.
    Question text embedded in <groundspeak:long_description> HTML.

  Format B (multi-cache / waypoint format, --wpt flag):
    Root <gpx> has <desc>(HasChildren)</desc>.
    One <wpt> for the adventure parent (LC{base}).
    One <wpt> per stage (S{n}{suffix}) with <cmt> = question text.

Public API:
    import_lab2gpx(path, tag_names=None) -> ImportStats
"""

import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from django.db import transaction

from geocaches.importers.gpx_gc import ImportStats
from geocaches.importers.gsak import (
    _LC_STAGE_RE, _LC_BASE_RE, _s_code_to_stage,
    _stage_str_to_int,
    _get_or_create_adventure, _upsert_parent_geocache,
    _extract_adventure_description,
)
from geocaches.importers.lookups import gpx, gs, gpx_attrs_to_status, parse_gpx_date, unescape

NS_GSAK = "http://www.gsak.net/xmlv1/6"
NS_LAB2GPX = "https://lab2gpx.gcutils.de/ns/lab2gpx/1"


def _gsak(tag: str) -> str:
    return f"{{{NS_GSAK}}}{tag}"


def _l2g(tag: str) -> str:
    return f"{{{NS_LAB2GPX}}}{tag}"


def _txt(el, tag: str) -> str:
    child = el.find(tag)
    return (child.text or "").strip() if child is not None else ""


_QUESTION_RE = re.compile(r'Question:<br\s*/?>\s*([^<]+)', re.IGNORECASE)


def _extract_question(html_text: str) -> str:
    """Extract question text from lab2gpx long_description HTML."""
    m = _QUESTION_RE.search(html_text)
    return unescape(m.group(1).strip()) if m else ""


_STAGE_HEADER_RE = re.compile(r'<h4[^>]*>\s*S(\d+)\s+')


def _extract_format_b_stage_sections(html: str) -> dict[int, dict[str, str]]:
    """Parse the parent's long_description HTML and extract per-stage data.

    Returns a dict mapping stage number to
    {"question_text": ..., "long_description": ...}.
    """
    result: dict[int, dict[str, str]] = {}
    # Split on stage headers, keeping the stage number
    splits = _STAGE_HEADER_RE.split(html)
    # splits = [preamble, stage_num_1, stage_body_1, stage_num_2, stage_body_2, ...]
    for i in range(1, len(splits) - 1, 2):
        stage_num = int(splits[i])
        body = splits[i + 1]

        # Extract question text: <p>Question:<br />...</p>
        q_match = _QUESTION_RE.search(body)
        question = unescape(q_match.group(1).strip()) if q_match else ""

        # Extract waypoint description: content after <h5>Waypoint Description</h5>
        # until the next <hr or end of body
        wp_match = re.search(
            r'<h5>\s*Waypoint Description\s*</h5>\s*(.*?)(?:<hr\s*/?>|$)',
            body, re.DOTALL | re.IGNORECASE,
        )
        long_desc = wp_match.group(1).strip() if wp_match else ""

        result[stage_num] = {
            "question_text": question,
            "long_description": long_desc,
        }
    return result


def _parse_float(s) -> Optional[float]:
    try:
        return float(s) if s else None
    except (ValueError, TypeError):
        return None


def import_lab2gpx(
    path: str,
    tag_names: Optional[list[str]] = None,
) -> ImportStats:
    """Import a lab2gpx GPX file into GCForge."""
    from geocaches.models import Tag

    stats = ImportStats()
    tags = [Tag.objects.get_or_create(name=n)[0] for n in tag_names] if tag_names else []

    tree = ET.parse(str(path))
    root = tree.getroot()

    # Detect format: Format B has <desc>(HasChildren)</desc> in the root element
    root_desc_el = root.find(gpx("desc"))
    is_format_b = root_desc_el is not None and "(HasChildren)" in (root_desc_el.text or "")

    now = datetime.now(timezone.utc)

    with transaction.atomic():
        if is_format_b:
            _import_format_b(root, tags, stats, now)
        else:
            _import_format_a(root, tags, stats, now)

    return stats


# ---------------------------------------------------------------------------
# Shared DB helper
# ---------------------------------------------------------------------------

def _save_alc_stage(al_code: str, model_fields: dict, tags: list, stats: ImportStats) -> None:
    from geocaches.services import save_geocache as _save

    stage_uuid = model_fields.get("al_stage_uuid", "")

    # Include al_code in fields so it gets updated when found by UUID
    fields = dict(model_fields)
    fields["al_code"] = al_code

    result = _save(
        al_code=al_code,
        al_stage_uuid=stage_uuid,
        fields=fields,
        tags=tags or None,
    )

    if result.locked:
        stats.locked += 1
    elif result.created:
        stats.created += 1
    else:
        stats.updated += 1


# ---------------------------------------------------------------------------
# Format A — one wpt per stage (LC{base}-{n})
# ---------------------------------------------------------------------------

def _import_format_a(root, tags, stats: ImportStats, now) -> None:
    # Adventure records keyed by LC{base} code, populated as stages are parsed
    adventures: dict[str, object] = {}

    for wpt in root.findall(gpx("wpt")):
        name_el = wpt.find(gpx("name"))
        if name_el is None:
            continue
        raw_code = (name_el.text or "").strip()
        m = _LC_STAGE_RE.match(raw_code)
        if not m:
            continue  # not a lab stage wpt

        base = m.group(1)
        n = _stage_str_to_int(m.group(2))
        if n is None:
            stats.errors.append(f"{raw_code}: unrecognised stage suffix, skipped")
            continue
        # Normalise to decimal canonical code
        gc_code = f"LC{base}-{n}"
        adv_code = f"LC{base}"

        lat = _parse_float(wpt.get("lat"))
        lon = _parse_float(wpt.get("lon"))
        if lat is None or lon is None:
            stats.errors.append(f"{gc_code}: missing coordinates, skipped")
            continue

        # Groundspeak cache element
        gs_cache = wpt.find(gs("cache"))
        gs_name = _txt(gs_cache, gs("name")) if gs_cache is not None else ""
        owner = _txt(gs_cache, gs("owner")) if gs_cache is not None else ""
        placed_by = _txt(gs_cache, gs("placed_by")) if gs_cache is not None else ""
        long_desc_el = gs_cache.find(gs("long_description")) if gs_cache is not None else None
        long_desc_html = unescape(long_desc_el.text or "") if long_desc_el is not None else ""

        # Parse available/archived from cache attributes
        if gs_cache is not None:
            status = gpx_attrs_to_status(
                gs_cache.get("archived", "False"),
                gs_cache.get("available", "True"),
            )
        else:
            status = "Active"

        # <time> → placed date
        time_el = wpt.find(gpx("time"))
        placed_date = parse_gpx_date((time_el.text or "") if time_el is not None else "")

        # GSAK extension: stage UUID
        gsak_ext = wpt.find(_gsak("wptExtension"))
        stage_uuid = _txt(gsak_ext, _gsak("Guid")) if gsak_ext is not None else ""

        # lab2gpx extension: adventure UUID, stage count, themes
        l2g_ext = wpt.find(_l2g("adventureLab"))
        adv_guid = _txt(l2g_ext, _l2g("uuid")) if l2g_ext is not None else ""
        themes_raw = _txt(l2g_ext, _l2g("themes")) if l2g_ext is not None else ""
        themes = [t.strip() for t in themes_raw.split(",") if t.strip()] if themes_raw else []
        stages_total_str = _txt(l2g_ext, _l2g("stagesTotal")) if l2g_ext is not None else ""
        try:
            stages_total = int(stages_total_str) if stages_total_str else None
        except ValueError:
            stages_total = None

        # Adventure title: gs:name is "Adventure : Stage Name"
        if " : " in gs_name:
            adv_title = gs_name.split(" : ", 1)[0].strip()
            stage_name = gs_name.split(" : ", 1)[1].strip()
        else:
            adv_title = gs_name
            stage_name = _txt(wpt, gpx("desc"))

        # Adventure-level description: everything before "Question:" in the HTML
        adv_description = _extract_adventure_description(long_desc_html)

        # Create/update Adventure (only first stage sets most fields)
        if adv_code not in adventures:
            adv = _get_or_create_adventure(
                adv_code, adv_title, owner, lat, lon, now,
                status=status, description=adv_description,
                stage_count=stages_total, adventure_guid=adv_guid,
            )
            if themes and not adv.themes:
                adv.themes = themes
                adv.save()
            adventures[adv_code] = adv
            # Create the parent Geocache (LC{base}) once per adventure
            _upsert_parent_geocache(
                adv=adv, owner=owner, placed_by=placed_by, status=status,
                hidden_date=placed_date, country="", state="", county="",
                long_description=adv_description, hint="",
                now=now, tags=tags,
            )
        else:
            adv = adventures[adv_code]

        # Rebuild gc_code from canonical adventure code (may differ from GPX base)
        gc_code = f"{adv.code}-{n}"

        question_text = _extract_question(long_desc_html)

        model_fields = {
            "name":              stage_name or gc_code,
            "owner":             owner,
            "placed_by":         placed_by,
            "cache_type":        "Adventure Lab",
            "size":              "Virtual",
            "status":            status,
            "latitude":          lat,
            "longitude":         lon,
            "hidden_date":       placed_date,
            "last_gpx_date":     now,
            "long_description":  long_desc_html,
            "adventure":         adv,
            "stage_number":      n,
            "question_text":     question_text,
            "al_stage_uuid":     stage_uuid,
        }

        try:
            _save_alc_stage(gc_code, model_fields, tags, stats)
        except Exception as exc:
            stats.errors.append(f"{gc_code}: {exc}")

    # Recompute completed flag for each adventure after all its stages are saved
    from geocaches.models import recompute_adventure_completed
    for adv in adventures.values():
        recompute_adventure_completed(adv)


# ---------------------------------------------------------------------------
# Format B — parent wpt (LC{base}) + stage wpts (S{n}{suffix})
# ---------------------------------------------------------------------------

def _import_format_b(root, tags, stats: ImportStats, now) -> None:
    # Index all wpts by name
    wpts_by_name: dict[str, ET.Element] = {}
    for wpt in root.findall(gpx("wpt")):
        name_el = wpt.find(gpx("name"))
        if name_el is not None and name_el.text:
            wpts_by_name[name_el.text.strip()] = wpt

    # Collect stage wpts grouped by parent code
    stages_by_parent: dict[str, list[tuple[str, ET.Element]]] = {}
    for name, wpt in wpts_by_name.items():
        gsak_ext = wpt.find(_gsak("wptExtension"))
        if gsak_ext is None:
            continue
        parent_el = gsak_ext.find(_gsak("Parent"))
        if parent_el is not None and parent_el.text:
            parent_code = parent_el.text.strip()
            stages_by_parent.setdefault(parent_code, []).append((name, wpt))

    # Process each adventure parent
    for adv_lc_code, stage_list in stages_by_parent.items():
        m = _LC_BASE_RE.match(adv_lc_code)
        if not m:
            continue
        base = m.group(1)
        parent_wpt = wpts_by_name.get(adv_lc_code)
        if parent_wpt is None:
            continue

        lat = _parse_float(parent_wpt.get("lat"))
        lon = _parse_float(parent_wpt.get("lon"))

        gs_cache = parent_wpt.find(gs("cache"))
        adv_title = _txt(gs_cache, gs("name")) if gs_cache is not None else ""
        owner = _txt(gs_cache, gs("owner")) if gs_cache is not None else ""

        # Adventure UUID is in <gsak:Guid> on the parent wpt
        gsak_ext = parent_wpt.find(_gsak("wptExtension"))
        adv_guid = _txt(gsak_ext, _gsak("Guid")) if gsak_ext is not None else ""

        time_el = parent_wpt.find(gpx("time"))
        placed_date = parse_gpx_date((time_el.text or "") if time_el is not None else "")

        # Parent long_description and extracted adventure description
        long_desc_el = gs_cache.find(gs("long_description")) if gs_cache is not None else None
        parent_long_desc = unescape(long_desc_el.text or "") if long_desc_el is not None else ""
        adv_description = _extract_adventure_description(parent_long_desc)

        adv = _get_or_create_adventure(
            adv_lc_code, adv_title, owner, lat, lon, now,
            description=adv_description,
            stage_count=len(stage_list) if stage_list else None,
            adventure_guid=adv_guid,
        )

        # Parent status
        if gs_cache is not None:
            parent_status = gpx_attrs_to_status(
                gs_cache.get("archived", "False"),
                gs_cache.get("available", "True"),
            )
        else:
            parent_status = "Active"

        # Create the parent Geocache (LC{base})
        _upsert_parent_geocache(
            adv=adv, owner=owner, placed_by=owner, status=parent_status,
            hidden_date=placed_date, country="", state="", county="",
            long_description=adv_description, hint="",
            now=now, tags=tags,
        )

        # Parse per-stage sections from parent long_description
        stage_sections = _extract_format_b_stage_sections(parent_long_desc)

        for s_code, stage_wpt in stage_list:
            parsed = _s_code_to_stage(s_code, base)
            if parsed is None:
                stats.errors.append(f"{s_code}: cannot derive canonical code, skipped")
                continue
            _, n = parsed
            canonical_code = f"{adv.code}-{n}"

            stage_lat = _parse_float(stage_wpt.get("lat")) or lat
            stage_lon = _parse_float(stage_wpt.get("lon")) or lon

            # <cmt> = question text; <desc> = "n Stage Name" (strip leading number)
            cmt_el = stage_wpt.find(gpx("cmt"))
            cmt_text = (cmt_el.text or "").strip() if cmt_el is not None else ""
            desc_el = stage_wpt.find(gpx("desc"))
            stage_name_raw = (desc_el.text or "").strip() if desc_el is not None else ""
            # Strip leading digit prefix if present ("1 Moabiter Brücke" → "Moabiter Brücke")
            stage_name = re.sub(r'^\d+\s+', '', stage_name_raw)

            # Use parsed parent sections for question_text and long_description,
            # with <cmt> from stage wpt taking priority for question_text
            section = stage_sections.get(n, {})
            question_text = cmt_text or section.get("question_text", "")
            long_description = section.get("long_description", "")

            model_fields = {
                "name":              stage_name or canonical_code,
                "owner":             owner,
                "placed_by":         owner,
                "cache_type":        "Adventure Lab",
                "size":              "Virtual",
                "status":            parent_status,
                "latitude":          stage_lat,
                "longitude":         stage_lon,
                "hidden_date":       placed_date,
                "last_gpx_date":     now,
                "long_description":  long_description,
                "adventure":         adv,
                "stage_number":      n,
                "question_text":     question_text,
                "al_stage_uuid":     "",
            }

            try:
                _save_alc_stage(canonical_code, model_fields, tags, stats)
            except Exception as exc:
                stats.errors.append(f"{canonical_code}: {exc}")

        from geocaches.models import recompute_adventure_completed
        recompute_adventure_completed(adv)
