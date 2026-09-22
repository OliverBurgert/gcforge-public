"""
Pre-compute and cache distance/bearing from reference points to geocaches.

A full rebuild costs 6–13 s for 70,919 rows (measured 2026-09-11 against the
developer's 1.8 GB profile database), so it never runs inside a request.
Instead:

* every coordinate write goes through :mod:`geocaches.services.coords`, which
  calls :func:`update_for_cache` for the one cache that moved;
* :func:`ensure_cached` only *detects* missing rows and hands the work to the
  background task runner (visible in the task dock), returning ``False``;
* ``geocaches.filtering.query.annotate_distance`` coalesces the cached value with the
  per-row SQLite haversine callback, so a request that hits a partially filled
  cache still gets correct distances — just more slowly.

Rows for soft-deleted caches are kept (trash/restore must not trigger a
rebuild); the missing-row detection only looks at live caches.

See docs/architecture-review-2026-09.md §4.2 item 2.
"""

import math
import threading

from django.db import connection

from . import haversine_km
from ..models import DistanceCache, Geocache

BATCH_SIZE = 2000

_lock = threading.Lock()
_in_flight: set[int] = set()


def _bearing_deg(lat1, lon1, lat2, lon2):
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dlam = math.radians(lon2 - lon1)
    x = math.sin(dlam) * math.cos(phi2)
    y = math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(dlam)
    return (math.degrees(math.atan2(x, y)) + 360) % 360


# ---------------------------------------------------------------------------
# Coordinate sources — corrected coordinates always win over the listed ones
# ---------------------------------------------------------------------------

_COORDS_SELECT = (
    "COALESCE(cc.latitude, g.latitude), COALESCE(cc.longitude, g.longitude)"
)


def _tables():
    from geocaches.models import CorrectedCoordinates

    return (
        Geocache._meta.db_table,
        CorrectedCoordinates._meta.db_table,
        DistanceCache._meta.db_table,
    )


def _all_coords():
    """``(pk, lat, lon)`` for every cache with coordinates, trashed included."""
    g, cc, _dc = _tables()
    sql = (
        f"SELECT g.id, {_COORDS_SELECT} "
        f"FROM {g} g LEFT JOIN {cc} cc ON cc.geocache_id = g.id "
        f"WHERE g.latitude IS NOT NULL AND g.longitude IS NOT NULL"
    )
    with connection.cursor() as cur:
        cur.execute(sql)
        return cur.fetchall()


def _missing_coords(ref_point):
    """``(pk, lat, lon)`` for caches without a row for *ref_point*."""
    g, cc, dc = _tables()
    sql = (
        f"SELECT g.id, {_COORDS_SELECT} "
        f"FROM {g} g "
        f"LEFT JOIN {cc} cc ON cc.geocache_id = g.id "
        f"LEFT JOIN {dc} dc ON dc.geocache_id = g.id AND dc.ref_point_id = %s "
        f"WHERE g.latitude IS NOT NULL AND g.longitude IS NOT NULL AND dc.id IS NULL"
    )
    with connection.cursor() as cur:
        cur.execute(sql, [ref_point.pk])
        return cur.fetchall()


def has_missing_rows(ref_point):
    """True when at least one *live* cache has no row for *ref_point*.

    A LEFT JOIN anti-join with ``LIMIT 1`` — ~20 ms over 68 k caches when the
    cache is complete, immediate when it is empty.  Deliberately ignores
    soft-deleted caches so trashing or restoring one never looks like a gap.
    """
    g, _cc, dc = _tables()
    sql = (
        f"SELECT 1 FROM {g} g "
        f"LEFT JOIN {dc} dc ON dc.geocache_id = g.id AND dc.ref_point_id = %s "
        f"WHERE g.deleted_at IS NULL AND g.latitude IS NOT NULL "
        f"AND g.longitude IS NOT NULL AND dc.id IS NULL LIMIT 1"
    )
    with connection.cursor() as cur:
        cur.execute(sql, [ref_point.pk])
        return cur.fetchone() is not None


# ---------------------------------------------------------------------------
# Writing rows
# ---------------------------------------------------------------------------

def _rows_for(ref_point, chunk):
    ref_lat, ref_lon = ref_point.latitude, ref_point.longitude
    return [
        DistanceCache(
            geocache_id=pk,
            ref_point=ref_point,
            distance_km=haversine_km(ref_lat, ref_lon, lat, lon),
            bearing_deg=_bearing_deg(ref_lat, ref_lon, lat, lon),
        )
        for pk, lat, lon in chunk
    ]


def _fill(ref_point, rows, task_info=None):
    """bulk_create a DistanceCache row per ``(pk, lat, lon)`` in *rows*.

    One short transaction per batch rather than one long one over 70 k rows —
    see geocaches/db_lock.py.  Measured identical in wall time, and it keeps a
    background refill from blocking foreground writes for ten seconds.
    """
    from geocaches.db_lock import db_write_atomic

    if task_info is not None:
        task_info.total = len(rows)
        task_info.completed = 0
    done = 0
    for start in range(0, len(rows), BATCH_SIZE):
        chunk = rows[start:start + BATCH_SIZE]
        with db_write_atomic():
            DistanceCache.objects.bulk_create(
                _rows_for(ref_point, chunk),
                batch_size=BATCH_SIZE,
                ignore_conflicts=True,
            )
        done += len(chunk)
        if task_info is not None:
            task_info.completed = done
    return done


def recompute_distances(ref_point, task_info=None):
    """Delete and rebuild every row for *ref_point*.

    6–13 s for 70,919 rows on the developer's 1.8 GB profile database
    (measured 2026-09-11; the spread is disk, not algorithm).  Only for a
    reference point that moved — a gap after an import is filled by
    :func:`recompute_missing` instead.
    """
    from geocaches.db_lock import db_write_atomic

    with db_write_atomic():
        DistanceCache.objects.filter(ref_point=ref_point).delete()
    return _fill(ref_point, _all_coords(), task_info=task_info)


def recompute_missing(ref_point, task_info=None):
    """Add the rows *ref_point* is missing, leaving existing ones untouched."""
    return _fill(ref_point, _missing_coords(ref_point), task_info=task_info)


def update_for_cache(geocache_id):
    """Refresh one cache's rows for every reference point.

    Called from :mod:`geocaches.services.coords` after any write that moves a
    cache (listed coordinates or corrected ones).  Caches that have no rows at
    all are left alone — there is nothing stale to fix, and
    :func:`ensure_cached` will schedule the fill.
    """
    from geocaches.db_lock import db_write_atomic
    from preferences.models import ReferencePoint

    if not DistanceCache.objects.filter(geocache_id=geocache_id).exists():
        return 0
    g, cc, _dc = _tables()
    sql = (
        f"SELECT g.id, {_COORDS_SELECT} "
        f"FROM {g} g LEFT JOIN {cc} cc ON cc.geocache_id = g.id "
        f"WHERE g.id = %s AND g.latitude IS NOT NULL AND g.longitude IS NOT NULL"
    )
    with connection.cursor() as cur:
        cur.execute(sql, [geocache_id])
        rows = cur.fetchall()

    # An empty result means the coordinates were cleared: the rows just go.
    entries = [
        _rows_for(ref, rows)[0]
        for ref in ReferencePoint.objects.all()
    ] if rows else []
    with db_write_atomic():
        DistanceCache.objects.filter(geocache_id=geocache_id).delete()
        if entries:
            DistanceCache.objects.bulk_create(entries, ignore_conflicts=True)
    return len(entries)


# ---------------------------------------------------------------------------
# Background scheduling
# ---------------------------------------------------------------------------

def _recompute_task(ref_pks, task_info=None):
    from preferences.models import ReferencePoint

    written = 0
    try:
        for pk in ref_pks:
            ref = ReferencePoint.objects.filter(pk=pk).first()
            if ref is None:
                continue
            if task_info is not None:
                task_info.phase = ref.name
            written += recompute_missing(ref, task_info=task_info)
        return {"rows": written}
    finally:
        with _lock:
            _in_flight.difference_update(ref_pks)


def schedule_recompute(ref_point=None):
    """Fill missing rows in a background task; ``None`` means every point.

    Returns the task id, or ``None`` when a recompute covering the same
    reference points is already in flight (so a burst of requests cannot queue
    the same multi-second job over and over).
    """
    from preferences.models import ReferencePoint

    if ref_point is None:
        pks = list(ReferencePoint.objects.values_list("pk", flat=True))
        label = "Distances"
    else:
        pks = [ref_point.pk]
        label = f"Distances: {ref_point.name}"
    if not pks:
        return None

    with _lock:
        todo = [pk for pk in pks if pk not in _in_flight]
        if not todo:
            return None
        _in_flight.update(todo)

    from geocaches.tasks import submit_task
    try:
        return submit_task(label, _recompute_task, todo)
    except Exception:
        with _lock:
            _in_flight.difference_update(todo)
        raise


def ensure_cached(ref_point):
    """True when every live cache already has a row for *ref_point*.

    Never computes inline.  When rows are missing it schedules the background
    fill and returns False; the caller's query stays correct through
    ``annotate_distance``'s per-row haversine fallback.
    """
    if not has_missing_rows(ref_point):
        return True
    schedule_recompute(ref_point)
    return False


def invalidate(ref_point=None):
    """Delete cached distances.  If *ref_point* is None, delete all."""
    if ref_point:
        DistanceCache.objects.filter(ref_point=ref_point).delete()
    else:
        DistanceCache.objects.all().delete()


def reset_in_flight():
    """Forget which recomputes are running (test teardown only)."""
    with _lock:
        _in_flight.clear()
