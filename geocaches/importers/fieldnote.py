"""Field note file parser — supports GC.com / c:geo / Locus format.

Format (one entry per line):
    {cache_code},{datetime}Z,{log_type},"{text}"

Encoding: UTF-16 LE (with or without BOM) is the standard; UTF-8 with/without
BOM is accepted as fallback.
"""
from __future__ import annotations

import csv
import io
import logging
import re
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

# Log type aliases used by c:geo / Locus / GC website → canonical GCForge value
_LOG_TYPE_MAP = {
    "found it": "Found it",
    "didn't find it": "Didn't find it",
    "write note": "Write note",
    "will attend": "Will Attend",
    "attended": "Attended",
    "webcam photo taken": "Webcam Photo Taken",
    "needs maintenance": "Needs Maintenance",
    "owner maintenance": "Owner Maintenance",
}

# GC-code-like pattern: GC / OC / OP / OK / OB / OR followed by alphanumerics
_CODE_RE = re.compile(r"^(GC|LC|OC|OP|OK|OB|OR)[A-Z0-9]+$", re.IGNORECASE)

# Map cache-code prefix → OC domain (for building external URLs)
_OC_DOMAINS = {
    "OC": "www.opencaching.de",
    "OP": "www.opencaching.pl",
    "OK": "www.opencaching.us",
    "ON": "www.opencaching.nl",
    "OB": "opencache.uk",
    "OR": "www.opencaching.ro",
}

# Map cache-code prefix → platform identifier (for map_sync)
_CODE_PREFIX_TO_PLATFORM = {
    "GC": "gc",
    "OC": "oc_de",
    "OP": "oc_pl",
    "OK": "oc_us",
    "ON": "oc_nl",
    "OB": "oc_uk",
    "OR": "oc_ro",
}


def external_url_for_code(cache_code: str) -> str:
    """Return the external website URL for a GC/OC cache code."""
    prefix = cache_code[:2].upper()
    if prefix == "GC":
        return f"https://www.geocaching.com/geocache/{cache_code}"
    domain = _OC_DOMAINS.get(prefix, "www.opencaching.de")
    return f"https://{domain}/viewcache.php?wp={cache_code}"


def platform_for_code(cache_code: str) -> str:
    """Return the platform identifier for a cache code prefix."""
    return _CODE_PREFIX_TO_PLATFORM.get(cache_code[:2].upper(), "gc")


@dataclass
class FieldNoteEntry:
    cache_code: str
    logged_at: datetime          # UTC-aware
    log_type: str                # canonical GCForge LogType value
    text: str

    @property
    def external_url(self) -> str:
        return external_url_for_code(self.cache_code)

    @property
    def platform(self) -> str:
        return platform_for_code(self.cache_code)


@dataclass
class FieldNoteImportResult:
    imported: int = 0
    skipped: int = 0              # cache not found in DB or duplicate
    errors: list[str] = field(default_factory=list)
    entries: list[FieldNoteEntry] = field(default_factory=list)
    skipped_existing: list[str] = field(default_factory=list)  # already in DB
    not_found_entries: list[FieldNoteEntry] = field(default_factory=list)  # cache not in DB


def _decode(raw: bytes) -> str:
    """Decode field note bytes, trying common encodings in order."""
    # UTF-16 LE BOM
    if raw[:2] == b"\xff\xfe":
        return raw[2:].decode("utf-16-le")
    # UTF-16 BE BOM
    if raw[:2] == b"\xfe\xff":
        return raw[2:].decode("utf-16-be")
    # Heuristic: if every other byte is 0x00, assume UTF-16 LE without BOM
    if len(raw) >= 4 and raw[1] == 0 and raw[3] == 0:
        return raw.decode("utf-16-le")
    # UTF-8 BOM
    if raw[:3] == b"\xef\xbb\xbf":
        return raw[3:].decode("utf-8")
    return raw.decode("utf-8", errors="replace")


def parse_fieldnote_bytes(raw: bytes) -> list[FieldNoteEntry]:
    """Parse raw field note bytes into a list of FieldNoteEntry objects."""
    text = _decode(raw)
    entries: list[FieldNoteEntry] = []

    reader = csv.reader(io.StringIO(text))
    for lineno, row in enumerate(reader, 1):
        # Strip whitespace from each cell (UTF-16 decode may leave leading space)
        row = [c.strip() for c in row]
        if not row or not row[0]:
            continue
        if len(row) < 3:
            logger.debug("Field note line %d: too few columns, skipping", lineno)
            continue

        cache_code = row[0].strip().upper()
        if not _CODE_RE.match(cache_code):
            logger.debug("Field note line %d: unrecognised code %r, skipping", lineno, cache_code)
            continue

        # Parse datetime — format: "2026-03-18T21:51:15Z"
        dt_str = row[1].strip().rstrip("Z")
        parsed_at = None
        for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M", "%Y-%m-%dT%H:%M:%S.%f"):
            try:
                parsed_at = datetime.strptime(dt_str, fmt).replace(tzinfo=timezone.utc)
                break
            except ValueError:
                continue
        if parsed_at is None:
            logger.debug("Field note line %d: cannot parse datetime %r, skipping", lineno, row[1])
            continue
        logged_at = parsed_at

        raw_type = row[2].strip()
        log_type = _LOG_TYPE_MAP.get(raw_type.lower(), raw_type)

        text = row[3].strip() if len(row) > 3 else ""

        entries.append(FieldNoteEntry(
            cache_code=cache_code,
            logged_at=logged_at,
            log_type=log_type,
            text=text,
        ))

    return entries


def parse_fieldnote_file(path: str | Path) -> list[FieldNoteEntry]:
    """Read and parse a field note file."""
    raw = Path(path).read_bytes()
    return parse_fieldnote_bytes(raw)


def analyze_fieldnote_file(path: str | Path) -> FieldNoteImportResult:
    """Parse a field note file and check which entries are in the DB.

    Does NOT create any Note records and does NOT move the file.
    Populates result.not_found_entries with entries whose cache isn't in the DB.
    """
    from geocaches.models import Geocache, Note

    result = FieldNoteImportResult()
    path = Path(path)

    try:
        entries = parse_fieldnote_file(path)
    except Exception as exc:
        result.errors.append(f"Could not read {path.name}: {exc}")
        return result

    result.entries = entries

    for entry in entries:
        cache = (
            Geocache.objects.filter(gc_code__iexact=entry.cache_code).first()
            or Geocache.objects.filter(oc_code__iexact=entry.cache_code).first()
        )
        if cache is None:
            result.not_found_entries.append(entry)
            result.skipped += 1
            continue

        exists = cache.notes.filter(
            note_type="field_note",
            logged_at=entry.logged_at,
            log_type=entry.log_type,
        ).exists()
        if exists:
            result.skipped += 1
            dt_str = entry.logged_at.strftime("%Y-%m-%d %H:%M") if entry.logged_at else "?"
            result.skipped_existing.append(
                f"{entry.cache_code} ({dt_str} UTC, {entry.log_type})"
            )
        else:
            result.imported += 1  # would be imported

    return result


def _get_or_create_placeholder(cache_code: str):
    """Return an existing Geocache for cache_code or create a minimal placeholder."""
    from geocaches.models import Geocache
    cache_code = cache_code.upper()
    cache = (
        Geocache.objects.filter(gc_code__iexact=cache_code).first()
        or Geocache.objects.filter(oc_code__iexact=cache_code).first()
    )
    if cache:
        return cache
    kwargs = {
        "name": cache_code,
        "latitude": 0.0,
        "longitude": 0.0,
        "cache_type": "Traditional Cache",
        "is_placeholder": True,
    }
    if cache_code.startswith("GC"):
        kwargs["gc_code"] = cache_code
    else:
        kwargs["oc_code"] = cache_code
    return Geocache.objects.create(**kwargs)


def import_fieldnote_file(
    path: str | Path,
    mode: str = "skip_missing",
) -> FieldNoteImportResult:
    """Parse a field note file and create Note objects for each entry.

    mode:
      "skip_missing"      — skip entries where the cache isn't in the DB (default)
      "import_all"        — create a placeholder Geocache for unknown caches

    After a successful import the source file is moved to the processed
    subfolder next to the fieldnotes root.
    """
    from geocaches.models import Geocache, Note

    result = FieldNoteImportResult()
    path = Path(path)

    try:
        entries = parse_fieldnote_file(path)
    except Exception as exc:
        result.errors.append(f"Could not read {path.name}: {exc}")
        return result

    result.entries = entries

    for entry in entries:
        # Look up by GC or OC code
        cache = (
            Geocache.objects.filter(gc_code__iexact=entry.cache_code).first()
            or Geocache.objects.filter(oc_code__iexact=entry.cache_code).first()
        )
        if cache is None:
            if mode == "import_all":
                cache = _get_or_create_placeholder(entry.cache_code)
            else:
                result.not_found_entries.append(entry)
                result.skipped += 1
                logger.debug("Field note: cache %s not found in DB, skipping", entry.cache_code)
                continue

        # Skip if this exact note already exists (idempotent re-import)
        exists = cache.notes.filter(
            note_type="field_note",
            logged_at=entry.logged_at,
            log_type=entry.log_type,
        ).exists()
        if exists:
            result.skipped += 1
            dt_str = entry.logged_at.strftime("%Y-%m-%d %H:%M") if entry.logged_at else "?"
            result.skipped_existing.append(
                f"{entry.cache_code} ({dt_str} UTC, {entry.log_type})"
            )
            continue

        Note.objects.create(
            geocache=cache,
            note_type="field_note",
            format="plain",
            log_type=entry.log_type,
            logged_at=entry.logged_at,
            body=entry.text,
        )
        result.imported += 1

    # Move to processed folder
    processed_dir = _fieldnotes_dir() / "processed"
    processed_dir.mkdir(parents=True, exist_ok=True)
    dest = processed_dir / path.name
    if dest.exists():
        stem = path.stem
        suffix = path.suffix
        from datetime import datetime as _dt
        dest = processed_dir / f"{stem}_{_dt.now().strftime('%Y%m%d_%H%M%S')}{suffix}"
    try:
        shutil.move(str(path), str(dest))
    except Exception as exc:
        logger.warning("Could not move %s to processed: %s", path, exc)

    return result


def _fieldnotes_dir() -> Path:
    from django.conf import settings
    return Path(settings.BASE_DIR) / "fieldnotes"


def download_gc_fieldnotes() -> Path:
    """Fetch log drafts from the GC API and save as a field note file.

    Returns the path of the saved file.
    Raises an exception if the API call fails or returns no drafts.
    """
    from geocaches.sync.gc_client import GCClient
    from datetime import datetime as _dt

    client = GCClient()
    drafts = client.get_log_drafts()

    if not drafts:
        raise ValueError("No log drafts found on geocaching.com.")

    # GC API logdrafts bug: loggedDate contains the user's *local* time but is
    # labelled "Z" (UTC).  Correct this per cache by treating the value as local
    # time in the cache's timezone and converting to true UTC.
    from geocaches.models import Geocache
    from geocaches.sync.log_submit import cache_timezone as _cache_tz

    _tz_cache: dict = {}

    def _gc_draft_to_utc(gc_code: str, raw_dt: str) -> str:
        naive_str = raw_dt.rstrip("Z").split(".")[0]
        try:
            naive = datetime.strptime(naive_str, "%Y-%m-%dT%H:%M:%S")
        except ValueError:
            try:
                naive = datetime.strptime(naive_str, "%Y-%m-%dT%H:%M")
            except ValueError:
                return naive_str + "Z"
        if gc_code not in _tz_cache:
            obj = Geocache.objects.filter(gc_code__iexact=gc_code).first()
            _tz_cache[gc_code] = _cache_tz(obj.latitude, obj.longitude) if obj else timezone.utc
        utc_dt = naive.replace(tzinfo=_tz_cache[gc_code]).astimezone(timezone.utc)
        return utc_dt.strftime("%Y-%m-%dT%H:%M:%SZ")

    lines = []
    for d in drafts:
        gc_code = d.get("geocacheCode", "").strip()
        if not gc_code:
            continue
        logged_date = _gc_draft_to_utc(gc_code, d.get("loggedDate", ""))
        log_type = d.get("geocacheLogType", {}).get("name", "Write note")
        text = d.get("note", "") or ""
        text_escaped = text.replace('"', '""')
        lines.append(f'{gc_code},{logged_date},{log_type},"{text_escaped}"')

    content = "\n".join(lines) + "\n"

    fn = _dt.now().strftime("gc_%Y%m%d_%H%M%S.txt")
    out_path = _fieldnotes_dir() / fn
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(content, encoding="utf-8")
    logger.info("Fetched %d GC log drafts → %s", len(drafts), out_path)
    return out_path
