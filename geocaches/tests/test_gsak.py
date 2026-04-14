"""
Tests for geocaches.importers.gsak — GSAK importer adapter functions.

Verifies that _save_cache, _save_alc_stage_format_a, and _save_alc_format_b
correctly delegate to services.save_geocache() while preserving their
existing return signatures and GSAK-specific behaviour.
"""

import sqlite3
import tempfile
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

from django.test import TestCase

from geocaches.importers.gsak import (
    ImportStats,
    _build_log_dicts,
    _save_alc_format_b,
    _save_alc_stage_format_a,
    _save_cache,
    import_gsak_db,
    split_gsak_note,
)
from geocaches.models import (
    Adventure,
    Attribute,
    CacheSize,
    CacheStatus,
    CacheType,
    CorrectedCoordinates,
    Geocache,
    Image,
    Log,
    Note,
    NoteType,
    Tag,
    Waypoint,
)


# ---------------------------------------------------------------------------
# Test data helpers
# ---------------------------------------------------------------------------

def _make_row(**overrides):
    """Return a dict mimicking a sqlite3.Row for a standard GSAK cache."""
    defaults = {
        "Code": "GC12345",
        "Name": "Test Cache",
        "PlacedBy": "TestOwner",
        "OwnerName": "TestOwner",
        "OwnerId": "12345",
        "CacheType": "T",
        "Container": "Small",
        "Archived": 0,
        "TempDisabled": 0,
        "Latitude": 48.5,
        "Longitude": 9.1,
        "Difficulty": 2.0,
        "Terrain": 1.5,
        "PlacedDate": "2020-01-01",
        "LastFoundDate": "2023-06-15",
        "Country": "Germany",
        "State": "BW",
        "County": "",
        "Elevation": 450.0,
        "Found": 0,
        "FoundByMeDate": "",
        "FoundCount": 42,
        "FTF": 0,
        "DNF": 0,
        "DNFDate": "",
        "MacroFlag": 0,
        "UserFlag": 0,
        "Watch": 0,
        "GcNote": "",
        "UserSort": 0,
        "Color": "",
        "Lock": 0,
        "IsPremium": 0,
        "HasTravelBug": 0,
        "FavPoints": 10,
        "NumberOfLogs": 5,
        "HasCorrected": 0,
        "Guid": "",
    }
    defaults.update(overrides)
    return defaults


def _make_alc_row(**overrides):
    """Return a dict mimicking a sqlite3.Row for an ALC cache."""
    defaults = _make_row(
        CacheType="Q",
        Container="Not chosen",
        Difficulty=0,
        Terrain=0,
        Elevation=0,
    )
    defaults.update(overrides)
    return defaults


def _empty_memos():
    return {}


def _make_memos(code, long_desc="", short_desc="", hints="", user_note=""):
    return {
        code: {
            "LongDescription": long_desc,
            "ShortDescription": short_desc,
            "Hints": hints,
            "UserNote": user_note,
        }
    }


# ---------------------------------------------------------------------------
# _build_log_dicts
# ---------------------------------------------------------------------------

class TestBuildLogDicts(TestCase):
    def test_converts_gsak_log_rows(self):
        logs_by_code = {
            "GC12345": [
                {"lLogId": 100, "lType": "Found it", "lBy": "Alice", "lDate": "2023-01-15"},
                {"lLogId": 101, "lType": "Write note", "lBy": "Bob", "lDate": "2023-02-01"},
            ]
        }
        log_texts = {
            ("GC12345", 100): "Great cache!",
            ("GC12345", 101): "Needs maintenance",
        }
        result = _build_log_dicts("GC12345", logs_by_code, log_texts)
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["source_id"], "100")
        self.assertEqual(result[0]["user_name"], "Alice")
        self.assertEqual(result[0]["text"], "Great cache!")

    def test_skips_logs_with_no_date(self):
        logs_by_code = {"GC12345": [{"lLogId": 1, "lType": "Found it", "lBy": "X", "lDate": ""}]}
        result = _build_log_dicts("GC12345", logs_by_code, {})
        self.assertEqual(len(result), 0)

    def test_empty_code(self):
        result = _build_log_dicts("GCNONE", {}, {})
        self.assertEqual(result, [])


# ---------------------------------------------------------------------------
# _save_cache — standard cache adapter
# ---------------------------------------------------------------------------

class TestSaveCacheAdapter(TestCase):
    def test_creates_new_cache(self):
        row = _make_row()
        result = _save_cache(
            row, "GC12345", "GC12345", None, 48.5, 9.1, datetime.now(timezone.utc), [],
            _empty_memos(), {}, {}, {}, {}, {}, {},
        )
        self.assertEqual(result, "created")
        self.assertEqual(Geocache.objects.count(), 1)
        gc = Geocache.objects.get(gc_code="GC12345")
        self.assertEqual(gc.name, "Test Cache")
        self.assertEqual(gc.cache_type, "Traditional")
        self.assertAlmostEqual(gc.latitude, 48.5)

    def test_updates_existing_cache(self):
        row = _make_row()
        now = datetime.now(timezone.utc)
        _save_cache(row, "GC12345", "GC12345", None, 48.5, 9.1, now, [],
                     _empty_memos(), {}, {}, {}, {}, {}, {})
        row2 = _make_row(Name="Updated Name")
        result = _save_cache(row2, "GC12345", "GC12345", None, 48.5, 9.1, now, [],
                             _empty_memos(), {}, {}, {}, {}, {}, {})
        self.assertEqual(result, "updated")
        self.assertEqual(Geocache.objects.get(gc_code="GC12345").name, "Updated Name")

    def test_locked_cache_skipped(self):
        Geocache.objects.create(
            gc_code="GC12345", name="Locked", cache_type=CacheType.TRADITIONAL,
            latitude=48.5, longitude=9.1, import_locked=True,
        )
        row = _make_row(Name="Should Not Update")
        result = _save_cache(
            row, "GC12345", "GC12345", None, 48.5, 9.1, datetime.now(timezone.utc), [],
            _empty_memos(), {}, {}, {}, {}, {}, {},
        )
        self.assertEqual(result, "locked")
        self.assertEqual(Geocache.objects.get(gc_code="GC12345").name, "Locked")

    def test_found_promotion(self):
        row = _make_row(Found=1, FoundByMeDate="2023-05-20")
        _save_cache(row, "GC12345", "GC12345", None, 48.5, 9.1, datetime.now(timezone.utc), [],
                     _empty_memos(), {}, {}, {}, {}, {}, {})
        gc = Geocache.objects.get(gc_code="GC12345")
        self.assertTrue(gc.found)
        self.assertEqual(gc.found_date, date(2023, 5, 20))

    def test_found_never_demoted(self):
        Geocache.objects.create(
            gc_code="GC12345", name="Found", cache_type=CacheType.TRADITIONAL,
            latitude=48.5, longitude=9.1, found=True, found_date=date(2023, 1, 1),
        )
        row = _make_row(Found=0)
        _save_cache(row, "GC12345", "GC12345", None, 48.5, 9.1, datetime.now(timezone.utc), [],
                     _empty_memos(), {}, {}, {}, {}, {}, {})
        gc = Geocache.objects.get(gc_code="GC12345")
        self.assertTrue(gc.found)
        self.assertEqual(gc.found_date, date(2023, 1, 1))

    def test_tags_applied(self):
        tag = Tag.objects.create(name="MyDB")
        row = _make_row()
        _save_cache(row, "GC12345", "GC12345", None, 48.5, 9.1, datetime.now(timezone.utc), [tag],
                     _empty_memos(), {}, {}, {}, {}, {}, {})
        self.assertIn(tag, Geocache.objects.get(gc_code="GC12345").tags.all())

    def test_attributes_created(self):
        row = _make_row()
        attrs_by_code = {"GC12345": [{"aId": 1, "aInc": 1}, {"aId": 2, "aInc": 0}]}
        _save_cache(row, "GC12345", "GC12345", None, 48.5, 9.1, datetime.now(timezone.utc), [],
                     _empty_memos(), {}, {}, {}, {}, attrs_by_code, {})
        gc = Geocache.objects.get(gc_code="GC12345")
        self.assertEqual(gc.attributes.count(), 2)

    def test_logs_created_and_deduped(self):
        row = _make_row()
        now = datetime.now(timezone.utc)
        logs_by_code = {
            "GC12345": [
                {"lLogId": 100, "lType": "Found it", "lBy": "Alice", "lDate": "2023-01-15"},
            ]
        }
        log_texts = {("GC12345", 100): "TFTC"}
        _save_cache(row, "GC12345", "GC12345", None, 48.5, 9.1, now, [],
                     _empty_memos(), log_texts, logs_by_code, {}, {}, {}, {})
        self.assertEqual(Log.objects.count(), 1)
        # Re-import — should not duplicate
        _save_cache(row, "GC12345", "GC12345", None, 48.5, 9.1, now, [],
                     _empty_memos(), log_texts, logs_by_code, {}, {}, {}, {})
        self.assertEqual(Log.objects.count(), 1)

    def test_waypoints_created(self):
        row = _make_row()
        waypoints_by_code = {
            "GC12345": [
                {"cCode": "P112345", "cPrefix": "P1", "cName": "Parking",
                 "cType": "Parking Area", "cLat": 48.51, "cLon": 9.11, "cByuser": 0},
            ]
        }
        _save_cache(row, "GC12345", "GC12345", None, 48.5, 9.1, datetime.now(timezone.utc), [],
                     _empty_memos(), {}, {}, waypoints_by_code, {}, {}, {})
        self.assertEqual(Waypoint.objects.count(), 1)
        self.assertEqual(Waypoint.objects.first().name, "Parking")

    def test_corrected_coordinates(self):
        row = _make_row(HasCorrected=1)
        corrected = {"GC12345": {"kAfterLat": 48.6, "kAfterLon": 9.2, "kBeforeLat": 48.5, "kBeforeLon": 9.1}}
        _save_cache(row, "GC12345", "GC12345", None, 48.5, 9.1, datetime.now(timezone.utc), [],
                     _empty_memos(), {}, {}, {}, corrected, {}, {})
        cc = CorrectedCoordinates.objects.get(geocache__gc_code="GC12345")
        self.assertAlmostEqual(cc.latitude, 48.6)
        self.assertAlmostEqual(cc.longitude, 9.2)

    def test_corrected_coords_skipped_when_no_flag(self):
        row = _make_row(HasCorrected=0)
        corrected = {"GC12345": {"kAfterLat": 48.6, "kAfterLon": 9.2, "kBeforeLat": 48.5, "kBeforeLon": 9.1}}
        _save_cache(row, "GC12345", "GC12345", None, 48.5, 9.1, datetime.now(timezone.utc), [],
                     _empty_memos(), {}, {}, {}, corrected, {}, {})
        self.assertEqual(CorrectedCoordinates.objects.count(), 0)

    def test_images_created_and_deduped(self):
        row = _make_row()
        now = datetime.now(timezone.utc)
        images_by_code = {
            "GC12345": [
                {"iImage": "https://example.com/1.jpg", "iName": "Photo", "iDescription": "desc"},
            ]
        }
        _save_cache(row, "GC12345", "GC12345", None, 48.5, 9.1, now, [],
                     _empty_memos(), {}, {}, {}, {}, {}, images_by_code)
        self.assertEqual(Image.objects.count(), 1)
        # Re-import — should not duplicate
        _save_cache(row, "GC12345", "GC12345", None, 48.5, 9.1, now, [],
                     _empty_memos(), {}, {}, {}, {}, {}, images_by_code)
        self.assertEqual(Image.objects.count(), 1)

    def test_notes_created_skip_if_exist(self):
        row = _make_row()
        now = datetime.now(timezone.utc)
        memos = _make_memos("GC12345", user_note="My user note")
        _save_cache(row, "GC12345", "GC12345", None, 48.5, 9.1, now, [],
                     memos, {}, {}, {}, {}, {}, {})
        self.assertEqual(Note.objects.count(), 1)
        self.assertEqual(Note.objects.first().body, "My user note")
        # Re-import — notes should be skipped (skip_notes_if_exist=True)
        _save_cache(row, "GC12345", "GC12345", None, 48.5, 9.1, now, [],
                     memos, {}, {}, {}, {}, {}, {})
        self.assertEqual(Note.objects.count(), 1)

    def test_notes_with_field_note_split(self):
        row = _make_row()
        memos = _make_memos(
            "GC12345",
            user_note="My note$~--Field Note Start from 2023-05-20 12:00:00--\nFound it!\n--Field Note End--",
        )
        _save_cache(row, "GC12345", "GC12345", None, 48.5, 9.1, datetime.now(timezone.utc), [],
                     memos, {}, {}, {}, {}, {}, {})
        self.assertEqual(Note.objects.count(), 2)
        types = set(Note.objects.values_list("note_type", flat=True))
        self.assertEqual(types, {"note", "field_note"})


# ---------------------------------------------------------------------------
# _save_alc_stage_format_a — ALC Format A adapter
# ---------------------------------------------------------------------------

class TestSaveAlcStageFormatA(TestCase):
    def test_creates_stage_and_adventure(self):
        row = _make_alc_row(Code="LC28NG-1", Name="My Adventure : Stage 1")
        result = _save_alc_stage_format_a(
            row, "LC28NG-1", "28NG", 1, 48.5, 9.1,
            datetime.now(timezone.utc), [], "",
            _empty_memos(), {}, {},
            adv_stage_count=3,
        )
        self.assertEqual(result, "created")
        self.assertTrue(Adventure.objects.filter(code="LC28NG").exists())
        # Parent geocache + stage geocache
        self.assertTrue(Geocache.objects.filter(al_code="LC28NG").exists())
        stage = Geocache.objects.get(al_code="LC28NG-1")
        self.assertEqual(stage.name, "Stage 1")
        self.assertEqual(stage.cache_type, CacheType.LAB)
        self.assertEqual(stage.stage_number, 1)

    def test_updates_existing_stage(self):
        row = _make_alc_row(Code="LC28NG-1", Name="My Adventure : Stage 1")
        now = datetime.now(timezone.utc)
        _save_alc_stage_format_a(
            row, "LC28NG-1", "28NG", 1, 48.5, 9.1, now, [], "",
            _empty_memos(), {}, {},
        )
        row2 = _make_alc_row(Code="LC28NG-1", Name="My Adventure : Updated Stage")
        result = _save_alc_stage_format_a(
            row2, "LC28NG-1", "28NG", 1, 48.5, 9.1, now, [], "",
            _empty_memos(), {}, {},
        )
        self.assertEqual(result, "updated")
        self.assertEqual(Geocache.objects.get(al_code="LC28NG-1").name, "Updated Stage")

    def test_locked_stage_skipped(self):
        adv = Adventure.objects.create(code="LC28NG", title="Adv", latitude=48.5, longitude=9.1)
        Geocache.objects.create(
            al_code="LC28NG-1", name="Locked Stage", cache_type=CacheType.LAB,
            latitude=48.5, longitude=9.1, import_locked=True, adventure=adv,
        )
        row = _make_alc_row(Code="LC28NG-1", Name="My Adventure : Should Not Update")
        result = _save_alc_stage_format_a(
            row, "LC28NG-1", "28NG", 1, 48.5, 9.1,
            datetime.now(timezone.utc), [], "",
            _empty_memos(), {}, {},
        )
        self.assertEqual(result, "locked")
        self.assertEqual(Geocache.objects.get(al_code="LC28NG-1").name, "Locked Stage")

    def test_found_promotion_from_gsak(self):
        row = _make_alc_row(
            Code="LC28NG-1", Name="My Adventure : Stage 1",
            Found=1, FoundByMeDate="2023-08-15",
        )
        _save_alc_stage_format_a(
            row, "LC28NG-1", "28NG", 1, 48.5, 9.1,
            datetime.now(timezone.utc), [], "",
            _empty_memos(), {}, {},
        )
        stage = Geocache.objects.get(al_code="LC28NG-1")
        self.assertTrue(stage.found)
        self.assertEqual(stage.found_date, date(2023, 8, 15))

    def test_uuid_lookup(self):
        adv = Adventure.objects.create(code="LC28NG", title="Adv", latitude=48.5, longitude=9.1)
        Geocache.objects.create(
            al_code="LC28NG", name="Parent", cache_type=CacheType.LAB,
            latitude=48.5, longitude=9.1, adventure=adv,
        )
        Geocache.objects.create(
            al_code="LC28NG-1", name="Original", cache_type=CacheType.LAB,
            latitude=48.5, longitude=9.1, adventure=adv,
            al_stage_uuid="aaaa-bbbb",
        )
        row = _make_alc_row(
            Code="LC28NG-1", Name="My Adventure : Updated Via UUID",
            Guid="aaaa-bbbb",
        )
        result = _save_alc_stage_format_a(
            row, "LC28NG-1", "28NG", 1, 48.5, 9.1,
            datetime.now(timezone.utc), [], "aaaa-bbbb",
            _empty_memos(), {}, {},
        )
        self.assertEqual(result, "updated")
        stage = Geocache.objects.get(al_stage_uuid="aaaa-bbbb")
        self.assertEqual(stage.name, "Updated Via UUID")

    def test_tags_applied_to_stage(self):
        tag = Tag.objects.create(name="ALC-Tag")
        row = _make_alc_row(Code="LC28NG-1", Name="My Adventure : Stage 1")
        _save_alc_stage_format_a(
            row, "LC28NG-1", "28NG", 1, 48.5, 9.1,
            datetime.now(timezone.utc), [tag], "",
            _empty_memos(), {}, {},
        )
        stage = Geocache.objects.get(al_code="LC28NG-1")
        self.assertIn(tag, stage.tags.all())

    def test_logs_created(self):
        row = _make_alc_row(Code="LC28NG-1", Name="My Adventure : Stage 1")
        logs_by_code = {
            "LC28NG-1": [
                {"lLogId": 200, "lType": "Found it", "lBy": "Finder", "lDate": "2023-09-01"},
            ]
        }
        log_texts = {("LC28NG-1", 200): "Found the lab!"}
        _save_alc_stage_format_a(
            row, "LC28NG-1", "28NG", 1, 48.5, 9.1,
            datetime.now(timezone.utc), [], "",
            _empty_memos(), log_texts, logs_by_code,
        )
        self.assertEqual(Log.objects.filter(geocache__al_code="LC28NG-1").count(), 1)


# ---------------------------------------------------------------------------
# _save_alc_format_b — ALC Format B adapter
# ---------------------------------------------------------------------------

class TestSaveAlcFormatB(TestCase):
    def test_creates_stages_from_waypoints(self):
        row = _make_alc_row(Code="LC28NG", Name="My Adventure")
        waypoints_by_code = {
            "LC28NG": [
                {"cCode": "S128NG", "cPrefix": "S1", "cName": "Stage One",
                 "cType": "Waypoint", "cLat": 48.51, "cLon": 9.11, "cByuser": 0},
                {"cCode": "S228NG", "cPrefix": "S2", "cName": "Stage Two",
                 "cType": "Waypoint", "cLat": 48.52, "cLon": 9.12, "cByuser": 0},
            ]
        }
        stats = ImportStats()
        _save_alc_format_b(
            row, "LC28NG", "28NG", 48.5, 9.1,
            datetime.now(timezone.utc), [], _empty_memos(), {}, {}, waypoints_by_code, stats,
        )
        self.assertEqual(stats.created, 2)
        self.assertTrue(Geocache.objects.filter(al_code="LC28NG-1").exists())
        self.assertTrue(Geocache.objects.filter(al_code="LC28NG-2").exists())
        # Parent geocache also created
        self.assertTrue(Geocache.objects.filter(al_code="LC28NG").exists())

    def test_updates_existing_stages(self):
        row = _make_alc_row(Code="LC28NG", Name="My Adventure")
        now = datetime.now(timezone.utc)
        waypoints_by_code = {
            "LC28NG": [
                {"cCode": "S128NG", "cPrefix": "S1", "cName": "Stage One",
                 "cType": "Waypoint", "cLat": 48.51, "cLon": 9.11, "cByuser": 0},
            ]
        }
        stats1 = ImportStats()
        _save_alc_format_b(
            row, "LC28NG", "28NG", 48.5, 9.1, now, [],
            _empty_memos(), {}, {}, waypoints_by_code, stats1,
        )
        self.assertEqual(stats1.created, 1)

        waypoints_by_code["LC28NG"][0]["cName"] = "Updated Stage"
        stats2 = ImportStats()
        _save_alc_format_b(
            row, "LC28NG", "28NG", 48.5, 9.1, now, [],
            _empty_memos(), {}, {}, waypoints_by_code, stats2,
        )
        self.assertEqual(stats2.updated, 1)
        self.assertEqual(Geocache.objects.get(al_code="LC28NG-1").name, "Updated Stage")

    def test_locked_stage_in_format_b(self):
        adv = Adventure.objects.create(code="LC28NG", title="Adv", latitude=48.5, longitude=9.1)
        Geocache.objects.create(
            al_code="LC28NG", name="Parent", cache_type=CacheType.LAB,
            latitude=48.5, longitude=9.1, adventure=adv,
        )
        Geocache.objects.create(
            al_code="LC28NG-1", name="Locked", cache_type=CacheType.LAB,
            latitude=48.5, longitude=9.1, import_locked=True, adventure=adv,
        )
        row = _make_alc_row(Code="LC28NG", Name="My Adventure")
        waypoints_by_code = {
            "LC28NG": [
                {"cCode": "S128NG", "cPrefix": "S1", "cName": "Should Not Update",
                 "cType": "Waypoint", "cLat": 48.51, "cLon": 9.11, "cByuser": 0},
            ]
        }
        stats = ImportStats()
        _save_alc_format_b(
            row, "LC28NG", "28NG", 48.5, 9.1,
            datetime.now(timezone.utc), [], _empty_memos(), {}, {}, waypoints_by_code, stats,
        )
        self.assertEqual(stats.locked, 1)
        self.assertEqual(Geocache.objects.get(al_code="LC28NG-1").name, "Locked")

    def test_found_promotion_format_b(self):
        row = _make_alc_row(Code="LC28NG", Name="My Adventure", Found=1, FoundByMeDate="2023-07-01")
        waypoints_by_code = {
            "LC28NG": [
                {"cCode": "S128NG", "cPrefix": "S1", "cName": "Stage One",
                 "cType": "Waypoint", "cLat": 48.51, "cLon": 9.11, "cByuser": 0},
            ]
        }
        stats = ImportStats()
        _save_alc_format_b(
            row, "LC28NG", "28NG", 48.5, 9.1,
            datetime.now(timezone.utc), [], _empty_memos(), {}, {}, waypoints_by_code, stats,
        )
        stage = Geocache.objects.get(al_code="LC28NG-1")
        self.assertTrue(stage.found)
        self.assertEqual(stage.found_date, date(2023, 7, 1))

    def test_tags_applied_to_stages(self):
        tag = Tag.objects.create(name="LabTag")
        row = _make_alc_row(Code="LC28NG", Name="My Adventure")
        waypoints_by_code = {
            "LC28NG": [
                {"cCode": "S128NG", "cPrefix": "S1", "cName": "Stage One",
                 "cType": "Waypoint", "cLat": 48.51, "cLon": 9.11, "cByuser": 0},
            ]
        }
        stats = ImportStats()
        _save_alc_format_b(
            row, "LC28NG", "28NG", 48.5, 9.1,
            datetime.now(timezone.utc), [tag], _empty_memos(), {}, {}, waypoints_by_code, stats,
        )
        stage = Geocache.objects.get(al_code="LC28NG-1")
        self.assertIn(tag, stage.tags.all())

    def test_non_stage_waypoints_ignored(self):
        row = _make_alc_row(Code="LC28NG", Name="My Adventure")
        waypoints_by_code = {
            "LC28NG": [
                {"cCode": "S128NG", "cPrefix": "S1", "cName": "Stage One",
                 "cType": "Waypoint", "cLat": 48.51, "cLon": 9.11, "cByuser": 0},
                {"cCode": "XXXXXX", "cPrefix": "XX", "cName": "Not a stage",
                 "cType": "Waypoint", "cLat": 48.52, "cLon": 9.12, "cByuser": 0},
            ]
        }
        stats = ImportStats()
        _save_alc_format_b(
            row, "LC28NG", "28NG", 48.5, 9.1,
            datetime.now(timezone.utc), [], _empty_memos(), {}, {}, waypoints_by_code, stats,
        )
        self.assertEqual(stats.created, 1)  # Only the valid stage


# ---------------------------------------------------------------------------
# Integration test — import_gsak_db with a real SQLite file
# ---------------------------------------------------------------------------

def _create_gsak_db(path, caches, memos=None, logs=None, log_memos=None,
                    waypoints=None, corrected=None, attributes=None, images=None):
    """Create a minimal GSAK-style sqlite.db3 for testing."""
    conn = sqlite3.connect(str(path))
    conn.execute("""
        CREATE TABLE Caches (
            Code TEXT PRIMARY KEY,
            Name TEXT, PlacedBy TEXT, OwnerName TEXT, OwnerId TEXT,
            CacheType TEXT, Container TEXT,
            Archived INTEGER DEFAULT 0, TempDisabled INTEGER DEFAULT 0,
            Latitude REAL, Longitude REAL,
            Difficulty REAL, Terrain REAL,
            PlacedDate TEXT, LastFoundDate TEXT,
            Country TEXT, State TEXT, County TEXT, Elevation REAL,
            Found INTEGER DEFAULT 0, FoundByMeDate TEXT,
            FoundCount INTEGER DEFAULT 0, FTF INTEGER DEFAULT 0,
            DNF INTEGER DEFAULT 0, DNFDate TEXT,
            MacroFlag INTEGER DEFAULT 0, UserFlag INTEGER DEFAULT 0,
            Watch INTEGER DEFAULT 0, GcNote TEXT DEFAULT '',
            UserSort INTEGER DEFAULT 0, Color TEXT DEFAULT '',
            Lock INTEGER DEFAULT 0, IsPremium INTEGER DEFAULT 0,
            HasTravelBug INTEGER DEFAULT 0, FavPoints INTEGER DEFAULT 0,
            NumberOfLogs INTEGER DEFAULT 0, HasCorrected INTEGER DEFAULT 0,
            Guid TEXT DEFAULT ''
        )
    """)
    conn.execute("""
        CREATE TABLE CacheMemo (
            Code TEXT PRIMARY KEY,
            LongDescription TEXT DEFAULT '',
            ShortDescription TEXT DEFAULT '',
            Hints TEXT DEFAULT '',
            UserNote TEXT DEFAULT ''
        )
    """)
    conn.execute("CREATE TABLE Logs (lParent TEXT, lLogId INTEGER, lType TEXT, lBy TEXT, lDate TEXT)")
    conn.execute("CREATE TABLE LogMemo (lParent TEXT, lLogId INTEGER, lText TEXT)")
    conn.execute("""
        CREATE TABLE Waypoints (
            cParent TEXT, cCode TEXT, cPrefix TEXT, cName TEXT,
            cType TEXT, cLat REAL, cLon REAL, cByuser INTEGER DEFAULT 0
        )
    """)
    conn.execute("CREATE TABLE Corrected (kCode TEXT, kBeforeLat REAL, kBeforeLon REAL, kAfterLat REAL, kAfterLon REAL)")
    conn.execute("CREATE TABLE Attributes (aCode TEXT, aId INTEGER, aInc INTEGER)")
    conn.execute("CREATE TABLE CacheImages (iCode TEXT, iName TEXT, iDescription TEXT, iImage TEXT)")

    for c in caches:
        cols = ", ".join(c.keys())
        placeholders = ", ".join("?" for _ in c)
        conn.execute(f"INSERT INTO Caches ({cols}) VALUES ({placeholders})", list(c.values()))

    for m in (memos or []):
        conn.execute(
            "INSERT INTO CacheMemo (Code, LongDescription, ShortDescription, Hints, UserNote) VALUES (?,?,?,?,?)",
            (m["Code"], m.get("LongDescription", ""), m.get("ShortDescription", ""),
             m.get("Hints", ""), m.get("UserNote", "")),
        )

    for log in (logs or []):
        conn.execute("INSERT INTO Logs VALUES (?,?,?,?,?)",
                     (log["lParent"], log["lLogId"], log["lType"], log["lBy"], log["lDate"]))

    for lm in (log_memos or []):
        conn.execute("INSERT INTO LogMemo VALUES (?,?,?)",
                     (lm["lParent"], lm["lLogId"], lm["lText"]))

    for wp in (waypoints or []):
        conn.execute("INSERT INTO Waypoints VALUES (?,?,?,?,?,?,?,?)",
                     (wp["cParent"], wp["cCode"], wp["cPrefix"], wp["cName"],
                      wp["cType"], wp["cLat"], wp["cLon"], wp.get("cByuser", 0)))

    for cr in (corrected or []):
        conn.execute("INSERT INTO Corrected VALUES (?,?,?,?,?)",
                     (cr["kCode"], cr.get("kBeforeLat"), cr.get("kBeforeLon"),
                      cr["kAfterLat"], cr["kAfterLon"]))

    for a in (attributes or []):
        conn.execute("INSERT INTO Attributes VALUES (?,?,?)", (a["aCode"], a["aId"], a["aInc"]))

    for img in (images or []):
        conn.execute("INSERT INTO CacheImages VALUES (?,?,?,?)",
                     (img["iCode"], img["iName"], img["iDescription"], img["iImage"]))

    conn.commit()
    conn.close()


class TestImportGsakDb(TestCase):
    def test_imports_standard_cache(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "MyDB" / "sqlite.db3"
            db_path.parent.mkdir()
            _create_gsak_db(db_path, caches=[{
                "Code": "GC12345", "Name": "Test Cache", "PlacedBy": "Owner",
                "OwnerName": "Owner", "OwnerId": "1", "CacheType": "T",
                "Container": "Small", "Latitude": 48.5, "Longitude": 9.1,
                "Difficulty": 2.0, "Terrain": 1.5, "PlacedDate": "2020-01-01",
                "Country": "Germany", "State": "BW",
            }])
            result = import_gsak_db(str(db_path), tag_names=["TestTag"])
            self.assertEqual(result.created, 1)
            self.assertEqual(result.updated, 0)
            gc = Geocache.objects.get(gc_code="GC12345")
            self.assertEqual(gc.name, "Test Cache")
            self.assertIn("TestTag", [t.name for t in gc.tags.all()])

    def test_imports_with_logs_and_attributes(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "MyDB" / "sqlite.db3"
            db_path.parent.mkdir()
            _create_gsak_db(
                db_path,
                caches=[{
                    "Code": "GC12345", "Name": "Test", "PlacedBy": "O",
                    "OwnerName": "O", "OwnerId": "1", "CacheType": "T",
                    "Container": "Small", "Latitude": 48.5, "Longitude": 9.1,
                    "Difficulty": 2.0, "Terrain": 1.5,
                    "Country": "Germany", "State": "BW",
                }],
                logs=[{"lParent": "GC12345", "lLogId": 100, "lType": "Found it",
                       "lBy": "Alice", "lDate": "2023-01-15"}],
                log_memos=[{"lParent": "GC12345", "lLogId": 100, "lText": "TFTC"}],
                attributes=[{"aCode": "GC12345", "aId": 1, "aInc": 1}],
            )
            result = import_gsak_db(str(db_path))
            self.assertEqual(result.created, 1)
            gc = Geocache.objects.get(gc_code="GC12345")
            self.assertEqual(gc.logs.count(), 1)
            self.assertEqual(gc.attributes.count(), 1)

    def test_imports_with_corrected_coords_and_images(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "MyDB" / "sqlite.db3"
            db_path.parent.mkdir()
            _create_gsak_db(
                db_path,
                caches=[{
                    "Code": "GC12345", "Name": "Mystery", "PlacedBy": "O",
                    "OwnerName": "O", "OwnerId": "1", "CacheType": "U",
                    "Container": "Small", "Latitude": 48.5, "Longitude": 9.1,
                    "Difficulty": 3.0, "Terrain": 2.0, "HasCorrected": 1,
                    "Country": "Germany", "State": "BW",
                }],
                corrected=[{"kCode": "GC12345", "kAfterLat": 48.6, "kAfterLon": 9.2}],
                images=[{"iCode": "GC12345", "iName": "Hint Photo",
                         "iDescription": "Spoiler", "iImage": "https://example.com/1.jpg"}],
            )
            result = import_gsak_db(str(db_path))
            self.assertEqual(result.created, 1)
            cc = CorrectedCoordinates.objects.get(geocache__gc_code="GC12345")
            self.assertAlmostEqual(cc.latitude, 48.6)
            self.assertEqual(Image.objects.count(), 1)

    def test_imports_alc_format_a(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "MyDB" / "sqlite.db3"
            db_path.parent.mkdir()
            _create_gsak_db(
                db_path,
                caches=[
                    {
                        "Code": "LC28NG-1", "Name": "Adventure : Stage 1",
                        "PlacedBy": "Owner", "OwnerName": "Owner", "OwnerId": "1",
                        "CacheType": "Q", "Container": "Not chosen",
                        "Latitude": 48.5, "Longitude": 9.1,
                        "Country": "Germany", "State": "BW",
                    },
                    {
                        "Code": "LC28NG-2", "Name": "Adventure : Stage 2",
                        "PlacedBy": "Owner", "OwnerName": "Owner", "OwnerId": "1",
                        "CacheType": "Q", "Container": "Not chosen",
                        "Latitude": 48.51, "Longitude": 9.11,
                        "Country": "Germany", "State": "BW",
                    },
                ],
            )
            result = import_gsak_db(str(db_path))
            self.assertEqual(result.created, 2)
            self.assertTrue(Adventure.objects.filter(code="LC28NG").exists())
            self.assertTrue(Geocache.objects.filter(al_code="LC28NG-1").exists())
            self.assertTrue(Geocache.objects.filter(al_code="LC28NG-2").exists())

    def test_reimport_updates(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "MyDB" / "sqlite.db3"
            db_path.parent.mkdir()
            _create_gsak_db(db_path, caches=[{
                "Code": "GC12345", "Name": "Original", "PlacedBy": "O",
                "OwnerName": "O", "OwnerId": "1", "CacheType": "T",
                "Container": "Small", "Latitude": 48.5, "Longitude": 9.1,
                "Country": "Germany", "State": "BW",
            }])
            import_gsak_db(str(db_path))
            # Reimport
            result = import_gsak_db(str(db_path))
            self.assertEqual(result.updated, 1)
            self.assertEqual(result.created, 0)
