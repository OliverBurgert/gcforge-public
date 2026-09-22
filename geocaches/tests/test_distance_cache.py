"""Tests for the pre-computed DistanceCache (geocaches/geo/distance_cache.py).

Two properties matter and used to be broken (docs/architecture-review-2026-09.md
§4.2 item 2): every write that moves a cache must refresh that cache's rows,
and no request may rebuild the whole table inline.
"""
from datetime import date
from unittest.mock import patch

from django.contrib.messages.storage.fallback import FallbackStorage
from django.test import RequestFactory, TestCase

from geocaches.geo import haversine_km
from geocaches.geo.distance_cache import (
    ensure_cached,
    recompute_distances,
    recompute_missing,
    schedule_recompute,
    update_for_cache,
)
from geocaches.models import (
    CacheSize, CacheStatus, CacheType, CorrectedCoordinates, DistanceCache, Geocache,
)
from geocaches.filtering.query import annotate_distance
from geocaches.services import save_geocache
from geocaches.services.coords import clear_corrected_coords, set_corrected_coords
from preferences.models import ReferencePoint

REF_LAT, REF_LON = 48.0, 9.0
MOVED_LAT, MOVED_LON = 49.0, 10.0


def _make_cache(code="GC00001", lat=48.1, lon=9.1, **kw):
    kw.setdefault("gc_code", code)
    return Geocache.objects.create(
        name="Test", cache_type=CacheType.TRADITIONAL, size=CacheSize.SMALL,
        status=CacheStatus.ACTIVE, latitude=lat, longitude=lon,
        difficulty=2.0, terrain=2.0, hidden_date=date(2020, 1, 1), owner="test",
        **kw,
    )


def _fields(lat=48.1, lon=9.1, **overrides):
    defaults = {
        "name": "Test Cache", "owner": "Owner",
        "cache_type": CacheType.TRADITIONAL, "size": CacheSize.SMALL,
        "status": CacheStatus.ACTIVE, "latitude": lat, "longitude": lon,
        "difficulty": 2.0, "terrain": 1.5, "hidden_date": date(2020, 1, 1),
    }
    defaults.update(overrides)
    return defaults


def _row(cache, ref):
    return DistanceCache.objects.get(geocache=cache, ref_point=ref)


def _expected(ref, lat, lon):
    return haversine_km(ref.latitude, ref.longitude, lat, lon)


class _StubClient:
    """Minimal BasePlatformClient stand-in for sync_caches()."""
    platform = "oc_de"
    batch_size = 50

    def __init__(self, payloads):
        self._payloads = payloads

    def get_caches(self, codes, mode, log_count=5):
        return self._payloads


class DistanceCacheBaseTest(TestCase):
    def setUp(self):
        self.ref = ReferencePoint.objects.create(
            name="Home", latitude=REF_LAT, longitude=REF_LON,
        )
        self.cache = _make_cache()
        recompute_distances(self.ref)


# ---------------------------------------------------------------------------
# Invalidation — one test per coordinate-write path
# ---------------------------------------------------------------------------

class CoordinateWritePathTest(DistanceCacheBaseTest):
    def test_corrected_coords_view_updates_rows(self):
        from geocaches.views.notes_logs import corrected_coords_save

        req = RequestFactory().post("/", {"latitude": "49.0", "longitude": "10.0"})
        req.session = "session"
        req._messages = FallbackStorage(req)
        corrected_coords_save(req, "GC00001")

        self.assertAlmostEqual(
            _row(self.cache, self.ref).distance_km,
            _expected(self.ref, MOVED_LAT, MOVED_LON), places=6,
        )

    def test_clearing_corrected_coords_restores_listed_position(self):
        set_corrected_coords(self.cache, MOVED_LAT, MOVED_LON)
        self.assertAlmostEqual(
            _row(self.cache, self.ref).distance_km,
            _expected(self.ref, MOVED_LAT, MOVED_LON), places=6,
        )
        clear_corrected_coords(self.cache)
        self.assertAlmostEqual(
            _row(self.cache, self.ref).distance_km,
            _expected(self.ref, 48.1, 9.1), places=6,
        )

    def test_save_geocache_coordinate_change_updates_rows(self):
        save_geocache(gc_code="GC00001", fields=_fields(lat=MOVED_LAT, lon=MOVED_LON))
        self.assertAlmostEqual(
            _row(self.cache, self.ref).distance_km,
            _expected(self.ref, MOVED_LAT, MOVED_LON), places=6,
        )

    def test_save_geocache_corrected_coords_update_rows(self):
        save_geocache(
            gc_code="GC00001", fields=_fields(),
            corrected_coords={"latitude": MOVED_LAT, "longitude": MOVED_LON},
        )
        self.assertAlmostEqual(
            _row(self.cache, self.ref).distance_km,
            _expected(self.ref, MOVED_LAT, MOVED_LON), places=6,
        )

    def test_unchanged_save_leaves_the_existing_row_alone(self):
        row_pk = _row(self.cache, self.ref).pk
        save_geocache(gc_code="GC00001", fields=_fields())
        self.assertEqual(_row(self.cache, self.ref).pk, row_pk)

    def test_api_sync_coordinate_change_updates_rows(self):
        from geocaches.sync.base import SyncMode
        from geocaches.sync.service import sync_caches

        cache = _make_cache(code="", oc_code="OC0001", lat=48.1, lon=9.1)
        client = _StubClient([{
            "oc_code": "OC0001",
            "fields": _fields(lat=MOVED_LAT, lon=MOVED_LON),
            "update_source": "oc",
        }])
        recompute_missing(self.ref)
        sync_caches(client, ["OC0001"], SyncMode.LIGHT)

        self.assertAlmostEqual(
            _row(cache, self.ref).distance_km,
            _expected(self.ref, MOVED_LAT, MOVED_LON), places=6,
        )

    def test_map_sync_coordinate_change_updates_rows(self):
        from geocaches.services.map_sync import run_sync_task

        cache = _make_cache(code="", oc_code="OC0002", lat=48.1, lon=9.1)
        client = _StubClient([{
            "oc_code": "OC0002",
            "fields": _fields(lat=MOVED_LAT, lon=MOVED_LON),
            "update_source": "oc",
        }])
        recompute_missing(self.ref)
        run_sync_task(client, ["OC0002"], None, 5)

        self.assertAlmostEqual(
            _row(cache, self.ref).distance_km,
            _expected(self.ref, MOVED_LAT, MOVED_LON), places=6,
        )

    def test_alc_save_coordinate_change_updates_rows(self):
        from geocaches.importers.gpx_gc import ImportStats
        from geocaches.services.save_alc import save_alc_stage

        stage_fields = {
            "name": "Stage 1", "owner": "Tester", "placed_by": "Tester",
            "cache_type": CacheType.LAB, "size": "Virtual", "status": "Active",
            "latitude": 48.1, "longitude": 9.1, "hidden_date": None,
            "long_description": "", "adventure": None, "stage_number": 1,
            "question_text": "", "al_stage_uuid": "stage-uuid-1",
        }
        save_alc_stage("LC0001-1", dict(stage_fields), [], ImportStats())
        cache = Geocache.objects.get(al_code="LC0001-1")
        recompute_missing(self.ref)

        moved = dict(stage_fields, latitude=MOVED_LAT, longitude=MOVED_LON)
        save_alc_stage("LC0001-1", moved, [], ImportStats())

        self.assertAlmostEqual(
            _row(cache, self.ref).distance_km,
            _expected(self.ref, MOVED_LAT, MOVED_LON), places=6,
        )

    def test_update_for_cache_covers_every_reference_point(self):
        other = ReferencePoint.objects.create(name="Away", latitude=50.0, longitude=11.0)
        recompute_missing(other)
        set_corrected_coords(self.cache, MOVED_LAT, MOVED_LON)
        self.assertAlmostEqual(
            _row(self.cache, other).distance_km,
            _expected(other, MOVED_LAT, MOVED_LON), places=6,
        )

    def test_cache_without_rows_is_not_given_partial_ones(self):
        fresh = _make_cache(code="GC00002")
        self.assertEqual(update_for_cache(fresh.pk), 0)
        self.assertFalse(DistanceCache.objects.filter(geocache=fresh).exists())


# ---------------------------------------------------------------------------
# ensure_cached() must never rebuild inline
# ---------------------------------------------------------------------------

class EnsureCachedTest(TestCase):
    def setUp(self):
        self.ref = ReferencePoint.objects.create(
            name="Home", latitude=REF_LAT, longitude=REF_LON,
        )
        self.cache = _make_cache()

    def test_missing_rows_are_handed_to_a_background_task(self):
        with patch("geocaches.tasks.submit_task", return_value="tid") as submit:
            self.assertFalse(ensure_cached(self.ref))
        self.assertEqual(submit.call_count, 1)
        self.assertTrue(submit.call_args[0][0].startswith("Distances"))
        # Nothing was computed inside the call itself.
        self.assertFalse(DistanceCache.objects.exists())

    def test_task_fills_the_rows_and_the_next_call_is_satisfied(self):
        self.assertFalse(ensure_cached(self.ref))  # runs inline under TASKS_RUN_SYNC
        self.assertTrue(DistanceCache.objects.filter(ref_point=self.ref).exists())
        self.assertTrue(ensure_cached(self.ref))

    def test_a_second_schedule_is_suppressed_while_one_is_in_flight(self):
        with patch("geocaches.tasks.submit_task", return_value="tid") as submit:
            self.assertIsNotNone(schedule_recompute(self.ref))
            self.assertIsNone(schedule_recompute(self.ref))
        self.assertEqual(submit.call_count, 1)

    def test_list_view_schedules_instead_of_rebuilding_inline(self):
        with patch("geocaches.tasks.submit_task", return_value="tid") as submit:
            resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(DistanceCache.objects.exists())
        self.assertTrue(
            any(c.args and str(c.args[0]).startswith("Distances")
                for c in submit.call_args_list)
        )

    def test_annotation_stays_correct_while_the_cache_is_incomplete(self):
        uncached = _make_cache(code="GC00002", lat=48.5, lon=9.5)
        recompute_missing(self.ref)
        DistanceCache.objects.filter(geocache=uncached).delete()

        rows = {
            g.gc_code: g.distance_km
            for g in annotate_distance(Geocache.objects.all(), self.ref)
        }
        self.assertAlmostEqual(rows["GC00001"], _expected(self.ref, 48.1, 9.1), places=6)
        self.assertAlmostEqual(rows["GC00002"], _expected(self.ref, 48.5, 9.5), places=6)


# ---------------------------------------------------------------------------
# recompute_missing()
# ---------------------------------------------------------------------------

class RecomputeMissingTest(DistanceCacheBaseTest):
    def test_only_fills_the_gaps(self):
        row_pk = _row(self.cache, self.ref).pk
        added = _make_cache(code="GC00002", lat=48.5, lon=9.5)

        self.assertEqual(recompute_missing(self.ref), 1)
        self.assertEqual(_row(self.cache, self.ref).pk, row_pk)
        self.assertAlmostEqual(
            _row(added, self.ref).distance_km,
            _expected(self.ref, 48.5, 9.5), places=6,
        )

    def test_uses_corrected_coordinates(self):
        added = _make_cache(code="GC00002", lat=48.5, lon=9.5)
        CorrectedCoordinates.objects.create(
            geocache=added, latitude=MOVED_LAT, longitude=MOVED_LON,
        )
        recompute_missing(self.ref)
        self.assertAlmostEqual(
            _row(added, self.ref).distance_km,
            _expected(self.ref, MOVED_LAT, MOVED_LON), places=6,
        )


# ---------------------------------------------------------------------------
# Trash / restore must not look like a gap
# ---------------------------------------------------------------------------

class TrashRestoreTest(DistanceCacheBaseTest):
    def test_trash_and_restore_do_not_trigger_a_recompute(self):
        from geocaches.services.trash import restore_cache, trash_cache

        with patch("geocaches.tasks.submit_task") as submit:
            trash_cache(self.cache)
            self.assertTrue(ensure_cached(self.ref))
            restore_cache(self.cache)
            self.assertTrue(ensure_cached(self.ref))
        submit.assert_not_called()

    def test_trashing_keeps_the_row(self):
        from geocaches.services.trash import trash_cache

        trash_cache(self.cache)
        self.assertTrue(DistanceCache.objects.filter(geocache=self.cache).exists())


# ---------------------------------------------------------------------------
# Reference-point saves
# ---------------------------------------------------------------------------

class ReferencePointTest(DistanceCacheBaseTest):
    def test_renaming_keeps_the_rows(self):
        row_pk = _row(self.cache, self.ref).pk
        self.ref.name = "Renamed"
        self.ref.save()
        self.assertEqual(_row(self.cache, self.ref).pk, row_pk)

    def test_moving_drops_the_rows(self):
        self.ref.latitude = MOVED_LAT
        self.ref.longitude = MOVED_LON
        self.ref.save()
        self.assertFalse(DistanceCache.objects.filter(ref_point=self.ref).exists())

    def test_edit_view_drops_the_rows_when_the_point_moves(self):
        from preferences.views.refpoints import edit_refpoint

        req = RequestFactory().post("/", {
            "rp_id": str(self.ref.pk), "rp_name": "Home",
            "rp_lat": "49.0", "rp_lon": "10.0",
        })
        edit_refpoint(req)
        self.assertFalse(DistanceCache.objects.filter(ref_point=self.ref).exists())

    def test_edit_view_keeps_the_rows_on_a_rename(self):
        row_pk = _row(self.cache, self.ref).pk
        from preferences.views.refpoints import edit_refpoint

        req = RequestFactory().post("/", {
            "rp_id": str(self.ref.pk), "rp_name": "Renamed",
            "rp_lat": str(REF_LAT), "rp_lon": str(REF_LON),
        })
        edit_refpoint(req)
        self.assertEqual(_row(self.cache, self.ref).pk, row_pk)
