"""
Tests for geocaches.services._merge_into — the canonical merge of an OC cache
record into an existing GC cache.

Pin merge semantics so the upcoming refactor (which deletes the duplicate
_merge_misplaced helper in views.py) cannot silently change behaviour.
"""

from datetime import date

from django.test import TestCase

from geocaches.models import (
    CacheSize,
    CacheStatus,
    CacheType,
    Geocache,
    Image,
    Log,
    LogType,
    Note,
    NoteType,
    OCExtension,
    Tag,
    Waypoint,
    WaypointType,
)
from geocaches.services import _merge_into


def _cache(code_kind, code, **overrides):
    """Create a minimal Geocache. code_kind ∈ {'gc', 'oc'}."""
    defaults = dict(
        name=f"Cache {code}",
        owner="owner",
        cache_type=CacheType.TRADITIONAL,
        size=CacheSize.SMALL,
        status=CacheStatus.ACTIVE,
        latitude=48.0,
        longitude=9.0,
        difficulty=2.0,
        terrain=2.0,
        hidden_date=date(2020, 1, 1),
    )
    defaults.update(overrides)
    if code_kind == "gc":
        defaults["gc_code"] = code
        defaults.setdefault("primary_source", "gc")
    else:
        defaults["oc_code"] = code
        defaults.setdefault("primary_source", "oc_de")
    return Geocache.objects.create(**defaults)


class MergeFieldFill(TestCase):
    def test_empty_dest_field_filled_from_source(self):
        dest = _cache("gc", "GC0001", country="", elevation=None)
        src = _cache("oc", "OC0001", country="Germany", elevation=500)
        _merge_into(source=src, dest=dest, oc_code="OC0001")
        dest.refresh_from_db()
        self.assertEqual(dest.country, "Germany")
        self.assertEqual(dest.elevation, 500)
        self.assertEqual(dest.oc_code, "OC0001")

    def test_non_empty_dest_field_unchanged(self):
        dest = _cache("gc", "GC0002", country="Austria", elevation=300)
        src = _cache("oc", "OC0002", country="Germany", elevation=500)
        _merge_into(source=src, dest=dest, oc_code="OC0002")
        dest.refresh_from_db()
        self.assertEqual(dest.country, "Austria")
        self.assertEqual(dest.elevation, 300)

    def test_non_fill_field_never_overwritten(self):
        # `name` is NOT in the fill list — even if dest has the original and
        # source has a different one, dest keeps its name.
        dest = _cache("gc", "GC0003", name="GC Name")
        src = _cache("oc", "OC0003", name="OC Name")
        _merge_into(source=src, dest=dest, oc_code="OC0003")
        dest.refresh_from_db()
        self.assertEqual(dest.name, "GC Name")


class MergeLogs(TestCase):
    def test_logs_moved_when_dest_empty(self):
        dest = _cache("gc", "GC1001")
        src = _cache("oc", "OC1001")
        Log.objects.create(
            geocache=src, log_type=LogType.FOUND, user_name="u1",
            logged_date=date(2023, 1, 1), source="oc_de",
        )
        Log.objects.create(
            geocache=src, log_type=LogType.NOTE, user_name="u2",
            logged_date=date(2023, 2, 1), source="oc_de",
        )
        _merge_into(source=src, dest=dest, oc_code="OC1001")
        self.assertEqual(dest.logs.count(), 2)

    def test_log_dedup_by_date_user_type(self):
        dest = _cache("gc", "GC1002")
        src = _cache("oc", "OC1002")
        Log.objects.create(
            geocache=dest, log_type=LogType.FOUND, user_name="u1",
            logged_date=date(2023, 1, 1), source="gc",
        )
        # Identical (date, user, type) → not moved
        Log.objects.create(
            geocache=src, log_type=LogType.FOUND, user_name="u1",
            logged_date=date(2023, 1, 1), source="oc_de",
        )
        # Different user → moved
        Log.objects.create(
            geocache=src, log_type=LogType.FOUND, user_name="u2",
            logged_date=date(2023, 1, 1), source="oc_de",
        )
        _merge_into(source=src, dest=dest, oc_code="OC1002")
        self.assertEqual(dest.logs.count(), 2)
        self.assertEqual(set(dest.logs.values_list("user_name", flat=True)), {"u1", "u2"})


class MergeWaypoints(TestCase):
    def test_waypoints_moved_when_dest_empty(self):
        dest = _cache("gc", "GC2001")
        src = _cache("oc", "OC2001")
        Waypoint.objects.create(
            geocache=src, waypoint_type=WaypointType.PARKING,
            lookup="PK001", name="P1", latitude=48.1, longitude=9.1,
        )
        _merge_into(source=src, dest=dest, oc_code="OC2001")
        self.assertEqual(dest.waypoints.count(), 1)

    def test_waypoint_dedup_by_lookup(self):
        dest = _cache("gc", "GC2002")
        src = _cache("oc", "OC2002")
        Waypoint.objects.create(
            geocache=dest, waypoint_type=WaypointType.PARKING,
            lookup="PK002", name="dest-wp",
        )
        Waypoint.objects.create(
            geocache=src, waypoint_type=WaypointType.PARKING,
            lookup="PK002", name="src-wp",
        )
        Waypoint.objects.create(
            geocache=src, waypoint_type=WaypointType.STAGE,
            lookup="ST002", name="stage",
        )
        _merge_into(source=src, dest=dest, oc_code="OC2002")
        self.assertEqual(dest.waypoints.count(), 2)
        # The pre-existing dest waypoint is kept; the same-lookup src wp is
        # left orphaned and deleted with the source row.
        self.assertEqual(dest.waypoints.get(lookup="PK002").name, "dest-wp")


class MergeNotes(TestCase):
    def test_notes_moved_unconditionally(self):
        # _merge_into has no dedup on notes — both sides retained.
        dest = _cache("gc", "GC3001")
        src = _cache("oc", "OC3001")
        Note.objects.create(geocache=dest, note_type=NoteType.NOTE, body="dest note")
        Note.objects.create(geocache=src, note_type=NoteType.NOTE, body="src note 1")
        Note.objects.create(geocache=src, note_type=NoteType.NOTE, body="src note 2")
        _merge_into(source=src, dest=dest, oc_code="OC3001")
        bodies = set(dest.notes.values_list("body", flat=True))
        self.assertEqual(bodies, {"dest note", "src note 1", "src note 2"})


class MergeImages(TestCase):
    def test_images_dedup_by_url(self):
        dest = _cache("gc", "GC4001")
        src = _cache("oc", "OC4001")
        Image.objects.create(geocache=dest, url="https://x/dup.jpg", name="dest-dup")
        Image.objects.create(geocache=src, url="https://x/dup.jpg", name="src-dup")
        Image.objects.create(geocache=src, url="https://x/new.jpg", name="src-new")
        _merge_into(source=src, dest=dest, oc_code="OC4001")
        self.assertEqual(dest.images.count(), 2)
        self.assertEqual(dest.images.get(url="https://x/dup.jpg").name, "dest-dup")


class MergeTags(TestCase):
    def test_tag_union(self):
        dest = _cache("gc", "GC5001")
        src = _cache("oc", "OC5001")
        t1 = Tag.objects.create(name="alpha")
        t2 = Tag.objects.create(name="beta")
        t3 = Tag.objects.create(name="gamma")
        dest.tags.add(t1, t2)
        src.tags.add(t2, t3)
        _merge_into(source=src, dest=dest, oc_code="OC5001")
        self.assertEqual(set(dest.tags.values_list("name", flat=True)), {"alpha", "beta", "gamma"})


class MergeOCExtension(TestCase):
    def test_oc_extension_moved_when_present(self):
        dest = _cache("gc", "GC6001")
        src = _cache("oc", "OC6001")
        OCExtension.objects.create(geocache=src, rating=4.5, recommendations=3)
        _merge_into(source=src, dest=dest, oc_code="OC6001")
        dest.refresh_from_db()
        self.assertEqual(dest.oc_extension.rating, 4.5)
        self.assertEqual(dest.oc_extension.recommendations, 3)

    def test_no_oc_extension_silently_ok(self):
        dest = _cache("gc", "GC6002")
        src = _cache("oc", "OC6002")
        _merge_into(source=src, dest=dest, oc_code="OC6002")
        # No exception, dest still exists, source gone
        self.assertNotIn("oc_extension", dest._state.fields_cache)
        self.assertFalse(Geocache.objects.filter(oc_code="OC6002", gc_code="").exists())


class MergeSourceDeletion(TestCase):
    def test_source_row_deleted(self):
        dest = _cache("gc", "GC7001")
        src = _cache("oc", "OC7001")
        src_pk = src.pk
        _merge_into(source=src, dest=dest, oc_code="OC7001")
        self.assertFalse(Geocache.objects.filter(pk=src_pk).exists())
        self.assertTrue(Geocache.objects.filter(pk=dest.pk).exists())
