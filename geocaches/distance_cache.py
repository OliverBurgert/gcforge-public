"""
Pre-compute and cache distance/bearing from reference points to geocaches.

Pure-Python haversine runs ~1 s for 50 k caches — fast enough to compute
synchronously on first request or after an import.
"""

import math

from .models import DistanceCache, Geocache


# ---------------------------------------------------------------------------
# Pure-Python haversine / bearing (same formulas as settings.py)
# ---------------------------------------------------------------------------

def _haversine_km(lat1, lon1, lat2, lon2):
    R = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return 2 * R * math.asin(math.sqrt(min(a, 1.0)))


def _bearing_deg(lat1, lon1, lat2, lon2):
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dlam = math.radians(lon2 - lon1)
    x = math.sin(dlam) * math.cos(phi2)
    y = math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(dlam)
    return (math.degrees(math.atan2(x, y)) + 360) % 360


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def recompute_distances(ref_point):
    """Recompute DistanceCache for *ref_point* against all geocaches.

    Deletes existing entries for this ref point and bulk-creates new ones.
    Fast: ~1 s for 50 k caches on a modern machine.
    """
    DistanceCache.objects.filter(ref_point=ref_point).delete()

    ref_lat = ref_point.latitude
    ref_lon = ref_point.longitude

    from geocaches.models import CorrectedCoordinates

    caches = Geocache.objects.filter(
        latitude__isnull=False, longitude__isnull=False,
    ).values_list("pk", "latitude", "longitude")

    # Use corrected coords for distance when available
    corr_map = {}
    for gid, clat, clon in CorrectedCoordinates.objects.values_list(
        "geocache_id", "latitude", "longitude"
    ):
        corr_map[gid] = (clat, clon)

    entries = []
    for pk, lat, lon in caches:
        corr = corr_map.get(pk)
        if corr:
            lat, lon = corr
        entries.append(DistanceCache(
            geocache_id=pk,
            ref_point=ref_point,
            distance_km=_haversine_km(ref_lat, ref_lon, lat, lon),
            bearing_deg=_bearing_deg(ref_lat, ref_lon, lat, lon),
        ))

    DistanceCache.objects.bulk_create(entries, batch_size=2000, ignore_conflicts=True)


def ensure_cached(ref_point):
    """Ensure DistanceCache is populated for *ref_point*.

    Compares the cached row count against the geocache count.  If they
    differ (e.g. after an import or first-ever request), a full recompute
    is triggered.  This is intentionally simple — a full recompute is cheap.
    """
    cached = DistanceCache.objects.filter(ref_point=ref_point).count()
    total = Geocache.objects.filter(
        latitude__isnull=False, longitude__isnull=False,
    ).count()
    if cached != total:
        recompute_distances(ref_point)


def invalidate(ref_point=None):
    """Delete cached distances.  If *ref_point* is None, delete all."""
    if ref_point:
        DistanceCache.objects.filter(ref_point=ref_point).delete()
    else:
        DistanceCache.objects.all().delete()
