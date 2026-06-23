"""Tests for geocaches.services.save_alc — adventure/stage persistence helpers."""

from datetime import datetime, timezone

from django.test import TestCase

from geocaches.importers.gpx_gc import ImportStats
from geocaches.models import (
    ALStageDetail,
    Adventure,
    CacheType,
    Geocache,
    Tag,
)
from geocaches.services.save_alc import (
    merge_duplicate_adventure,
    save_adventure_from_api,
    save_alc_stage,
)


def _now():
    return datetime.now(timezone.utc)


def _minimal_api_data(**overrides):
    defaults = {
        "adventure_guid": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        "title": "Test Adventure",
        "owner": "TestUser",
        "lat": 52.52,
        "lon": 13.405,
        "status": "Active",
        "description": "A test adventure",
        "stage_count": 2,
    }
    defaults.update(overrides)
    return defaults


def _stage_fields(**overrides):
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
        "last_gpx_date": _now(),
        "long_description": "",
        "adventure": None,
        "stage_number": 1,
        "question_text": "What color is the door?",
        "al_stage_uuid": "",
    }
    defaults.update(overrides)
    return defaults


# ---------------------------------------------------------------------------
# save_adventure_from_api
# ---------------------------------------------------------------------------

class SaveAdventureFromApiTest(TestCase):
    def test_creates_adventure_and_parent(self):
        data = _minimal_api_data()
        adv, stats = save_adventure_from_api(data)
        self.assertIsInstance(adv, Adventure)
        self.assertEqual(adv.title, "Test Adventure")
        # Parent geocache created
        self.assertTrue(Geocache.objects.filter(al_code=adv.code).exists())

    def test_adventure_has_correct_fields(self):
        data = _minimal_api_data(stage_count=3)
        adv, _ = save_adventure_from_api(data)
        self.assertEqual(adv.stage_count, 3)
        self.assertEqual(adv.owner, "TestUser")
        self.assertAlmostEqual(adv.latitude, 52.52)

    def test_update_existing_adventure(self):
        data = _minimal_api_data()
        adv1, _ = save_adventure_from_api(data)
        data2 = _minimal_api_data(title="Updated Title")
        adv2, _ = save_adventure_from_api(data2)
        self.assertEqual(adv1.pk, adv2.pk)
        adv1.refresh_from_db()
        self.assertEqual(adv1.title, "Updated Title")

    def test_creates_stages_from_stages_list(self):
        data = _minimal_api_data(stages=[
            {
                "al_stage_uuid": "stage-uuid-1",
                "stage_number": 1,
                "lat": 52.51,
                "lon": 13.40,
                "title": "Stage 1",
                "long_description": "",
                "question_text": "Q1",
                "challenge_type": "Unknown",
                "key_image_url": "",
                "geofencing_radius": 50,
                "is_final": False,
            },
        ])
        adv, stats = save_adventure_from_api(data)
        self.assertEqual(Geocache.objects.filter(adventure=adv, al_detail__isnull=False).count(), 1)
        self.assertEqual(stats.created, 1)

    def test_tags_applied_to_parent(self):
        tag = Tag.objects.create(name="Lab")
        data = _minimal_api_data()
        adv, _ = save_adventure_from_api(data, tags=[tag])
        parent = Geocache.objects.get(al_code=adv.code)
        self.assertIn(tag, parent.tags.all())

    def test_extended_fields_updated(self):
        data = _minimal_api_data(
            key_image_url="https://example.com/img.jpg",
            adventure_type="Linear",
            smart_link="https://labs.geocaching.com/xxx",
            median_time_to_complete=30,
            themes=["nature"],
        )
        adv, _ = save_adventure_from_api(data)
        adv.refresh_from_db()
        self.assertEqual(adv.key_image_url, "https://example.com/img.jpg")
        self.assertEqual(adv.adventure_type, "Linear")
        self.assertEqual(adv.median_time_to_complete, 30)
        self.assertEqual(adv.themes, ["nature"])


# ---------------------------------------------------------------------------
# save_alc_stage — detail field extraction
# ---------------------------------------------------------------------------

class SaveAlcStageDetailTest(TestCase):
    def test_question_text_stored_in_al_detail(self):
        stats = ImportStats()
        save_alc_stage("LC1234-1", _stage_fields(question_text="What color?"), [], stats)
        detail = ALStageDetail.objects.get(geocache__al_code="LC1234-1")
        self.assertEqual(detail.question_text, "What color?")

    def test_stage_number_stored_in_al_detail(self):
        stats = ImportStats()
        save_alc_stage("LC1234-1", _stage_fields(stage_number=3), [], stats)
        detail = ALStageDetail.objects.get(geocache__al_code="LC1234-1")
        self.assertEqual(detail.stage_number, 3)

    def test_al_stage_uuid_stored_in_al_detail(self):
        stats = ImportStats()
        save_alc_stage("LC1234-1", _stage_fields(al_stage_uuid="my-uuid"), [], stats)
        detail = ALStageDetail.objects.get(geocache__al_code="LC1234-1")
        self.assertEqual(detail.al_stage_uuid, "my-uuid")

    def test_answer_hash_stored(self):
        stats = ImportStats()
        save_alc_stage("LC1234-1", _stage_fields(al_answer_hash="abc123"), [], stats)
        detail = ALStageDetail.objects.get(geocache__al_code="LC1234-1")
        self.assertEqual(detail.answer_hash, "abc123")

    def test_answer_hash_change_resets_correct_flag(self):
        stats = ImportStats()
        save_alc_stage("LC1234-1", _stage_fields(al_answer_hash="hash1"), [], stats)
        detail = ALStageDetail.objects.get(geocache__al_code="LC1234-1")
        detail.answer_is_correct = True
        detail.save()
        save_alc_stage("LC1234-1", _stage_fields(al_answer_hash="hash2"), [], stats)
        detail.refresh_from_db()
        self.assertIsNone(detail.answer_is_correct)

    def test_empty_question_text_leaves_detail_question_empty(self):
        stats = ImportStats()
        save_alc_stage("LC1234-1", _stage_fields(question_text=""), [], stats)
        detail = ALStageDetail.objects.get(geocache__al_code="LC1234-1")
        self.assertEqual(detail.question_text, "")

    def test_al_journal_text_not_stored_in_detail(self):
        stats = ImportStats()
        save_alc_stage(
            "LC1234-1",
            _stage_fields(al_journal_text="My personal journal"),
            [], stats,
        )
        detail = ALStageDetail.objects.get(geocache__al_code="LC1234-1")
        self.assertFalse(hasattr(detail, "al_journal_text"))


# ---------------------------------------------------------------------------
# merge_duplicate_adventure
# ---------------------------------------------------------------------------

class MergeDuplicateAdventureTest(TestCase):
    def _make_adventure_tree(self, code, stage_count=2, found_stages=None):
        found_stages = found_stages or set()
        adv = Adventure.objects.create(
            code=code, title=f"Adventure {code}",
            latitude=52.0, longitude=13.0,
        )
        parent = Geocache.objects.create(
            al_code=code, name=f"Parent {code}", cache_type=CacheType.LAB,
            latitude=52.0, longitude=13.0, adventure=adv,
        )
        stages = []
        for i in range(1, stage_count + 1):
            stage = Geocache.objects.create(
                al_code=f"{code}-{i}", name=f"Stage {i}", cache_type=CacheType.LAB,
                latitude=52.0 + i * 0.001, longitude=13.0 + i * 0.001,
                adventure=adv, found=i in found_stages,
            )
            ALStageDetail.objects.create(geocache=stage, stage_number=i)
            stages.append(stage)
        return adv, parent, stages

    def test_old_adventure_deleted_after_merge(self):
        old_adv, _, _ = self._make_adventure_tree("LCOLD", 2)
        can_adv, _, _ = self._make_adventure_tree("LCCAN", 2)
        merge_duplicate_adventure(old_adv, can_adv)
        self.assertFalse(Adventure.objects.filter(code="LCOLD").exists())

    def test_found_promoted_to_canonical(self):
        old_adv, _, old_stages = self._make_adventure_tree("LCOLD", 2, found_stages={1})
        can_adv, _, can_stages = self._make_adventure_tree("LCCAN", 2)
        merge_duplicate_adventure(old_adv, can_adv)
        can_stages[0].refresh_from_db()
        self.assertTrue(can_stages[0].found)

    def test_unfound_not_overwritten(self):
        old_adv, _, old_stages = self._make_adventure_tree("LCOLD", 2)
        can_adv, _, can_stages = self._make_adventure_tree("LCCAN", 2, found_stages={1})
        merge_duplicate_adventure(old_adv, can_adv)
        can_stages[0].refresh_from_db()
        self.assertTrue(can_stages[0].found)

    def test_tags_transferred(self):
        tag = Tag.objects.create(name="TransferTag")
        old_adv, old_parent, old_stages = self._make_adventure_tree("LCOLD", 1)
        old_stages[0].tags.add(tag)
        can_adv, _, can_stages = self._make_adventure_tree("LCCAN", 1)
        merge_duplicate_adventure(old_adv, can_adv)
        can_stages[0].refresh_from_db()
        self.assertIn(tag, can_stages[0].tags.all())

    def test_parent_tags_transferred(self):
        tag = Tag.objects.create(name="ParentTag")
        old_adv, old_parent, _ = self._make_adventure_tree("LCOLD", 1)
        old_parent.tags.add(tag)
        can_adv, can_parent, _ = self._make_adventure_tree("LCCAN", 1)
        merge_duplicate_adventure(old_adv, can_adv)
        can_parent.refresh_from_db()
        self.assertIn(tag, can_parent.tags.all())
