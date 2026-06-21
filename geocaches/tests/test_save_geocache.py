"""
Tests for geocaches.services.save_geocache — the canonical single-cache persistence function.
"""

from datetime import date

from django.test import TestCase

from geocaches.models import (
    ALStageDetail,
    Adventure,
    Attribute,
    CacheSize,
    CacheStatus,
    CacheType,
    CorrectedCoordinates,
    Geocache,
    Image,
    Log,
    LogType,
    Note,
    NoteType,
    Tag,
    Waypoint,
    WaypointType,
)
from geocaches.services import SaveResult, save_geocache


def _fields(**overrides):
    """Return a minimal dict of Geocache model fields for save_geocache()."""
    defaults = {
        "name": "Test Cache",
        "owner": "Owner",
        "cache_type": CacheType.TRADITIONAL,
        "size": CacheSize.SMALL,
        "status": CacheStatus.ACTIVE,
        "latitude": 48.5,
        "longitude": 9.1,
        "difficulty": 2.0,
        "terrain": 1.5,
        "short_description": "",
        "long_description": "",
        "hint": "",
        "hidden_date": date(2020, 1, 1),
        "country": "Germany",
        "state": "BW",
        "fav_points": 5,
        "has_trackable": False,
        "primary_source": "gc",
    }
    defaults.update(overrides)
    return defaults


# ---------------------------------------------------------------------------
# Lookup and creation
# ---------------------------------------------------------------------------

class TestLookupAndCreation(TestCase):
    def test_creates_new_geocache_by_gc_code(self):
        result = save_geocache(gc_code="GC11111", fields=_fields())
        self.assertTrue(result.created)
        self.assertFalse(result.locked)
        self.assertFalse(result.updated)
        self.assertEqual(Geocache.objects.count(), 1)
        self.assertEqual(result.geocache.gc_code, "GC11111")

    def test_creates_new_geocache_by_oc_code(self):
        result = save_geocache(oc_code="OC1234", fields=_fields())
        self.assertTrue(result.created)
        self.assertEqual(result.geocache.oc_code, "OC1234")

    def test_updates_existing_geocache(self):
        save_geocache(gc_code="GC11111", fields=_fields())
        result = save_geocache(gc_code="GC11111", fields=_fields(name="Updated"))
        self.assertFalse(result.created)
        self.assertTrue(result.updated)
        self.assertFalse(result.locked)
        self.assertEqual(result.geocache.name, "Updated")

    def test_raises_without_any_identity(self):
        with self.assertRaises(ValueError):
            save_geocache(fields=_fields())

    def test_al_stage_uuid_lookup(self):
        cache = Geocache.objects.create(
            al_code="LCTEST", name="Lab Stage", cache_type=CacheType.LAB,
            latitude=48.0, longitude=9.0,
        )
        ALStageDetail.objects.create(geocache=cache, stage_number=1, al_stage_uuid="aaaa-bbbb-cccc")
        result = save_geocache(
            al_stage_uuid="aaaa-bbbb-cccc",
            al_code="LCTEST",
            fields=_fields(name="Updated Lab"),
        )
        self.assertFalse(result.created)
        self.assertEqual(result.geocache.pk, cache.pk)
        self.assertEqual(result.geocache.name, "Updated Lab")

    def test_al_stage_uuid_fallback_to_gc_code(self):
        """If UUID doesn't match, falls back to gc_code lookup."""
        result = save_geocache(
            al_stage_uuid="nonexistent-uuid",
            gc_code="GC22222",
            fields=_fields(),
        )
        self.assertTrue(result.created)
        self.assertEqual(result.geocache.gc_code, "GC22222")

    def test_returns_save_result_dataclass(self):
        result = save_geocache(gc_code="GC11111", fields=_fields())
        self.assertIsInstance(result, SaveResult)


# ---------------------------------------------------------------------------
# Import lock
# ---------------------------------------------------------------------------

class TestImportLock(TestCase):
    def test_skips_locked_cache(self):
        Geocache.objects.create(
            gc_code="GC11111", name="Original", cache_type=CacheType.TRADITIONAL,
            size=CacheSize.SMALL, status=CacheStatus.ACTIVE,
            latitude=48.5, longitude=9.1, import_locked=True,
        )
        result = save_geocache(gc_code="GC11111", fields=_fields(name="Should Not Update"))
        self.assertTrue(result.locked)
        self.assertFalse(result.created)
        self.assertFalse(result.updated)
        self.assertEqual(Geocache.objects.get(gc_code="GC11111").name, "Original")

    def test_locked_skips_tags(self):
        Geocache.objects.create(
            gc_code="GC11111", name="Locked", cache_type=CacheType.TRADITIONAL,
            latitude=48.5, longitude=9.1, import_locked=True,
        )
        tag = Tag.objects.create(name="ShouldNotApply")
        result = save_geocache(gc_code="GC11111", fields=_fields(), tags=[tag])
        self.assertTrue(result.locked)
        self.assertEqual(result.geocache.tags.count(), 0)

    def test_locked_skips_logs(self):
        Geocache.objects.create(
            gc_code="GC11111", name="Locked", cache_type=CacheType.TRADITIONAL,
            latitude=48.5, longitude=9.1, import_locked=True,
        )
        logs = [{"source_id": "1", "log_type": LogType.FOUND,
                 "user_name": "A", "logged_date": date(2023, 1, 1), "text": "", "source": "gc"}]
        result = save_geocache(gc_code="GC11111", fields=_fields(), logs=logs)
        self.assertTrue(result.locked)
        self.assertEqual(Log.objects.count(), 0)


# ---------------------------------------------------------------------------
# Found status promotion
# ---------------------------------------------------------------------------

class TestFoundPromotion(TestCase):
    def test_found_promotes_unfound(self):
        save_geocache(gc_code="GC11111", fields=_fields())
        result = save_geocache(
            gc_code="GC11111", fields=_fields(),
            found=True, found_date=date(2023, 5, 20),
        )
        self.assertTrue(result.geocache.found)
        self.assertEqual(result.geocache.found_date, date(2023, 5, 20))

    def test_found_never_demoted(self):
        Geocache.objects.create(
            gc_code="GC11111", name="Found", cache_type=CacheType.TRADITIONAL,
            latitude=48.5, longitude=9.1, found=True, found_date=date(2023, 1, 1),
        )
        result = save_geocache(gc_code="GC11111", fields=_fields(), found=None)
        self.assertTrue(result.geocache.found)
        self.assertEqual(result.geocache.found_date, date(2023, 1, 1))

    def test_backfills_found_date_if_missing(self):
        Geocache.objects.create(
            gc_code="GC11111", name="Found", cache_type=CacheType.TRADITIONAL,
            latitude=48.5, longitude=9.1, found=True, found_date=None,
        )
        result = save_geocache(
            gc_code="GC11111", fields=_fields(),
            found=True, found_date=date(2024, 3, 15),
        )
        self.assertEqual(result.geocache.found_date, date(2024, 3, 15))

    def test_found_date_not_overwritten_if_already_set(self):
        Geocache.objects.create(
            gc_code="GC11111", name="Found", cache_type=CacheType.TRADITIONAL,
            latitude=48.5, longitude=9.1, found=True, found_date=date(2020, 6, 15),
        )
        result = save_geocache(
            gc_code="GC11111", fields=_fields(),
            found=True, found_date=date(2024, 3, 15),
        )
        # Original found_date preserved
        self.assertEqual(result.geocache.found_date, date(2020, 6, 15))

    def test_found_on_create_sets_both(self):
        result = save_geocache(
            gc_code="GC11111",
            fields=_fields(found=True, found_date=date(2023, 5, 20)),
            found=True,
            found_date=date(2023, 5, 20),
        )
        self.assertTrue(result.geocache.found)
        self.assertEqual(result.geocache.found_date, date(2023, 5, 20))


# ---------------------------------------------------------------------------
# Tags
# ---------------------------------------------------------------------------

class TestTags(TestCase):
    def test_applies_tags(self):
        tag = Tag.objects.create(name="PQ1")
        result = save_geocache(gc_code="GC11111", fields=_fields(), tags=[tag])
        self.assertIn(tag, result.geocache.tags.all())

    def test_multiple_tags(self):
        t1 = Tag.objects.create(name="PQ1")
        t2 = Tag.objects.create(name="2024")
        save_geocache(gc_code="GC11111", fields=_fields(), tags=[t1, t2])
        cache = Geocache.objects.get(gc_code="GC11111")
        self.assertEqual(cache.tags.count(), 2)

    def test_no_tags_is_fine(self):
        result = save_geocache(gc_code="GC11111", fields=_fields())
        self.assertEqual(result.geocache.tags.count(), 0)


# ---------------------------------------------------------------------------
# Attributes
# ---------------------------------------------------------------------------

class TestAttributes(TestCase):
    def test_creates_attributes(self):
        attrs = [
            {"source": "gc", "attribute_id": 1, "is_positive": True, "name": "Dogs"},
            {"source": "gc", "attribute_id": 2, "is_positive": False, "name": "No Dogs"},
        ]
        result = save_geocache(gc_code="GC11111", fields=_fields(), attributes=attrs)
        self.assertEqual(result.geocache.attributes.count(), 2)

    def test_reuses_existing_attributes(self):
        Attribute.objects.create(source="gc", attribute_id=1, is_positive=True, name="Dogs")
        attrs = [{"source": "gc", "attribute_id": 1, "is_positive": True, "name": "Dogs"}]
        save_geocache(gc_code="GC11111", fields=_fields(), attributes=attrs)
        self.assertEqual(Attribute.objects.count(), 1)

    def test_default_name_when_missing(self):
        attrs = [{"source": "gc", "attribute_id": 99, "is_positive": True}]
        save_geocache(gc_code="GC11111", fields=_fields(), attributes=attrs)
        self.assertEqual(Attribute.objects.get(attribute_id=99).name, "Attribute #99")


# ---------------------------------------------------------------------------
# Logs
# ---------------------------------------------------------------------------

class TestLogs(TestCase):
    def test_creates_logs(self):
        logs = [
            {"source_id": "1", "log_type": LogType.FOUND,
             "user_name": "Alice", "logged_date": date(2023, 1, 1), "text": "", "source": "gc"},
        ]
        save_geocache(gc_code="GC11111", fields=_fields(), logs=logs)
        self.assertEqual(Log.objects.count(), 1)

    def test_deduplicates_by_source_id(self):
        logs = [
            {"source_id": "1", "log_type": LogType.FOUND,
             "user_name": "Alice", "logged_date": date(2023, 1, 1), "text": "", "source": "gc"},
        ]
        save_geocache(gc_code="GC11111", fields=_fields(), logs=logs)
        save_geocache(gc_code="GC11111", fields=_fields(), logs=logs)
        self.assertEqual(Log.objects.count(), 1)

    def test_skips_logs_without_source_id(self):
        logs = [
            {"source_id": "", "log_type": LogType.FOUND,
             "user_name": "Alice", "logged_date": date(2023, 1, 1), "text": "", "source": "gc"},
        ]
        save_geocache(gc_code="GC11111", fields=_fields(), logs=logs)
        self.assertEqual(Log.objects.count(), 0)

    def test_multiple_logs_created(self):
        logs = [
            {"source_id": "1", "log_type": LogType.FOUND,
             "user_name": "A", "logged_date": date(2023, 1, 1), "text": "", "source": "gc"},
            {"source_id": "2", "log_type": LogType.DNF,
             "user_name": "B", "logged_date": date(2023, 2, 1), "text": "", "source": "gc"},
        ]
        save_geocache(gc_code="GC11111", fields=_fields(), logs=logs)
        self.assertEqual(Log.objects.count(), 2)


# ---------------------------------------------------------------------------
# Waypoints
# ---------------------------------------------------------------------------

class TestWaypoints(TestCase):
    def test_creates_waypoints(self):
        wpts = [
            {"lookup": "P111111", "prefix": "P1", "name": "Parking",
             "waypoint_type": WaypointType.PARKING, "latitude": 48.5,
             "longitude": 9.1, "note": "", "is_user_created": False},
        ]
        save_geocache(gc_code="GC11111", fields=_fields(), waypoints=wpts)
        self.assertEqual(Waypoint.objects.count(), 1)

    def test_updates_existing_waypoint(self):
        wpts = [
            {"lookup": "P111111", "prefix": "P1", "name": "Parking",
             "waypoint_type": WaypointType.PARKING, "latitude": 48.5,
             "longitude": 9.1, "note": "", "is_user_created": False},
        ]
        save_geocache(gc_code="GC11111", fields=_fields(), waypoints=wpts)
        wpts[0]["name"] = "Updated Parking"
        save_geocache(gc_code="GC11111", fields=_fields(), waypoints=wpts)
        self.assertEqual(Waypoint.objects.count(), 1)
        self.assertEqual(Waypoint.objects.first().name, "Updated Parking")

    def test_does_not_mutate_caller_data(self):
        wpts = [
            {"lookup": "P111111", "prefix": "P1", "name": "Parking",
             "waypoint_type": WaypointType.PARKING, "latitude": 48.5,
             "longitude": 9.1, "note": "", "is_user_created": False},
        ]
        save_geocache(gc_code="GC11111", fields=_fields(), waypoints=wpts)
        # The caller's dict should still have "lookup"
        self.assertIn("lookup", wpts[0])


# ---------------------------------------------------------------------------
# Corrected coordinates
# ---------------------------------------------------------------------------

class TestCorrectedCoords(TestCase):
    def test_creates_corrected_coords(self):
        coords = {"latitude": 48.6, "longitude": 9.2, "note": "Solved"}
        save_geocache(gc_code="GC11111", fields=_fields(), corrected_coords=coords)
        cc = CorrectedCoordinates.objects.get(geocache__gc_code="GC11111")
        self.assertAlmostEqual(cc.latitude, 48.6)
        self.assertEqual(cc.note, "Solved")

    def test_updates_corrected_coords(self):
        save_geocache(
            gc_code="GC11111", fields=_fields(),
            corrected_coords={"latitude": 48.6, "longitude": 9.2},
        )
        save_geocache(
            gc_code="GC11111", fields=_fields(),
            corrected_coords={"latitude": 48.7, "longitude": 9.3},
        )
        self.assertEqual(CorrectedCoordinates.objects.count(), 1)
        cc = CorrectedCoordinates.objects.first()
        self.assertAlmostEqual(cc.latitude, 48.7)


# ---------------------------------------------------------------------------
# Images
# ---------------------------------------------------------------------------

class TestImages(TestCase):
    def test_creates_images(self):
        imgs = [{"url": "https://example.com/1.jpg", "name": "Photo 1"}]
        save_geocache(gc_code="GC11111", fields=_fields(), images=imgs)
        self.assertEqual(Image.objects.count(), 1)

    def test_deduplicates_images_by_url(self):
        imgs = [{"url": "https://example.com/1.jpg", "name": "Photo 1"}]
        save_geocache(gc_code="GC11111", fields=_fields(), images=imgs)
        save_geocache(gc_code="GC11111", fields=_fields(), images=imgs)
        self.assertEqual(Image.objects.count(), 1)


# ---------------------------------------------------------------------------
# Notes
# ---------------------------------------------------------------------------

class TestNotes(TestCase):
    def test_creates_notes(self):
        note_data = [{"note_type": NoteType.NOTE, "body": "My note"}]
        save_geocache(gc_code="GC11111", fields=_fields(), notes=note_data)
        self.assertEqual(Note.objects.count(), 1)

    def test_skip_notes_if_exist_default(self):
        note_data = [{"note_type": NoteType.NOTE, "body": "First"}]
        save_geocache(gc_code="GC11111", fields=_fields(), notes=note_data)
        # Second import — notes should be skipped (default skip_notes_if_exist=True)
        save_geocache(gc_code="GC11111", fields=_fields(), notes=[{"note_type": NoteType.NOTE, "body": "Second"}])
        self.assertEqual(Note.objects.count(), 1)
        self.assertEqual(Note.objects.first().body, "First")

    def test_skip_notes_if_exist_false(self):
        note_data = [{"note_type": NoteType.NOTE, "body": "First"}]
        save_geocache(gc_code="GC11111", fields=_fields(), notes=note_data)
        save_geocache(
            gc_code="GC11111", fields=_fields(),
            notes=[{"note_type": NoteType.NOTE, "body": "Second"}],
            skip_notes_if_exist=False,
        )
        self.assertEqual(Note.objects.count(), 2)


# ---------------------------------------------------------------------------
# AL-parent found guard (moved from Geocache.save to services)
# ---------------------------------------------------------------------------

class TestAlParentFoundGuard(TestCase):
    def setUp(self):
        self.adv = Adventure.objects.create(
            code="LCGUARD", title="Guard Test",
            latitude=52.52, longitude=13.405,
        )

    def _al_fields(self, **overrides):
        return _fields(
            cache_type=CacheType.LAB,
            primary_source="",
            **overrides,
        )

    def test_rejects_found_on_al_parent_create(self):
        with self.assertRaises(ValueError):
            save_geocache(
                al_code="LCGUARD",
                fields=self._al_fields(
                    found=True,
                    adventure=self.adv,
                ),
            )

    def test_rejects_found_on_al_parent_update(self):
        save_geocache(
            al_code="LCGUARD",
            fields=self._al_fields(adventure=self.adv),
        )
        with self.assertRaises(ValueError):
            save_geocache(
                al_code="LCGUARD",
                fields=self._al_fields(
                    found=True,
                    adventure=self.adv,
                ),
            )

    def test_rejects_found_promotion_on_al_parent(self):
        save_geocache(
            al_code="LCGUARD",
            fields=self._al_fields(adventure=self.adv),
        )
        with self.assertRaises(ValueError):
            save_geocache(
                al_code="LCGUARD",
                fields=self._al_fields(adventure=self.adv),
                found=True,
                found_date=date(2024, 1, 1),
            )

    def test_accepts_found_on_al_stage(self):
        # Create the stage first, then add ALStageDetail so the guard knows it's a stage.
        save_geocache(
            al_code="LCGUARD-1",
            fields=self._al_fields(adventure=self.adv),
        )
        stage = Geocache.objects.get(al_code="LCGUARD-1")
        ALStageDetail.objects.create(geocache=stage, stage_number=1)
        result = save_geocache(
            al_code="LCGUARD-1",
            fields=self._al_fields(adventure=self.adv),
            found=True,
            found_date=date(2024, 1, 1),
        )
        self.assertTrue(result.geocache.found)

    def test_accepts_completed_on_al_parent(self):
        result = save_geocache(
            al_code="LCGUARD",
            fields=self._al_fields(
                completed=True,
                adventure=self.adv,
            ),
        )
        self.assertTrue(result.geocache.completed)
        self.assertFalse(result.geocache.found)

    def test_direct_save_no_longer_raises(self):
        """After moving the guard to services, direct .save() must not raise."""
        cache = Geocache.objects.create(
            al_code="LCGUARD", name="Parent",
            cache_type=CacheType.LAB,
            latitude=52.52, longitude=13.405,
            adventure=self.adv,
        )
        cache.found = True
        cache.save()  # should NOT raise


# ---------------------------------------------------------------------------
# Related objects — attributes, images, notes, OC extension, corrected coords
# ---------------------------------------------------------------------------

class TestRelatedObjects(TestCase):
    def test_attributes_added(self):
        from geocaches.models import Attribute
        attr = Attribute.objects.create(
            source="gc", attribute_id=1, is_positive=True, name="Dogs Allowed"
        )
        attributes = [{"source": "gc", "attribute_id": 1, "is_positive": True, "name": "Dogs"}]
        result = save_geocache(gc_code="GC11111", fields=_fields(), attributes=attributes)
        self.assertIn(attr, result.geocache.attributes.all())

    def test_attributes_deduped_on_update(self):
        attrs = [{"source": "gc", "attribute_id": 1, "is_positive": True, "name": "Dogs"}]
        save_geocache(gc_code="GC11111", fields=_fields(), attributes=attrs)
        save_geocache(gc_code="GC11111", fields=_fields(), attributes=attrs)
        self.assertEqual(Geocache.objects.get(gc_code="GC11111").attributes.count(), 1)

    def test_images_added(self):
        images = [{"url": "https://example.com/img.jpg", "name": "Photo"}]
        result = save_geocache(gc_code="GC11111", fields=_fields(), images=images)
        from geocaches.models import Image
        self.assertEqual(Image.objects.filter(geocache=result.geocache).count(), 1)

    def test_images_deduped_by_url(self):
        images = [{"url": "https://example.com/img.jpg", "name": "Photo"}]
        save_geocache(gc_code="GC11111", fields=_fields(), images=images)
        save_geocache(gc_code="GC11111", fields=_fields(), images=images)
        from geocaches.models import Image
        self.assertEqual(Image.objects.filter(geocache__gc_code="GC11111").count(), 1)

    def test_notes_added(self):
        from geocaches.models import NoteType
        notes = [{"note_type": NoteType.NOTE, "body": "My note"}]
        result = save_geocache(gc_code="GC11111", fields=_fields(), notes=notes)
        self.assertEqual(result.geocache.notes.count(), 1)

    def test_notes_skip_if_exist(self):
        from geocaches.models import Note, NoteType
        notes = [{"note_type": NoteType.NOTE, "body": "My note"}]
        save_geocache(gc_code="GC11111", fields=_fields(), notes=notes, skip_notes_if_exist=True)
        save_geocache(gc_code="GC11111", fields=_fields(), notes=notes, skip_notes_if_exist=True)
        self.assertEqual(Note.objects.filter(geocache__gc_code="GC11111").count(), 1)

    def test_notes_not_skipped_if_flag_false(self):
        from geocaches.models import Note, NoteType
        notes = [{"note_type": NoteType.NOTE, "body": "My note"}]
        save_geocache(gc_code="GC11111", fields=_fields(), notes=notes, skip_notes_if_exist=False)
        save_geocache(gc_code="GC11111", fields=_fields(), notes=notes, skip_notes_if_exist=False)
        self.assertEqual(Note.objects.filter(geocache__gc_code="GC11111").count(), 2)

    def test_corrected_coords_saved(self):
        from geocaches.models import CorrectedCoordinates
        cc = {"latitude": 48.501, "longitude": 9.101}
        result = save_geocache(gc_code="GC11111", fields=_fields(), corrected_coords=cc)
        self.assertTrue(CorrectedCoordinates.objects.filter(geocache=result.geocache).exists())
        result.geocache.refresh_from_db()
        self.assertTrue(result.geocache.has_corrected_coordinates)

    def test_oc_extension_saved(self):
        from geocaches.models import OCExtension
        oc_ext = {"req_passwd": True, "trip_time": 60}
        result = save_geocache(gc_code="GC11111", fields=_fields(), oc_ext=oc_ext)
        self.assertTrue(OCExtension.objects.filter(geocache=result.geocache).exists())

    def test_waypoints_added(self):
        wp = [{
            "lookup": "WP1", "name": "Stage 1", "latitude": 48.501, "longitude": 9.101,
            "waypoint_type": "Stage of a Multi-Cache",
        }]
        result = save_geocache(gc_code="GC11111", fields=_fields(), waypoints=wp)
        self.assertEqual(result.geocache.waypoints.count(), 1)

    def test_waypoints_user_modified_not_overwritten(self):
        from geocaches.models import WaypointType
        wp_data = [{
            "lookup": "WP1", "name": "Stage 1", "latitude": 48.501, "longitude": 9.101,
            "waypoint_type": WaypointType.STAGE,
        }]
        result = save_geocache(gc_code="GC11111", fields=_fields(), waypoints=wp_data)
        wp = result.geocache.waypoints.get(lookup="WP1")
        wp.is_user_modified = True
        wp.name = "My Custom Name"
        wp.save()
        wp_data[0]["name"] = "Should Not Apply"
        save_geocache(gc_code="GC11111", fields=_fields(), waypoints=wp_data)
        wp.refresh_from_db()
        self.assertEqual(wp.name, "My Custom Name")


# ---------------------------------------------------------------------------
# Log deduplication
# ---------------------------------------------------------------------------

class TestLogDedup(TestCase):
    def test_logs_deduped_by_source_id(self):
        from geocaches.models import LogType
        logs = [{
            "source_id": "LOG1", "log_type": LogType.FOUND,
            "user_name": "Finder", "logged_date": date(2023, 1, 1),
            "text": "", "source": "gc",
        }]
        save_geocache(gc_code="GC11111", fields=_fields(), logs=logs)
        save_geocache(gc_code="GC11111", fields=_fields(), logs=logs)
        from geocaches.models import Log
        self.assertEqual(Log.objects.filter(geocache__gc_code="GC11111").count(), 1)

    def test_logs_deduped_by_date_user_type(self):
        from geocaches.models import Log, LogType
        logs1 = [{
            "source_id": "LOG1", "log_type": LogType.FOUND,
            "user_name": "Finder", "logged_date": date(2023, 1, 1),
            "text": "", "source": "gc",
        }]
        logs2 = [{
            "source_id": "LOG2",  # different id
            "log_type": LogType.FOUND,
            "user_name": "Finder", "logged_date": date(2023, 1, 1),
            "text": "", "source": "gc",
        }]
        save_geocache(gc_code="GC11111", fields=_fields(), logs=logs1)
        save_geocache(gc_code="GC11111", fields=_fields(), logs=logs2)
        self.assertEqual(Log.objects.filter(geocache__gc_code="GC11111").count(), 1)

    def test_logs_without_source_id_skipped(self):
        from geocaches.models import Log, LogType
        logs = [{
            "source_id": "", "log_type": LogType.NOTE,
            "user_name": "Someone", "logged_date": date(2023, 1, 1),
            "text": "No ID", "source": "gc",
        }]
        save_geocache(gc_code="GC11111", fields=_fields(), logs=logs)
        self.assertEqual(Log.objects.count(), 0)

    def test_log_date_string_parsed(self):
        from geocaches.models import Log, LogType
        logs = [{
            "source_id": "LOG1", "log_type": LogType.FOUND,
            "user_name": "Finder", "logged_date": "2023-01-01",
            "text": "", "source": "gc",
        }]
        save_geocache(gc_code="GC11111", fields=_fields(), logs=logs)
        self.assertEqual(Log.objects.count(), 1)
