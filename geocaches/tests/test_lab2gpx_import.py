"""
Tests for lab2gpx GPX import — Format A (individual stages) and Format B (parent + stage wpts).
"""

from datetime import datetime, timezone
from pathlib import Path

from django.test import TestCase

from geocaches.importers.gpx_gc import ImportStats
from geocaches.importers.lab2gpx import _save_alc_stage, import_lab2gpx
from geocaches.lc_code import uuid_to_lc_code
from geocaches.models import Adventure, CacheType, Geocache, Tag

FIXTURES = Path(__file__).resolve().parent / "fixtures"

# The adventure UUID used in both fixtures; canonical LC code is derived from it.
_ADV_UUID = "4cef8a3f-ec20-472a-8de2-9b4859bc6526"
_PARENT_CODE = uuid_to_lc_code(_ADV_UUID)


class FormatAImportTest(TestCase):
    """Import a Format A lab2gpx GPX (one wpt per stage)."""

    @classmethod
    def setUpTestData(cls):
        cls.stats = import_lab2gpx(str(FIXTURES / "lab2gpx_format_a.gpx"))

    def test_one_adventure_created(self):
        self.assertEqual(Adventure.objects.count(), 1)

    def test_adventure_description_contains_expected_text(self):
        desc = Adventure.objects.first().description
        self.assertIn("Die Spree entspringt", desc)

    def test_adventure_description_excludes_metadata(self):
        desc = Adventure.objects.first().description
        self.assertNotIn("labs.geocaching.com", desc)
        self.assertNotIn("Radius:", desc)
        self.assertNotIn("Stages:", desc)
        self.assertNotIn("Kind of question:", desc)
        self.assertNotIn("Question:", desc)

    def test_two_stages_created(self):
        stage_codes = set(
            Geocache.objects.filter(stage_number__isnull=False)
            .values_list("al_code", flat=True)
        )
        self.assertEqual(stage_codes, {f"{_PARENT_CODE}-1", f"{_PARENT_CODE}-2"})

    def test_parent_geocache_exists(self):
        parent = Geocache.objects.get(al_code=_PARENT_CODE)
        self.assertIsNotNone(parent)

    def test_parent_long_description_contains_adventure_text(self):
        parent = Geocache.objects.get(al_code=_PARENT_CODE)
        self.assertIn("Die Spree entspringt", parent.long_description)

    def test_parent_long_description_excludes_metadata(self):
        parent = Geocache.objects.get(al_code=_PARENT_CODE)
        self.assertNotIn("labs.geocaching.com", parent.long_description)

    def test_stage1_question_text(self):
        stage1 = Geocache.objects.get(al_code=f"{_PARENT_CODE}-1")
        self.assertEqual(
            stage1.question_text,
            "Welcher Ort und welche Telefonnummer stehen am Fu\u00df der Laterne?",
        )

    def test_stage2_question_text(self):
        stage2 = Geocache.objects.get(al_code=f"{_PARENT_CODE}-2")
        self.assertEqual(
            stage2.question_text,
            "Welches Wort steht auf dem blauen Schild? Welche Telefonnummer steht auf dem wei\u00dfen Schild?",
        )


class FormatBImportTest(TestCase):
    """Import a Format B lab2gpx GPX (parent wpt + stage wpts)."""

    @classmethod
    def setUpTestData(cls):
        cls.stats = import_lab2gpx(str(FIXTURES / "lab2gpx_format_b.gpx"))

    def test_adventure_description_contains_expected_text(self):
        desc = Adventure.objects.first().description
        self.assertIn("Die Spree entspringt", desc)

    def test_adventure_description_excludes_metadata(self):
        desc = Adventure.objects.first().description
        self.assertNotIn("labs.geocaching.com", desc)
        self.assertNotIn("Radius:", desc)
        self.assertNotIn("Stages:", desc)
        self.assertNotIn("Question:", desc)

    def test_parent_long_description_contains_adventure_text(self):
        parent = Geocache.objects.get(al_code=_PARENT_CODE)
        self.assertIn("Die Spree entspringt", parent.long_description)

    def test_stage1_question_text(self):
        stage1 = Geocache.objects.get(al_code=f"{_PARENT_CODE}-1")
        self.assertEqual(
            stage1.question_text,
            "Welcher Ort und welche Telefonnummer stehen am Fu\u00df der Laterne?",
        )

    def test_stage2_question_text(self):
        stage2 = Geocache.objects.get(al_code=f"{_PARENT_CODE}-2")
        self.assertEqual(
            stage2.question_text,
            "Welches Wort steht auf dem blauen Schild? Welche Telefonnummer steht auf dem wei\u00dfen Schild?",
        )

    def test_stage1_long_description(self):
        stage1 = Geocache.objects.get(al_code=f"{_PARENT_CODE}-1")
        self.assertIn("Die Moabiter Br\u00fccke", stage1.long_description)

    def test_stage2_long_description(self):
        stage2 = Geocache.objects.get(al_code=f"{_PARENT_CODE}-2")
        self.assertIn("Mauersegmenten", stage2.long_description)


# ---------------------------------------------------------------------------
# _save_alc_stage adapter unit tests
# ---------------------------------------------------------------------------

def _stage_fields(**overrides):
    """Minimal model_fields dict for _save_alc_stage()."""
    defaults = {
        "name": "Test Stage",
        "owner": "Tester",
        "placed_by": "Tester",
        "cache_type": "Adventure Lab",
        "size": "Virtual",
        "status": "Active",
        "latitude": 52.5,
        "longitude": 13.4,
        "hidden_date": None,
        "last_gpx_date": datetime(2026, 1, 1, tzinfo=timezone.utc),
        "long_description": "",
        "adventure": None,
        "stage_number": 1,
        "question_text": "",
        "al_stage_uuid": "",
    }
    defaults.update(overrides)
    return defaults


class SaveAlcStageCreateTest(TestCase):
    """_save_alc_stage creates a new geocache and increments stats.created."""

    def test_creates_geocache(self):
        stats = ImportStats()
        _save_alc_stage("LC1234-1", _stage_fields(), [], stats)
        self.assertEqual(Geocache.objects.count(), 1)
        self.assertEqual(Geocache.objects.first().al_code, "LC1234-1")

    def test_stats_created_incremented(self):
        stats = ImportStats()
        _save_alc_stage("LC1234-1", _stage_fields(), [], stats)
        self.assertEqual(stats.created, 1)
        self.assertEqual(stats.updated, 0)
        self.assertEqual(stats.locked, 0)


class SaveAlcStageUpdateTest(TestCase):
    """_save_alc_stage updates an existing geocache and increments stats.updated."""

    def test_updates_existing(self):
        stats = ImportStats()
        _save_alc_stage("LC1234-1", _stage_fields(name="Original"), [], stats)
        _save_alc_stage("LC1234-1", _stage_fields(name="Updated"), [], stats)
        self.assertEqual(Geocache.objects.count(), 1)
        self.assertEqual(Geocache.objects.first().name, "Updated")
        self.assertEqual(stats.updated, 1)


class SaveAlcStageUUIDLookupTest(TestCase):
    """_save_alc_stage finds caches by UUID first, then al_code."""

    def test_uuid_lookup_finds_existing(self):
        Geocache.objects.create(
            al_code="LC1234-1", name="Original", cache_type=CacheType.LAB,
            latitude=52.5, longitude=13.4, al_stage_uuid="uuid-aaa",
        )
        stats = ImportStats()
        _save_alc_stage(
            "LC1234-1",
            _stage_fields(name="Via UUID", al_stage_uuid="uuid-aaa"),
            [], stats,
        )
        self.assertEqual(Geocache.objects.count(), 1)
        self.assertEqual(Geocache.objects.first().name, "Via UUID")
        self.assertEqual(stats.updated, 1)

    def test_uuid_lookup_updates_al_code(self):
        """When found by UUID, al_code is updated if it changed."""
        Geocache.objects.create(
            al_code="LC1234-1", name="Original", cache_type=CacheType.LAB,
            latitude=52.5, longitude=13.4, al_stage_uuid="uuid-bbb",
        )
        stats = ImportStats()
        _save_alc_stage(
            "LC5678-1",
            _stage_fields(al_stage_uuid="uuid-bbb"),
            [], stats,
        )
        cache = Geocache.objects.get(al_stage_uuid="uuid-bbb")
        self.assertEqual(cache.al_code, "LC5678-1")

    def test_uuid_miss_falls_back_to_al_code(self):
        stats = ImportStats()
        _save_alc_stage(
            "LC1234-1",
            _stage_fields(al_stage_uuid="nonexistent-uuid"),
            [], stats,
        )
        self.assertEqual(stats.created, 1)
        self.assertEqual(Geocache.objects.first().al_code, "LC1234-1")


class SaveAlcStageImportLockTest(TestCase):
    """_save_alc_stage skips locked caches."""

    def test_locked_cache_not_updated(self):
        Geocache.objects.create(
            al_code="LC1234-1", name="Locked", cache_type=CacheType.LAB,
            latitude=52.5, longitude=13.4, import_locked=True,
        )
        stats = ImportStats()
        _save_alc_stage("LC1234-1", _stage_fields(name="Should Not Apply"), [], stats)
        self.assertEqual(stats.locked, 1)
        self.assertEqual(stats.updated, 0)
        self.assertEqual(Geocache.objects.first().name, "Locked")

    def test_locked_cache_skips_tags(self):
        Geocache.objects.create(
            al_code="LC1234-1", name="Locked", cache_type=CacheType.LAB,
            latitude=52.5, longitude=13.4, import_locked=True,
        )
        tag = Tag.objects.create(name="MyTag")
        stats = ImportStats()
        _save_alc_stage("LC1234-1", _stage_fields(), [tag], stats)
        self.assertEqual(Geocache.objects.first().tags.count(), 0)


class SaveAlcStageTagsTest(TestCase):
    """_save_alc_stage applies tags to the geocache."""

    def test_tags_applied(self):
        tag = Tag.objects.create(name="Lab Import")
        stats = ImportStats()
        _save_alc_stage("LC1234-1", _stage_fields(), [tag], stats)
        self.assertIn(tag, Geocache.objects.first().tags.all())

    def test_empty_tags_list(self):
        stats = ImportStats()
        _save_alc_stage("LC1234-1", _stage_fields(), [], stats)
        self.assertEqual(Geocache.objects.first().tags.count(), 0)
