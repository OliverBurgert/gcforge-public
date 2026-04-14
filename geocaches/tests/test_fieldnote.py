"""
Tests for geocaches.importers.fieldnote — parser, analyzer, and importer.
"""

from __future__ import annotations

import tempfile
from datetime import datetime, timezone
from pathlib import Path

from django.test import TestCase

from geocaches.importers.fieldnote import (
    FieldNoteEntry,
    FieldNoteImportResult,
    _decode,
    analyze_fieldnote_file,
    external_url_for_code,
    import_fieldnote_file,
    parse_fieldnote_bytes,
    platform_for_code,
)
from geocaches.models import Geocache, Note


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _gc(gc_code="GC12345", name="Test Cache", lat=48.5, lon=9.1) -> Geocache:
    """Create and return a minimal Geocache with a gc_code."""
    return Geocache.objects.create(
        gc_code=gc_code,
        name=name,
        latitude=lat,
        longitude=lon,
        cache_type="Traditional Cache",
    )


def _oc(oc_code="OCABC1", name="OC Cache", lat=48.5, lon=9.1) -> Geocache:
    """Create and return a minimal Geocache with an oc_code."""
    return Geocache.objects.create(
        oc_code=oc_code,
        name=name,
        latitude=lat,
        longitude=lon,
        cache_type="Traditional Cache",
    )


def _fieldnote_file(content: str, suffix=".txt") -> Path:
    """Write content to a temp file and return the path (UTF-8)."""
    f = tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", suffix=suffix, delete=False
    )
    f.write(content)
    f.close()
    return Path(f.name)


def _fieldnote_file_bytes(data: bytes, suffix=".txt") -> Path:
    """Write raw bytes to a temp file and return the path."""
    f = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
    f.write(data)
    f.close()
    return Path(f.name)


# ---------------------------------------------------------------------------
# _decode — encoding detection
# ---------------------------------------------------------------------------

class TestDecode(TestCase):

    def test_utf16_le_with_bom(self):
        text = 'GC12345,2024-01-15T10:30Z,Found it,"Great cache"'
        raw = b"\xff\xfe" + text.encode("utf-16-le")
        result = _decode(raw)
        self.assertEqual(result, text)

    def test_utf16_le_without_bom(self):
        # Every other byte is 0x00 for ASCII text encoded as UTF-16 LE
        text = "GC12345,2024-01-15T10:30Z,Found it"
        raw = text.encode("utf-16-le")
        result = _decode(raw)
        self.assertEqual(result, text)

    def test_utf8_with_bom(self):
        text = 'GC12345,2024-01-15T10:30Z,Found it,"Great cache"'
        raw = b"\xef\xbb\xbf" + text.encode("utf-8")
        result = _decode(raw)
        self.assertEqual(result, text)

    def test_utf8_without_bom(self):
        text = 'GC12345,2024-01-15T10:30Z,Found it,"Great cache"'
        raw = text.encode("utf-8")
        result = _decode(raw)
        self.assertEqual(result, text)

    def test_plain_ascii(self):
        text = "GC12345,2024-01-15T10:30Z,Write note,Hello"
        raw = text.encode("ascii")
        result = _decode(raw)
        self.assertEqual(result, text)


# ---------------------------------------------------------------------------
# parse_fieldnote_bytes — main parser
# ---------------------------------------------------------------------------

class TestParseFieldnoteBytes(TestCase):

    def _parse(self, text: str) -> list[FieldNoteEntry]:
        return parse_fieldnote_bytes(text.encode("utf-8"))

    # --- Standard / happy-path ---

    def test_standard_line(self):
        entries = self._parse('GC12345,2024-01-15T10:30Z,Found it,"Great cache"')
        self.assertEqual(len(entries), 1)
        e = entries[0]
        self.assertEqual(e.cache_code, "GC12345")
        self.assertEqual(e.logged_at, datetime(2024, 1, 15, 10, 30, tzinfo=timezone.utc))
        self.assertEqual(e.log_type, "Found it")
        self.assertEqual(e.text, "Great cache")

    def test_datetime_with_seconds(self):
        entries = self._parse('GC12345,2024-01-15T10:30:45Z,Write note,"note"')
        self.assertEqual(entries[0].logged_at, datetime(2024, 1, 15, 10, 30, 45, tzinfo=timezone.utc))

    def test_empty_text_field(self):
        entries = self._parse('GC12345,2024-01-15T10:30Z,Write note,')
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].text, "")

    def test_text_with_commas_inside_quoted_field(self):
        entries = self._parse('GC12345,2024-01-15T10:30Z,Write note,"Hello, world, again"')
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].text, "Hello, world, again")

    # --- Log type normalisation ---

    def test_found_it_case_insensitive(self):
        entries = self._parse('GC12345,2024-01-15T10:30Z,found it,"text"')
        self.assertEqual(entries[0].log_type, "Found it")

    def test_didnt_find_it_normalised(self):
        entries = self._parse("GC12345,2024-01-15T10:30Z,didn't find it,")
        self.assertEqual(entries[0].log_type, "Didn't find it")

    def test_write_note_normalised(self):
        entries = self._parse("GC12345,2024-01-15T10:30Z,write note,")
        self.assertEqual(entries[0].log_type, "Write note")

    def test_will_attend_normalised(self):
        entries = self._parse("GC12345,2024-01-15T10:30Z,will attend,")
        self.assertEqual(entries[0].log_type, "Will Attend")

    def test_attended_normalised(self):
        entries = self._parse("GC12345,2024-01-15T10:30Z,attended,")
        self.assertEqual(entries[0].log_type, "Attended")

    def test_webcam_photo_taken_normalised(self):
        entries = self._parse("GC12345,2024-01-15T10:30Z,webcam photo taken,")
        self.assertEqual(entries[0].log_type, "Webcam Photo Taken")

    def test_needs_maintenance_normalised(self):
        entries = self._parse("GC12345,2024-01-15T10:30Z,needs maintenance,")
        self.assertEqual(entries[0].log_type, "Needs Maintenance")

    def test_owner_maintenance_normalised(self):
        entries = self._parse("GC12345,2024-01-15T10:30Z,owner maintenance,")
        self.assertEqual(entries[0].log_type, "Owner Maintenance")

    def test_unknown_log_type_passed_through(self):
        # Unknown types are not in the map — they are stored as-is (no skip)
        entries = self._parse('GC12345,2024-01-15T10:30Z,Mystery Type,"text"')
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].log_type, "Mystery Type")

    # --- Invalid / skipped lines ---

    def test_invalid_code_skipped(self):
        entries = self._parse('INVALID,2024-01-15T10:30Z,Found it,"text"')
        self.assertEqual(entries, [])

    def test_missing_code_skipped(self):
        entries = self._parse(',2024-01-15T10:30Z,Found it,"text"')
        self.assertEqual(entries, [])

    def test_bad_date_skipped(self):
        entries = self._parse('GC12345,not-a-date,Found it,"text"')
        self.assertEqual(entries, [])

    def test_too_few_columns_skipped(self):
        entries = self._parse("GC12345,2024-01-15T10:30Z")
        self.assertEqual(entries, [])

    def test_empty_line_skipped(self):
        entries = self._parse("")
        self.assertEqual(entries, [])

    # --- Cache code variants ---

    def test_oc_prefixed_code(self):
        entries = self._parse('OCABC1,2024-01-15T10:30Z,Found it,"OC cache"')
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].cache_code, "OCABC1")

    def test_lc_prefixed_code_included(self):
        # LC is in _CODE_RE so it is parsed, not skipped
        entries = self._parse('LC1234,2024-01-15T10:30Z,Found it,"LC cache"')
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].cache_code, "LC1234")

    def test_op_prefixed_code(self):
        entries = self._parse('OPXYZ1,2024-01-15T10:30Z,Found it,')
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].cache_code, "OPXYZ1")

    def test_code_uppercased(self):
        entries = self._parse('gc12345,2024-01-15T10:30Z,Found it,')
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].cache_code, "GC12345")

    # --- Multi-line / mixed input ---

    def test_multi_line_mixed_valid_and_invalid(self):
        text = (
            'GC00001,2024-01-15T10:30Z,Found it,"Cache 1"\n'
            'INVALID,2024-01-15T10:30Z,Found it,"Bad"\n'
            'GC00002,not-a-date,Found it,"Bad date"\n'
            'GC00003,2024-01-16T08:00Z,Write note,"Cache 3"\n'
        )
        entries = self._parse(text)
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0].cache_code, "GC00001")
        self.assertEqual(entries[1].cache_code, "GC00003")

    def test_utf16_le_bom_multiline(self):
        text = (
            'GC12345,2024-01-15T10:30Z,Found it,"First"\n'
            'GC67890,2024-01-16T08:00Z,Write note,"Second"\n'
        )
        raw = b"\xff\xfe" + text.encode("utf-16-le")
        entries = parse_fieldnote_bytes(raw)
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0].cache_code, "GC12345")
        self.assertEqual(entries[1].cache_code, "GC67890")


# ---------------------------------------------------------------------------
# FieldNoteEntry properties
# ---------------------------------------------------------------------------

class TestFieldNoteEntryProperties(TestCase):

    def _make(self, code: str) -> FieldNoteEntry:
        return FieldNoteEntry(
            cache_code=code,
            logged_at=datetime(2024, 1, 15, 10, 30, tzinfo=timezone.utc),
            log_type="Found it",
            text="",
        )

    def test_gc_external_url(self):
        e = self._make("GC12345")
        self.assertEqual(e.external_url, "https://www.geocaching.com/geocache/GC12345")

    def test_oc_external_url(self):
        e = self._make("OCABC1")
        self.assertIn("opencaching.de", e.external_url)
        self.assertIn("OCABC1", e.external_url)

    def test_gc_platform(self):
        self.assertEqual(self._make("GC12345").platform, "gc")

    def test_oc_platform(self):
        self.assertEqual(self._make("OCABC1").platform, "oc_de")

    def test_op_platform(self):
        self.assertEqual(self._make("OPXYZ1").platform, "oc_pl")


# ---------------------------------------------------------------------------
# external_url_for_code / platform_for_code
# ---------------------------------------------------------------------------

class TestCodeHelpers(TestCase):

    def test_gc_url(self):
        url = external_url_for_code("GC12345")
        self.assertEqual(url, "https://www.geocaching.com/geocache/GC12345")

    def test_oc_de_url(self):
        url = external_url_for_code("OCABC1")
        self.assertEqual(url, "https://www.opencaching.de/viewcache.php?wp=OCABC1")

    def test_op_pl_url(self):
        url = external_url_for_code("OPXYZ1")
        self.assertEqual(url, "https://www.opencaching.pl/viewcache.php?wp=OPXYZ1")

    def test_ob_uk_url(self):
        url = external_url_for_code("OB1234")
        self.assertEqual(url, "https://opencache.uk/viewcache.php?wp=OB1234")

    def test_gc_platform(self):
        self.assertEqual(platform_for_code("GC12345"), "gc")

    def test_oc_de_platform(self):
        self.assertEqual(platform_for_code("OCABC1"), "oc_de")

    def test_oc_pl_platform(self):
        self.assertEqual(platform_for_code("OPXYZ1"), "oc_pl")

    def test_unknown_prefix_defaults_to_gc(self):
        # Any unrecognised prefix falls back to "gc"
        self.assertEqual(platform_for_code("XX1234"), "gc")


# ---------------------------------------------------------------------------
# analyze_fieldnote_file
# ---------------------------------------------------------------------------

class TestAnalyzeFieldnoteFile(TestCase):

    def test_entries_populated(self):
        path = _fieldnote_file(
            'GC12345,2024-01-15T10:30Z,Found it,"Great"\n'
            'GC99999,2024-01-16T08:00Z,Write note,"Note"\n'
        )
        result = analyze_fieldnote_file(path)
        self.assertEqual(len(result.entries), 2)

    def test_cache_not_in_db_goes_to_not_found(self):
        path = _fieldnote_file('GC99999,2024-01-15T10:30Z,Found it,""\n')
        result = analyze_fieldnote_file(path)
        self.assertEqual(len(result.not_found_entries), 1)
        self.assertEqual(result.not_found_entries[0].cache_code, "GC99999")
        self.assertEqual(result.skipped, 1)

    def test_cache_in_db_counted_as_would_import(self):
        _gc("GC12345")
        path = _fieldnote_file('GC12345,2024-01-15T10:30Z,Found it,""\n')
        result = analyze_fieldnote_file(path)
        self.assertEqual(result.imported, 1)  # "would be imported"
        self.assertEqual(result.skipped, 0)
        self.assertEqual(result.not_found_entries, [])

    def test_existing_note_goes_to_skipped_existing(self):
        cache = _gc("GC12345")
        logged_at = datetime(2024, 1, 15, 10, 30, tzinfo=timezone.utc)
        Note.objects.create(
            geocache=cache,
            note_type="field_note",
            format="plain",
            log_type="Found it",
            logged_at=logged_at,
            body="",
        )
        path = _fieldnote_file('GC12345,2024-01-15T10:30Z,Found it,""\n')
        result = analyze_fieldnote_file(path)
        self.assertEqual(len(result.skipped_existing), 1)
        self.assertIn("GC12345", result.skipped_existing[0])
        self.assertEqual(result.skipped, 1)
        self.assertEqual(result.imported, 0)

    def test_mixed_found_not_found_existing(self):
        cache = _gc("GC00001")
        logged_at = datetime(2024, 1, 15, 10, 30, tzinfo=timezone.utc)
        Note.objects.create(
            geocache=cache,
            note_type="field_note",
            format="plain",
            log_type="Found it",
            logged_at=logged_at,
            body="",
        )
        path = _fieldnote_file(
            'GC00001,2024-01-15T10:30Z,Found it,"already exists"\n'  # skipped_existing
            'GC99999,2024-01-16T08:00Z,Write note,"not in db"\n'      # not_found
            'GC00001,2024-01-17T09:00Z,Write note,"new note"\n'       # would import
        )
        result = analyze_fieldnote_file(path)
        self.assertEqual(result.imported, 1)
        self.assertEqual(result.skipped, 2)
        self.assertEqual(len(result.skipped_existing), 1)
        self.assertEqual(len(result.not_found_entries), 1)

    def test_oc_code_matched_by_oc_code_field(self):
        _oc("OCABC1")
        path = _fieldnote_file('OCABC1,2024-01-15T10:30Z,Found it,""\n')
        result = analyze_fieldnote_file(path)
        self.assertEqual(result.imported, 1)
        self.assertEqual(result.not_found_entries, [])

    def test_unreadable_file_returns_error(self):
        result = analyze_fieldnote_file("/nonexistent/path/fieldnotes.txt")
        self.assertTrue(len(result.errors) > 0)


# ---------------------------------------------------------------------------
# import_fieldnote_file — integration tests
# ---------------------------------------------------------------------------

class TestImportFieldnoteFile(TestCase):

    def test_creates_note_for_existing_cache(self):
        _gc("GC12345")
        path = _fieldnote_file('GC12345,2024-01-15T10:30Z,Found it,"Great cache"\n')
        result = import_fieldnote_file(path)
        self.assertEqual(result.imported, 1)
        self.assertEqual(result.skipped, 0)
        note = Note.objects.filter(geocache__gc_code="GC12345").first()
        self.assertIsNotNone(note)
        self.assertEqual(note.note_type, "field_note")
        self.assertEqual(note.log_type, "Found it")
        self.assertEqual(note.body, "Great cache")
        self.assertEqual(note.logged_at, datetime(2024, 1, 15, 10, 30, tzinfo=timezone.utc))

    def test_write_note_log_type_stored_correctly(self):
        _gc("GC12345")
        path = _fieldnote_file('GC12345,2024-01-15T10:30Z,Write note,"Just a note"\n')
        import_fieldnote_file(path)
        note = Note.objects.filter(geocache__gc_code="GC12345").first()
        self.assertIsNotNone(note)
        self.assertEqual(note.log_type, "Write note")

    def test_skips_cache_not_in_db(self):
        path = _fieldnote_file('GC99999,2024-01-15T10:30Z,Found it,""\n')
        result = import_fieldnote_file(path)
        self.assertEqual(result.imported, 0)
        self.assertEqual(result.skipped, 1)
        self.assertEqual(Note.objects.count(), 0)

    def test_idempotent_no_duplicate_on_reimport(self):
        _gc("GC12345")
        content = 'GC12345,2024-01-15T10:30Z,Found it,"Great cache"\n'

        # First import
        path1 = _fieldnote_file(content)
        result1 = import_fieldnote_file(path1)
        self.assertEqual(result1.imported, 1)
        self.assertEqual(result1.skipped, 0)

        # Second import of same entry
        path2 = _fieldnote_file(content)
        result2 = import_fieldnote_file(path2)
        self.assertEqual(result2.imported, 0)
        self.assertEqual(result2.skipped, 1)
        self.assertIn("GC12345", result2.skipped_existing[0])

        # Only one Note in DB
        self.assertEqual(Note.objects.filter(geocache__gc_code="GC12345").count(), 1)

    def test_imported_and_skipped_counts_correct(self):
        _gc("GC00001")
        # GC00002 is not in DB, GC99999 is not in DB
        content = (
            'GC00001,2024-01-15T10:30Z,Found it,""\n'
            'GC00002,2024-01-16T08:00Z,Write note,""\n'
            'GC99999,2024-01-17T09:00Z,Found it,""\n'
        )
        path = _fieldnote_file(content)
        result = import_fieldnote_file(path)
        self.assertEqual(result.imported, 1)
        self.assertEqual(result.skipped, 2)

    def test_import_all_mode_creates_placeholder(self):
        content = 'GC99999,2024-01-15T10:30Z,Found it,"new placeholder"\n'
        path = _fieldnote_file(content)
        result = import_fieldnote_file(path, mode="import_all")
        self.assertEqual(result.imported, 1)
        self.assertEqual(result.skipped, 0)
        placeholder = Geocache.objects.filter(gc_code="GC99999").first()
        self.assertIsNotNone(placeholder)
        self.assertTrue(placeholder.is_placeholder)

    def test_oc_cache_matched_and_note_created(self):
        _oc("OCABC1")
        path = _fieldnote_file('OCABC1,2024-01-15T10:30Z,Found it,"OC find"\n')
        result = import_fieldnote_file(path)
        self.assertEqual(result.imported, 1)
        note = Note.objects.filter(geocache__oc_code="OCABC1").first()
        self.assertIsNotNone(note)
        self.assertEqual(note.log_type, "Found it")

    def test_multiple_entries_same_cache_different_datetime(self):
        _gc("GC12345")
        content = (
            'GC12345,2024-01-15T10:30Z,Found it,"First find"\n'
            'GC12345,2024-02-20T14:00Z,Write note,"Second note"\n'
        )
        path = _fieldnote_file(content)
        result = import_fieldnote_file(path)
        self.assertEqual(result.imported, 2)
        self.assertEqual(Note.objects.filter(geocache__gc_code="GC12345").count(), 2)

    def test_unreadable_file_returns_error(self):
        result = import_fieldnote_file("/nonexistent/path/fieldnotes.txt")
        self.assertEqual(result.imported, 0)
        self.assertTrue(len(result.errors) > 0)
