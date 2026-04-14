"""
Importer for GSAK (Geocaching Swiss Army Knife) sqlite.db3 database files.

Each GSAK database maps to a tag in GCForge. The importer reads all caches,
logs, waypoints, corrected coordinates, attributes, and images.

Adventure Lab Caches (type Q) are handled in two GSAK storage formats:
  Format A — individual LC{base}-{n} rows (lab2gpx single-stage import)
  Format B — LC{base} parent row + S{n}{suffix} waypoints (GSAK default)
Both are normalised to LC{base}-{n} canonical codes linked to an Adventure parent.

Public API:
    import_gsak_db(db_path, tag_names=None) -> ImportStats
"""

import re
import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional

from django.db import transaction

from geocaches.countries import name_to_iso as _country_to_iso
from geocaches.importers.gpx_gc import ImportStats
from geocaches.importers.lookups import OC_PREFIXES, gpx_container_to_size, gpx_sym_to_waypoint_type

# ---------------------------------------------------------------------------
# GSAK UserNote splitting
# ---------------------------------------------------------------------------

_GSAK_NOTE_SPLIT = "$~"
_FIELD_NOTE_RE = re.compile(
    r"--Field Note Start from (\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})--\r?\n(.*?)\r?\n--Field Note End--",
    re.DOTALL,
)


def split_gsak_note(raw: str) -> list[dict]:
    """
    Parse a raw GSAK CacheMemo.UserNote string and return a list of dicts
    ready for ``Note.objects.create(**record)``.

    - Text before ``$~``     → note_type="note"   (omitted if empty)
    - Each Field Note block  → note_type="field_note" with logged_at parsed
      (omitted only if both text and date are absent)
    """
    if not raw:
        return []

    if _GSAK_NOTE_SPLIT not in raw:
        text = raw.strip()
        return [{"note_type": "note", "format": "plain", "body": text}] if text else []

    user_part, field_part = raw.split(_GSAK_NOTE_SPLIT, 1)
    records: list[dict] = []

    user_text = user_part.strip()
    if user_text:
        records.append({"note_type": "note", "format": "plain", "body": user_text})

    for m in _FIELD_NOTE_RE.finditer(field_part):
        try:
            logged_at = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        except ValueError:
            logged_at = None
        text = m.group(2).strip()
        if logged_at is None and not text:
            continue
        records.append({
            "note_type": "field_note",
            "format":    "plain",
            "body":      text,
            "logged_at": logged_at,
        })

    return records


# GSAK single-character cache type codes → GCForge CacheType values
_GSAK_CACHE_TYPE = {
    "T": "Traditional",
    "M": "Multi-Cache",
    "U": "Mystery",
    "V": "Virtual",
    "E": "Event",
    "I": "Unknown",
    "C": "CITO",
    "R": "Earthcache",
    "B": "Letterbox Hybrid",
    "Q": "Adventure Lab",          # Adventure Lab Cache Stage (LC-prefix codes)
    "G": "NGS Benchmark",          # NGS Benchmark (geographic marker; retired on GC.com 2023-01-04)
    "W": "Webcam",
    "L": "Locationless",
    "F": "Mega-Event",
    "O": "Community Celebration Event",
    "Z": "Giga-Event",
}

# LC28NG-1  → base='28NG', n=1  (Format A: individual stage rows)
# Stage suffix may be decimal (GSAK), base31 (lab2gpx Format A), or hex (lab2gpx Format B wpt)
_LC_STAGE_RE = re.compile(r'^LC([A-Z0-9]+)-([A-Z0-9]+)$')
# LC28NG    → base='28NG'       (Format B: adventure parent row)
_LC_BASE_RE = re.compile(r'^LC([A-Z0-9]+)$')

# lab2gpx base31 alphabet (same as GC codes: no I, L, O, S, U)
_BASE31_ALPHABET = '0123456789ABCDEFGHJKMNPQRTVWXYZ'
_BASE31_MAP = {ch: i for i, ch in enumerate(_BASE31_ALPHABET)}


def _base31_to_int(s: str) -> Optional[int]:
    """Decode a base31 string to int. Returns None if invalid."""
    result = 0
    for ch in s.upper():
        if ch not in _BASE31_MAP:
            return None
        result = result * 31 + _BASE31_MAP[ch]
    return result


def _stage_str_to_int(s: str) -> Optional[int]:
    """Convert a stage suffix to int: try decimal, then hex, then base31."""
    if not s:
        return None
    # Decimal
    if s.isdigit():
        return int(s)
    # Hex (lab2gpx Format B uses dechex for stage prefix)
    try:
        return int(s, 16)
    except ValueError:
        pass
    # Base31 (lab2gpx Format A uses base31 for stage suffix)
    return _base31_to_int(s)

_ADV_DESC_SPLIT_RE = re.compile(r'Question\s*:', re.IGNORECASE)

# lab2gpx prepends auto-generated metadata paragraphs before the human description:
#   <p><a href="https://labs.geocaching.com/goto/...">...</a></p>
#   <p>Radius: 25m</p>  <p>Stages: 5</p>  <p>Kind of cache: ...</p>  etc.
_LAB2GPX_META_P_RE = re.compile(
    r'\s*<p>\s*(?:'
    r'<a\s[^>]*labs\.geocaching\.com[^>]*>.*?</a>'
    r'|Radius\s*:[^<]*'
    r'|Stages\s*:[^<]*'
    r'|Kind\s+of\s+cache\s*:[^<]*'
    r'|Type\s*:[^<]*'
    r'|Owner\s*:[^<]*'
    r'|Difficulty\s*:[^<]*'
    r'|Terrain\s*:[^<]*'
    r')\s*</p>',
    re.IGNORECASE | re.DOTALL,
)

# Matches the <h5>Adventure Lab Description</h5> heading (case-insensitive, flexible whitespace)
_ADV_LAB_DESC_HEADING_RE = re.compile(
    r'<h5[^>]*>\s*Adventure\s+Lab\s+Description\s*</h5>',
    re.IGNORECASE,
)


def _extract_adventure_description(html: str) -> str:
    """Return only the human-written adventure description from a lab2gpx long_description block.

    lab2gpx embeds the adventure description after an
    ``<h5>Adventure Lab Description</h5>`` heading. This function extracts
    that section, up to the next ``<hr`` (if any) or end of string.

    Falls back to the old heuristic (strip metadata from front, cut at
    "Question:") when the heading is not found.
    """
    if not html:
        return ""

    # Primary strategy: look for the <h5>Adventure Lab Description</h5> heading
    m = _ADV_LAB_DESC_HEADING_RE.search(html)
    if m:
        after = html[m.end():]
        # Take everything up to the next <hr (may be <hr>, <hr/>, <hr />) or end
        hr_match = re.search(r'<hr[\s/>]', after, re.IGNORECASE)
        if hr_match:
            after = after[:hr_match.start()]
        return after.strip()

    # Fallback: strip known lab2gpx metadata paragraphs from the start
    while True:
        m = _LAB2GPX_META_P_RE.match(html)
        if not m:
            break
        html = html[m.end():]
    html = html.strip()
    # Strip from "Question:" onwards
    m = _ADV_DESC_SPLIT_RE.search(html)
    if m:
        html = html[:m.start()].strip()
        html = re.sub(r'(<br\s*/?>\s*)+$', '', html).strip()
    return html


def _parse_date(s) -> Optional[date]:
    if not s:
        return None
    try:
        return date.fromisoformat(str(s)[:10])
    except ValueError:
        return None


def _parse_float(s) -> Optional[float]:
    if s is None or s == "":
        return None
    try:
        return float(s)
    except (ValueError, TypeError):
        return None


def _status(archived: int, temp_disabled: int) -> str:
    from geocaches.models import CacheStatus
    if archived:
        return CacheStatus.ARCHIVED
    if temp_disabled:
        return CacheStatus.DISABLED
    return CacheStatus.ACTIVE


def _s_code_to_stage(s_code: str, base: str) -> Optional[tuple[str, int]]:
    """
    Convert a GSAK/lab2gpx stage waypoint code + adventure base to (canonical_code, stage_number).
    e.g. 'S128NG' + '28NG' → ('LC28NG-1', 1)      (decimal, GSAK)
         'SA28NG' + '28NG' → ('LC28NG-10', 10)     (hex A=10, lab2gpx Format B)
    Format: {letter}{n_encoded}{last-4-of-base}
    Canonical code always uses decimal stage number.
    """
    if not s_code or not s_code[0].isalpha():
        return None
    suffix = base[-4:] if len(base) >= 4 else base
    inner = s_code[1:]  # strip prefix letter
    if not inner.endswith(suffix):
        return None
    n_str = inner[: -len(suffix)]
    n = _stage_str_to_int(n_str)
    if n is None:
        return None
    return f"LC{base}-{n}", n


def import_gsak_db(
    db_path: str,
    tag_names: Optional[list[str]] = None,
) -> ImportStats:
    """
    Import a GSAK sqlite.db3 file into the GCForge database.

    Args:
        db_path:    Path to the GSAK sqlite.db3 file.
        tag_names:  Tags to apply to all imported caches. Defaults to
                    [parent_directory_name] (i.e. the GSAK DB name).
    """
    from geocaches.models import Tag

    path = Path(db_path)
    if not tag_names:
        tag_names = [path.parent.name]

    stats = ImportStats()
    tags = [Tag.objects.get_or_create(name=n)[0] for n in tag_names]

    def _decode(b: bytes) -> str:
        """Decode SQLite bytes: try UTF-8 first, fall back to cp1252 (GSAK on Windows)."""
        if not b:
            return ""
        try:
            return b.decode("utf-8")
        except UnicodeDecodeError:
            return b.decode("cp1252", errors="replace")

    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.text_factory = _decode

    # Check whether the Caches table has a Guid column (not all GSAK versions do)
    _has_guid = bool(conn.execute(
        "SELECT 1 FROM pragma_table_info('Caches') WHERE name='Guid'"
    ).fetchone())

    try:
        # ------------------------------------------------------------------
        # Pre-load all related data into memory (avoids N+1 in the main loop)
        # ------------------------------------------------------------------

        memos = {
            row["Code"]: row for row in conn.execute(
                "SELECT Code, LongDescription, ShortDescription, Hints, UserNote "
                "FROM CacheMemo"
            )
        }

        log_texts = {
            (row["lParent"], row["lLogId"]): row["lText"]
            for row in conn.execute("SELECT lParent, lLogId, lText FROM LogMemo")
        }

        logs_by_code: dict[str, list] = {}
        for row in conn.execute(
            "SELECT lParent, lLogId, lType, lBy, lDate FROM Logs ORDER BY lDate DESC"
        ):
            logs_by_code.setdefault(row["lParent"], []).append(row)

        waypoints_by_code: dict[str, list] = {}
        for row in conn.execute(
            "SELECT cParent, cCode, cPrefix, cName, cType, cLat, cLon, cByuser "
            "FROM Waypoints"
        ):
            waypoints_by_code.setdefault(row["cParent"], []).append(row)

        corrected = {
            row["kCode"]: row for row in conn.execute(
                "SELECT kCode, kBeforeLat, kBeforeLon, kAfterLat, kAfterLon"
                " FROM Corrected"
            )
        }

        attrs_by_code: dict[str, list] = {}
        for row in conn.execute("SELECT aCode, aId, aInc FROM Attributes"):
            attrs_by_code.setdefault(row["aCode"], []).append(row)

        images_by_code: dict[str, list] = {}
        for row in conn.execute(
            "SELECT iCode, iName, iDescription, iImage FROM CacheImages"
        ):
            images_by_code.setdefault(row["iCode"], []).append(row)

        # ------------------------------------------------------------------
        # Pre-aggregate Format A ALC adventures (stage_count + description)
        # memos is already loaded, so this only needs the Code/CacheType columns.
        # ------------------------------------------------------------------

        alc_format_a_info: dict[str, dict] = {}  # base -> {stage_count, description}
        for row in conn.execute("SELECT Code FROM Caches WHERE CacheType = 'Q'"):
            code = row["Code"]
            m = _LC_STAGE_RE.match(code)
            if m and _stage_str_to_int(m.group(2)) is not None:
                base = m.group(1)
                info = alc_format_a_info.setdefault(base, {"stage_count": 0, "description": ""})
                info["stage_count"] += 1
                if not info["description"]:
                    memo = memos.get(code)
                    raw = (memo and memo["LongDescription"]) or ""
                    info["description"] = _extract_adventure_description(raw)

        # ------------------------------------------------------------------
        # Main import loop
        # ------------------------------------------------------------------

        guid_col = ", Guid" if _has_guid else ""
        now = datetime.now(timezone.utc)

        for row in conn.execute(f"""
            SELECT Code, Name, PlacedBy, OwnerName, OwnerId, CacheType, Container,
                   Archived, TempDisabled, Latitude, Longitude,
                   Difficulty, Terrain, PlacedDate, LastFoundDate,
                   Country, State, County, Elevation,
                   Found, FoundByMeDate, FoundCount, FTF, DNF, DNFDate,
                   MacroFlag, UserFlag, Watch, GcNote, UserSort, Color,
                   Lock, IsPremium, HasTravelBug, FavPoints, NumberOfLogs, HasCorrected
                   {guid_col}
            FROM Caches
        """):
            code = row["Code"]
            if not code:
                continue

            # Route OC codes to oc_code field instead of gc_code
            prefix = code[:2].upper()
            _is_oc = prefix in OC_PREFIXES
            gc_code = "" if _is_oc else code
            oc_code = code if _is_oc else ""

            lat = _parse_float(row["Latitude"])
            lon = _parse_float(row["Longitude"])
            if lat is None or lon is None:
                stats.errors.append(f"{code}: missing coordinates, skipped")
                continue

            guid = (row["Guid"] or "") if _has_guid else ""

            try:
                if row["CacheType"] == "Q":
                    result = _save_alc(
                        row, gc_code, lat, lon, now, tags, guid,
                        memos, log_texts, logs_by_code, waypoints_by_code, stats,
                        alc_format_a_info,
                    )
                else:
                    result = _save_cache(
                        row, code, gc_code, oc_code, lat, lon, now, tags,
                        memos, log_texts, logs_by_code,
                        waypoints_by_code, corrected, attrs_by_code, images_by_code,
                    )

                if result == "created":
                    stats.created += 1
                elif result == "updated":
                    stats.updated += 1
                elif result == "counted":
                    pass  # Format B: stages already counted inside _save_alc_format_b
                else:  # locked
                    stats.locked += 1

            except Exception as exc:
                stats.errors.append(f"{code}: {exc}")

    finally:
        conn.close()

    return stats


# ---------------------------------------------------------------------------
# Adventure Lab Cache helpers
# ---------------------------------------------------------------------------

def _get_or_create_adventure(
    code: str, title: str, owner: str, lat, lon, now,
    status: str = "", description: str = "", stage_count=None,
    adventure_guid: str = "",
):
    """Get or create an Adventure record, updating mutable fields if it exists.

    Lookup order:
      1. adventure_guid (stable UUID from lab2gpx/AL API)
      2. code (LC{base} — fallback when no GUID is available, e.g. GSAK imports)

    When a GUID is available the canonical LC code is derived deterministically
    via uuid_to_lc_code(), making it identical across all tools and installations.
    GSAK-imported adventures (no GUID) keep whatever code the export provided.
    """
    from geocaches.models import Adventure
    from geocaches.lc_code import uuid_to_lc_code

    # Derive the canonical code from the GUID when available; fall back to the
    # caller-supplied code for GSAK imports that carry no adventure GUID.
    canonical_code = uuid_to_lc_code(adventure_guid) if adventure_guid else code

    adv = None
    if adventure_guid:
        adv = Adventure.objects.filter(adventure_guid=adventure_guid).first()

    mutable = {
        "title": title,
        "owner": owner,
        "latitude": lat,
        "longitude": lon,
        "status": status,
        "description": description,
    }

    if adv is not None:
        changed = False
        if adv.code != canonical_code:
            adv.code = canonical_code
            changed = True
        for field, value in mutable.items():
            if value and getattr(adv, field) != value:
                setattr(adv, field, value)
                changed = True
        if stage_count is not None and adv.stage_count != stage_count:
            adv.stage_count = stage_count
            changed = True
        if changed:
            adv.save()
        return adv

    defaults = dict(mutable)
    if stage_count is not None:
        defaults["stage_count"] = stage_count
    if adventure_guid:
        defaults["adventure_guid"] = adventure_guid

    adv, created = Adventure.objects.get_or_create(code=canonical_code, defaults=defaults)
    if not created:
        changed = False
        for field, value in mutable.items():
            if value and getattr(adv, field) != value:
                setattr(adv, field, value)
                changed = True
        if stage_count is not None and adv.stage_count != stage_count:
            adv.stage_count = stage_count
            changed = True
        if adventure_guid and not adv.adventure_guid:
            adv.adventure_guid = adventure_guid
            changed = True
        if changed:
            adv.save()
    return adv


def _save_alc(
    row, gc_code, lat, lon, now, tags, guid,
    memos, log_texts, logs_by_code, waypoints_by_code, stats,
    alc_format_a_info=None,
) -> str:
    """
    Handle a type-Q (Adventure Lab) cache row.

    Format A: gc_code = 'LC28NG-1'  → individual stage rows, no waypoints
    Format B: gc_code = 'LC28NG'    → adventure parent, stages in Waypoints
    """
    m_stage = _LC_STAGE_RE.match(gc_code)
    m_parent = _LC_BASE_RE.match(gc_code)

    if m_stage:
        # Format A — individual stage row already has canonical code
        base = m_stage.group(1)
        n = _stage_str_to_int(m_stage.group(2))
        if n is None:
            return "skipped"
        # Normalise gc_code to decimal canonical form (e.g. LC28NG-A → LC28NG-10)
        gc_code = f"LC{base}-{n}"
        adv_info = (alc_format_a_info or {}).get(base, {})
        return _save_alc_stage_format_a(
            row, gc_code, base, n, lat, lon, now, tags, guid,
            memos, log_texts, logs_by_code,
            adv_stage_count=adv_info.get("stage_count"),
            adv_description=adv_info.get("description", ""),
        )
    elif m_parent:
        # Format B — parent row; stages come from Waypoints
        base = m_parent.group(1)
        _save_alc_format_b(
            row, gc_code, base, lat, lon, now, tags,
            memos, log_texts, logs_by_code, waypoints_by_code, stats,
        )
        return "counted"
    else:
        # Unexpected code format — fall through as Unknown type
        return "skipped"


def _upsert_parent_geocache(
    adv, owner, placed_by, status, hidden_date,
    country, state, county, long_description, hint,
    now, tags,
):
    """Create or update the parent Geocache (LC{base}) representing the whole adventure."""
    from geocaches.models import CacheType, Geocache
    fields = {
        "name":             adv.title or adv.code,
        "owner":            owner,
        "placed_by":        placed_by,
        "cache_type":       CacheType.LAB,
        "size":             "Virtual",
        "status":           status,
        "latitude":         adv.latitude,
        "longitude":        adv.longitude,
        "hidden_date":      hidden_date,
        "country":          country,
        "iso_country_code": _country_to_iso(country),
        "state":            state,
        "county":           county,
        "last_gpx_date":    now,
        "long_description": long_description,
        "hint":             hint,
        "adventure":        adv,
        "stage_number":     None,
    }
    with transaction.atomic():
        gc, created = Geocache.objects.get_or_create(al_code=adv.code, defaults=fields)
        if not created and not gc.import_locked:
            for k, v in fields.items():
                setattr(gc, k, v)
            gc.save()
        if tags:
            gc.tags.add(*tags)


def _save_alc_stage_format_a(
    row, gc_code, base, n, lat, lon, now, tags, guid,
    memos, log_texts, logs_by_code,
    adv_stage_count=None, adv_description="",
) -> str:
    """Persist a Format A ALC stage (LC{base}-{n} individual row).

    Adapter that translates GSAK ALC stage data into a canonical
    services.save_geocache() call. Adventure/parent upsert stays here.
    """
    from geocaches.models import CacheType
    from geocaches.services import save_geocache as _save

    memo = memos.get(gc_code)

    # Adventure title: 'Adventure Name : Stage Name' → extract adventure part
    full_name = row["Name"] or ""
    if " : " in full_name:
        adv_title, stage_name = full_name.split(" : ", 1)
    else:
        adv_title = full_name
        stage_name = full_name

    status = _status(row["Archived"] or 0, row["TempDisabled"] or 0)

    adv = _get_or_create_adventure(
        code=f"LC{base}",
        title=adv_title.strip(),
        owner=row["OwnerName"] or "",
        lat=lat,
        lon=lon,
        now=now,
        status=status,
        description=adv_description,
        stage_count=adv_stage_count,
    )
    # Rebuild gc_code from canonical adventure code (may differ from GPX base)
    gc_code = f"{adv.code}-{n}"
    hidden_date = _parse_date(row["PlacedDate"])
    owner = row["OwnerName"] or ""
    placed_by = row["PlacedBy"] or ""
    country = row["Country"] or ""
    state = row["State"] or ""
    county = row["County"] or ""

    # Upsert the parent Geocache for the whole adventure (LC{base})
    parent_desc = adv_description or _extract_adventure_description(
        (memo and memo["LongDescription"]) or ""
    )
    _upsert_parent_geocache(
        adv=adv, owner=owner, placed_by=placed_by, status=status,
        hidden_date=hidden_date, country=country, state=state, county=county,
        long_description=parent_desc,
        hint=(memo and memo["Hints"]) or "",
        now=now, tags=tags,
    )

    # Question text is embedded in the long_description HTML from lab2gpx
    question_text = (memo and memo["LongDescription"]) or ""

    fields = {
        "name":              stage_name.strip(),
        "owner":             owner,
        "placed_by":         placed_by,
        "cache_type":        CacheType.LAB,
        "status":            status,
        "latitude":          lat,
        "longitude":         lon,
        "hidden_date":       hidden_date,
        "country":           country,
        "iso_country_code":  _country_to_iso(country),
        "state":             state,
        "county":            county,
        "last_gpx_date":     now,
        "short_description": (memo and memo["ShortDescription"]) or "",
        "long_description":  question_text,
        "hint":              (memo and memo["Hints"]) or "",
        "adventure":         adv,
        "stage_number":      n,
        "question_text":     question_text,
        "al_stage_uuid":     guid,
        "al_journal_text":   (memo and memo["UserNote"]) or "",
    }

    found_from_gsak = bool(row["Found"])
    found_date_from_gsak = _parse_date(row["FoundByMeDate"])

    logs_data = _build_log_dicts(gc_code, logs_by_code, log_texts)

    with transaction.atomic():
        result = _save(
            al_stage_uuid=guid,
            al_code=gc_code,
            fields=fields,
            found=found_from_gsak or None,
            found_date=found_date_from_gsak,
            tags=tags,
            logs=logs_data or None,
        )

    if result.locked:
        return "locked"
    elif result.created:
        return "created"
    else:
        return "updated"


def _save_alc_format_b(
    row, gc_code, base, lat, lon, now, tags,
    memos, log_texts, logs_by_code, waypoints_by_code, stats,
) -> None:
    """
    Persist a Format B ALC: parent row (LC{base}) + S-code stage waypoints.
    Creates/updates an Adventure record, the parent Geocache, and one Geocache
    per stage. Counts directly into stats.

    Stage saves use services.save_geocache(); adventure/parent upsert stays here.
    """
    from geocaches.models import CacheType
    from geocaches.services import save_geocache as _save

    owner = row["OwnerName"] or ""
    placed_by = row["PlacedBy"] or ""
    status = _status(row["Archived"] or 0, row["TempDisabled"] or 0)
    hidden_date = _parse_date(row["PlacedDate"])
    country = row["Country"] or ""
    state = row["State"] or ""
    county = row["County"] or ""
    memo = memos.get(gc_code)
    long_desc = (memo and memo["LongDescription"]) or ""
    hint = (memo and memo["Hints"]) or ""

    # Count parseable stage waypoints for stage_count
    parseable_stages = sum(
        1 for wp in waypoints_by_code.get(gc_code, [])
        if _s_code_to_stage(wp["cCode"] or "", base) is not None
    )

    adv = _get_or_create_adventure(
        code=f"LC{base}",
        title=row["Name"] or "",
        owner=owner,
        lat=lat,
        lon=lon,
        now=now,
        status=status,
        description=long_desc,
        stage_count=parseable_stages or None,
    )

    # Save the adventure parent as a Geocache (LC{base})
    _upsert_parent_geocache(
        adv=adv, owner=owner, placed_by=placed_by, status=status,
        hidden_date=hidden_date, country=country, state=state, county=county,
        long_description=long_desc, hint=hint,
        now=now, tags=tags,
    )

    found_from_parent = bool(row["Found"])
    found_date_from_parent = _parse_date(row["FoundByMeDate"])

    logs_data = _build_log_dicts(gc_code, logs_by_code, log_texts)

    for wp_row in waypoints_by_code.get(gc_code, []):
        s_code = wp_row["cCode"] or ""
        parsed = _s_code_to_stage(s_code, base)
        if parsed is None:
            continue
        _, n = parsed
        canonical_code = f"{adv.code}-{n}"

        stage_lat = _parse_float(wp_row["cLat"]) or lat
        stage_lon = _parse_float(wp_row["cLon"]) or lon
        question_text = wp_row["cName"] or ""

        fields = {
            "name":              question_text or f"Stage {n}",
            "owner":             owner,
            "placed_by":         placed_by,
            "cache_type":        CacheType.LAB,
            "status":            status,
            "latitude":          stage_lat,
            "longitude":         stage_lon,
            "hidden_date":       hidden_date,
            "country":           country,
            "iso_country_code":  _country_to_iso(country),
            "state":             state,
            "county":            county,
            "last_gpx_date":     now,
            "short_description": (memo and memo["ShortDescription"]) or "",
            "long_description":  long_desc,
            "hint":              hint,
            "adventure":         adv,
            "stage_number":      n,
            "question_text":     question_text,
            "al_stage_uuid":     "",
            "al_journal_text":   (memo and memo["UserNote"]) or "",
        }

        try:
            with transaction.atomic():
                result = _save(
                    al_code=canonical_code,
                    fields=fields,
                    found=found_from_parent or None,
                    found_date=found_date_from_parent,
                    tags=tags,
                    logs=logs_data or None,
                )

                if result.locked:
                    stats.locked += 1
                elif result.created:
                    stats.created += 1
                else:
                    stats.updated += 1

        except Exception as exc:
            stats.errors.append(f"{canonical_code}: {exc}")

    from geocaches.models import recompute_adventure_completed
    recompute_adventure_completed(adv)


def _build_log_dicts(source_code, logs_by_code, log_texts) -> list[dict]:
    """Convert GSAK log rows to dicts suitable for services.save_geocache(logs=...)."""
    result = []
    for log_row in logs_by_code.get(source_code, []):
        log_date = _parse_date(log_row["lDate"])
        if log_date is None:
            continue
        result.append({
            "log_type": log_row["lType"],
            "user_name": log_row["lBy"] or "",
            "logged_date": log_date,
            "text": log_texts.get((source_code, log_row["lLogId"]), "") or "",
            "source_id": str(log_row["lLogId"]),
        })
    return result


# ---------------------------------------------------------------------------
# Standard (non-ALC) cache
# ---------------------------------------------------------------------------

def _save_cache(
    row, gsak_code, gc_code, oc_code, lat, lon, now, tags,
    memos, log_texts, logs_by_code,
    waypoints_by_code, corrected, attrs_by_code, images_by_code,
) -> str:
    """Persist one cache and all its related data. Returns 'created'/'updated'/'locked'.

    Adapter that translates GSAK row data into a canonical
    services.save_geocache() call.
    ``gsak_code`` is the original Code from the GSAK database, used to look up
    related rows (memos, logs, waypoints, etc.).
    """
    from geocaches.models import Attribute, CacheType
    from geocaches.services import save_geocache as _save

    memo = memos.get(gsak_code)

    try:
        owner_gc_id = int(row["OwnerId"]) if row["OwnerId"] else None
    except (ValueError, TypeError):
        owner_gc_id = None

    fields = {
        "name":              row["Name"] or "",
        "owner":             row["OwnerName"] or "",
        "placed_by":         row["PlacedBy"] or "",
        "owner_gc_id":       owner_gc_id,
        "cache_type":        _GSAK_CACHE_TYPE.get(row["CacheType"], CacheType.UNKNOWN),
        "size":              gpx_container_to_size(row["Container"] or ""),
        "status":            _status(row["Archived"] or 0, row["TempDisabled"] or 0),
        "latitude":          lat,
        "longitude":         lon,
        "difficulty":        _parse_float(row["Difficulty"]),
        "terrain":           _parse_float(row["Terrain"]),
        "hidden_date":       _parse_date(row["PlacedDate"]),
        "last_found_date":   _parse_date(row["LastFoundDate"]),
        "country":           row["Country"] or "",
        "iso_country_code":  _country_to_iso(row["Country"] or ""),
        "found_count":       row["FoundCount"] or 0,
    }

    # Only include enrichment-owned fields when GSAK actually has data,
    # so we don't overwrite values populated by GCForge's enrichment process.
    gsak_state = row["State"] or ""
    gsak_county = row["County"] or ""
    gsak_elevation = _parse_float(row["Elevation"])
    if gsak_state:
        fields["state"] = gsak_state
    if gsak_county:
        fields["county"] = gsak_county
    if gsak_elevation is not None:
        fields["elevation"] = gsak_elevation

    fields.update({
        "ftf":               bool(row["FTF"]),
        "dnf":               bool(row["DNF"]),
        "dnf_date":          _parse_date(row["DNFDate"]),
        "user_flag":         bool(row["UserFlag"]),
        "watch":             bool(row["Watch"]),
        "gc_note":           row["GcNote"] or "",
        "user_sort":         row["UserSort"],
        "color":             row["Color"] or "",
        "import_locked":     bool(row["Lock"]),
        "is_premium":        bool(row["IsPremium"]),
        "has_trackable":     bool(row["HasTravelBug"]),
        "fav_points":        row["FavPoints"] or 0,
        "platform_log_count": row["NumberOfLogs"] or 0,
        "last_gpx_date":     now,
        "short_description": (memo and memo["ShortDescription"]) or "",
        "long_description":  (memo and memo["LongDescription"]) or "",
        "hint":              (memo and memo["Hints"]) or "",
    })

    found_from_gsak = bool(row["Found"])
    found_date_from_gsak = _parse_date(row["FoundByMeDate"])

    # Logs
    logs_data = _build_log_dicts(gsak_code, logs_by_code, log_texts)

    # Attributes
    attr_dicts = [
        {
            "source": Attribute.Source.GC,
            "attribute_id": attr_row["aId"],
            "is_positive": bool(attr_row["aInc"]),
            "name": f"Attribute #{attr_row['aId']}",
        }
        for attr_row in attrs_by_code.get(gsak_code, [])
    ]

    # Waypoints
    wpts_data = [
        {
            "lookup": wp_row["cCode"] or "",
            "prefix": wp_row["cPrefix"] or "",
            "name": wp_row["cName"] or "",
            "waypoint_type": gpx_sym_to_waypoint_type(wp_row["cType"] or ""),
            "latitude": _parse_float(wp_row["cLat"]),
            "longitude": _parse_float(wp_row["cLon"]),
            "is_user_created": bool(wp_row["cByuser"]),
        }
        for wp_row in waypoints_by_code.get(gsak_code, [])
        if wp_row["cCode"]
    ]

    # Corrected coordinates
    # GSAK overwrites Caches.Latitude/Longitude with the corrected values
    # when HasCorrected=1, so Caches.Latitude == Corrected.kAfterLat.
    # We must restore the *original* coords from Corrected.kBeforeLat/kBeforeLon.
    corr_dict = None
    if row["HasCorrected"]:
        corr = corrected.get(gsak_code)
        if corr:
            corr_lat = _parse_float(corr["kAfterLat"])
            corr_lon = _parse_float(corr["kAfterLon"])
            if corr_lat is not None and corr_lon is not None:
                corr_dict = {"latitude": corr_lat, "longitude": corr_lon}
                # Restore original (pre-correction) coords as the primary position
                before_lat = _parse_float(corr["kBeforeLat"])
                before_lon = _parse_float(corr["kBeforeLon"])
                if before_lat is not None and before_lon is not None:
                    fields["latitude"] = before_lat
                    fields["longitude"] = before_lon

    # Images
    img_dicts = [
        {
            "url": img_row["iImage"] or "",
            "name": img_row["iName"] or "",
            "description": img_row["iDescription"] or "",
        }
        for img_row in images_by_code.get(gsak_code, [])
        if img_row["iImage"]
    ]

    # Notes
    note_dicts = None
    if memo and memo["UserNote"]:
        note_dicts = split_gsak_note(memo["UserNote"]) or None

    with transaction.atomic():
        result = _save(
            gc_code=gc_code,
            oc_code=oc_code,
            fields=fields,
            found=found_from_gsak or None,
            found_date=found_date_from_gsak,
            tags=tags,
            logs=logs_data or None,
            waypoints=wpts_data or None,
            attributes=attr_dicts or None,
            corrected_coords=corr_dict,
            images=img_dicts or None,
            notes=note_dicts,
            skip_notes_if_exist=True,
        )

    if result.locked:
        return "locked"
    elif result.created:
        return "created"
    else:
        return "updated"
