"""Geodesic helpers — haversine distance, point-in-polygon, corridor distance."""

import math

_R_M = 6_371_000.0   # Earth radius in metres
_R_KM = 6371.0       # Earth radius in kilometres


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Haversine distance in metres between two WGS-84 points."""
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    )
    return _R_M * 2 * math.asin(math.sqrt(min(a, 1.0)))


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Haversine distance in kilometres between two WGS-84 points."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return 2 * _R_KM * math.asin(math.sqrt(min(a, 1.0)))


def bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Initial great-circle bearing in degrees (0 = N, clockwise) from p1 to p2."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dlam = math.radians(lon2 - lon1)
    x = math.sin(dlam) * math.cos(phi2)
    y = math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(dlam)
    return (math.degrees(math.atan2(x, y)) + 360) % 360


def point_in_polygon(lat: float, lon: float, ring: list) -> bool:
    """Ray-casting point-in-polygon test.

    *ring* is a closed ``[[lng, lat], ...]`` ring (GeoJSON coordinate order).
    """
    inside = False
    n = len(ring) - 1
    j = n - 1
    for i in range(n):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        if ((yi > lat) != (yj > lat)) and lon < (xj - xi) * (lat - yi) / (yj - yi + 1e-15) + xi:
            inside = not inside
        j = i
    return inside


def dist_to_segment_km(
    lat: float, lon: float,
    lat1: float, lon1: float,
    lat2: float, lon2: float,
) -> float:
    """Minimum km distance from point to a line segment.

    Uses a flat-projection for the parametric *t* and haversine for the
    final distance — accurate enough for corridor widths up to ~50 km.
    """
    dx = lon2 - lon1
    dy = lat2 - lat1
    len2 = dx * dx + dy * dy
    if len2 < 1e-14:
        return haversine_km(lat, lon, lat1, lon1)
    t = max(0.0, min(1.0, ((lon - lon1) * dx + (lat - lat1) * dy) / len2))
    return haversine_km(lat, lon, lat1 + t * dy, lon1 + t * dx)
