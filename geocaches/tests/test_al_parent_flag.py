"""Tests for the explicit ``Geocache.is_al_parent`` flag.

Covers the 0046 backfill's classification, every writer that builds an AL tree,
``is_al_parent()`` reading the flag instead of probing for an ALStageDetail row,
and the two behaviours that hang off the parent test: the cascade delete and the
completed-recompute signal.
"""
from datetime import datetime, timezone
from importlib import import_module
from pathlib import Path

from django.apps import apps as django_apps
from django.test import TestCase
from django.urls import reverse
from django.utils.timezone import now as tz_now

from geocaches.importers.lab2gpx import import_lab2gpx
from geocaches.lc_code import uuid_to_lc_code
from geocaches.models import ALStageDetail, Adventure, CacheType, Geocache
from geocaches.services.adventures import is_al_parent, recompute_adventure_completed
from geocaches.services.save_alc import save_adventure_from_api

FIXTURES = Path(__file__).resolve().parent / "fixtures"

# The adventure UUID inside the lab2gpx fixtures; the LC code derives from it.
_LAB2GPX_UUID = "4cef8a3f-ec20-472a-8de2-9b4859bc6526"


def _adventure(code="LCFLAG"):
    return Adventure.objects.create(
        code=code, title="Flag Adventure", latitude=52.52, longitude=13.405,
    )


def _row(al_code, adv, **kwargs):
    defaults = dict(
        al_code=al_code, name=al_code, cache_type=CacheType.LAB,
        latitude=52.52, longitude=13.405, adventure=adv,
    )
    defaults.update(kwargs)
    return Geocache.objects.create(**defaults)


def _flag_of(pk):
    """Read the flag through all_objects so trashed rows are visible too."""
    return Geocache.all_objects.get(pk=pk).is_al_parent


# ---------------------------------------------------------------------------
# 0046 data migration
# ---------------------------------------------------------------------------

class MigrationBackfillTest(TestCase):
    """The backfill must reproduce the old derived rule exactly."""

    def _run_backfill(self):
        module = import_module("geocaches.migrations.0046_geocache_is_al_parent")
        module.forwards(django_apps, None)

    def test_flags_parent_but_not_stage_or_plain_cache(self):
        adv = _adventure()
        parent = _row("LCFLAG", adv, is_al_parent=False)
        stage = _row("LCFLAG-1", adv, is_al_parent=False)
        ALStageDetail.objects.create(geocache=stage, stage_number=1)
        plain = Geocache.objects.create(
            gc_code="GC0001", name="Plain", cache_type=CacheType.TRADITIONAL,
            latitude=48.0, longitude=9.0,
        )

        self._run_backfill()

        self.assertTrue(_flag_of(parent.pk), "adventure row with no stage detail is a parent")
        self.assertFalse(_flag_of(stage.pk), "a row with stage detail is a stage")
        self.assertFalse(_flag_of(plain.pk), "a non-AL cache is never a parent")

    def test_flags_trashed_parents_too(self):
        """Trashed parents keep their stages and must return as parents."""
        adv = _adventure("LCTRSH")
        parent = _row("LCTRSH", adv, is_al_parent=False, deleted_at=tz_now())

        self._run_backfill()

        self.assertTrue(_flag_of(parent.pk))

    def test_is_idempotent(self):
        adv = _adventure()
        parent = _row("LCFLAG", adv, is_al_parent=False)

        self._run_backfill()
        self._run_backfill()

        self.assertTrue(_flag_of(parent.pk))


# ---------------------------------------------------------------------------
# Writers
# ---------------------------------------------------------------------------

class WriterMaintainsFlagTest(TestCase):
    """Every path that creates an AL tree must flag the parent and only the parent."""

    def test_alc_api_save(self):
        adv, _ = save_adventure_from_api({
            "adventure_guid": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            "title": "API Adventure", "owner": "Tester",
            "lat": 52.52, "lon": 13.405, "status": "Active",
            "description": "", "stage_count": 1,
            "stages": [{
                "stage_uuid": "stage-uuid-1", "stage_number": 1,
                "lat": 52.5, "lon": 13.4, "name": "Stage 1",
                "question": "Q?", "description": "",
                "answer_hash": "", "answer_code_hashes": [], "choices": [],
                "key_image_url": "", "geofencing_radius": 50,
                "challenge_type": "", "is_final": False,
            }],
        })

        self.assertTrue(Geocache.objects.get(al_code=adv.code).is_al_parent)
        self.assertFalse(Geocache.objects.get(al_code=f"{adv.code}-1").is_al_parent)

    def test_alc_api_save_repairs_an_unflagged_parent(self):
        """Re-importing an adventure restores a flag that somehow got cleared."""
        data = {
            "adventure_guid": "bbbbbbbb-cccc-dddd-eeee-ffffffffffff",
            "title": "Repair", "owner": "Tester",
            "lat": 52.52, "lon": 13.405, "status": "Active",
            "description": "", "stage_count": 0, "stages": [],
        }
        adv, _ = save_adventure_from_api(data)
        Geocache.objects.filter(al_code=adv.code).update(is_al_parent=False)

        save_adventure_from_api(data)

        self.assertTrue(Geocache.objects.get(al_code=adv.code).is_al_parent)

    def test_lab2gpx_format_a_import(self):
        import_lab2gpx(str(FIXTURES / "lab2gpx_format_a.gpx"))
        code = uuid_to_lc_code(_LAB2GPX_UUID)

        self.assertTrue(Geocache.objects.get(al_code=code).is_al_parent)
        self.assertFalse(Geocache.objects.get(al_code=f"{code}-1").is_al_parent)

    def test_lab2gpx_format_b_import(self):
        import_lab2gpx(str(FIXTURES / "lab2gpx_format_b.gpx"))
        code = uuid_to_lc_code(_LAB2GPX_UUID)

        self.assertTrue(Geocache.objects.get(al_code=code).is_al_parent)
        self.assertFalse(Geocache.objects.get(al_code=f"{code}-1").is_al_parent)

    def test_gsak_format_a_import(self):
        from geocaches.importers.gsak import _save_alc_stage_format_a
        from geocaches.tests.test_gsak import _empty_memos, _make_alc_row

        row = _make_alc_row(Code="LC28NG-1", Name="My Adventure : Stage 1")
        _save_alc_stage_format_a(
            row, "LC28NG-1", "28NG", 1, 48.5, 9.1,
            datetime.now(timezone.utc), [], "",
            _empty_memos(), {}, {},
        )

        self.assertTrue(Geocache.objects.get(al_code="LC28NG").is_al_parent)
        self.assertFalse(Geocache.objects.get(al_code="LC28NG-1").is_al_parent)

    def test_attaching_stage_detail_clears_the_parent_flag(self):
        """al_answer_save's get_or_create must not leave a row parent *and* stage."""
        adv = _adventure()
        parent = _row("LCFLAG", adv, is_al_parent=True)

        response = self.client.post(
            reverse("geocaches:al_answer_save", args=[parent.al_code]),
            {"user_answer": "blue"},
        )

        self.assertEqual(response.status_code, 302)
        parent.refresh_from_db()
        self.assertFalse(parent.is_al_parent)


# ---------------------------------------------------------------------------
# is_al_parent()
# ---------------------------------------------------------------------------

class IsAlParentReadsFlagTest(TestCase):
    def test_reads_the_flag_without_a_query(self):
        adv = _adventure()
        parent = _row("LCFLAG", adv, is_al_parent=True)

        with self.assertNumQueries(0):
            self.assertTrue(is_al_parent(parent))

    def test_unflagged_adventure_row_is_not_a_parent(self):
        adv = _adventure()
        stage = _row("LCFLAG-1", adv)
        ALStageDetail.objects.create(geocache=stage, stage_number=1)

        self.assertFalse(is_al_parent(stage))

    def test_plain_cache_is_not_a_parent(self):
        cache = Geocache.objects.create(
            gc_code="GC0002", name="Plain", cache_type=CacheType.TRADITIONAL,
            latitude=48.0, longitude=9.0,
        )
        self.assertFalse(is_al_parent(cache))


# ---------------------------------------------------------------------------
# Behaviours that hang off the parent test
# ---------------------------------------------------------------------------

class CascadeAndRecomputeTest(TestCase):
    def _tree(self, found_stages=()):
        adv = _adventure("LCCASC")
        parent = _row("LCCASC", adv, is_al_parent=True)
        stages = []
        for i in (1, 2):
            stage = _row(f"LCCASC-{i}", adv, found=i in found_stages)
            ALStageDetail.objects.create(geocache=stage, stage_number=i)
            stages.append(stage)
        return adv, parent, stages

    def test_deleting_a_flagged_parent_cascades_to_stages(self):
        _, parent, stages = self._tree()
        stage_pks = [s.pk for s in stages]

        parent.delete()

        self.assertEqual(Geocache.all_objects.filter(pk__in=stage_pks).count(), 0)

    def test_deleting_a_stage_does_not_cascade(self):
        _, parent, stages = self._tree()

        stages[0].delete()

        self.assertTrue(Geocache.objects.filter(pk=parent.pk).exists())
        self.assertTrue(Geocache.objects.filter(pk=stages[1].pk).exists())

    def test_recompute_marks_the_flagged_parent_completed(self):
        adv, parent, _ = self._tree(found_stages=(1, 2))

        self.assertTrue(recompute_adventure_completed(adv))

        parent.refresh_from_db()
        self.assertTrue(parent.completed)
        self.assertTrue(parent.found)

    def test_saving_a_stage_fires_the_recompute_signal(self):
        _, parent, stages = self._tree(found_stages=(1,))

        stages[1].found = True
        stages[1].save(update_fields=["found"])

        parent.refresh_from_db()
        self.assertTrue(parent.completed)

    def test_trashing_a_flagged_parent_cascades_to_stages(self):
        from geocaches.services.trash import trash_cache

        _, parent, stages = self._tree()
        stage_pks = [s.pk for s in stages]

        trash_cache(parent)

        self.assertEqual(Geocache.objects.filter(pk__in=stage_pks).count(), 0)
        self.assertEqual(
            Geocache.all_objects.filter(pk__in=stage_pks, deleted_at__isnull=False).count(), 2,
        )
