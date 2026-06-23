"""BRouter routing client — road-snapped routes from a BRouter server.

Online-first: the public ``brouter.de`` server is used by default. The base URL
(``settings.BROUTER_URL``) is the single swap point for a future self-hosted /
localhost BRouter instance — the same engine the BRouter Android app uses
offline — so nothing else needs to change to go offline later.

The route call runs server-side (not in the browser) to avoid cross-origin
restrictions and to keep the future localhost swap transparent to the frontend.
"""
from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request

from django.conf import settings

from ..geo import haversine_m

logger = logging.getLogger(__name__)

_UA = "GCForge/1.0 (geocache management; https://github.com/gcforge/gcforge)"
_TIMEOUT = 30  # seconds


class BRouterError(Exception):
    """Raised when the BRouter server cannot return a route."""


def _base_url() -> str:
    return getattr(settings, "BROUTER_URL", "https://brouter.de/brouter").rstrip("/")


def build_url(lonlats, profile: str, fmt: str) -> str:
    """Build a BRouter request URL. ``lonlats`` is an ordered list of (lon, lat)."""
    pts = "|".join(f"{float(lon):.6f},{float(lat):.6f}" for lon, lat in lonlats)
    query = urllib.parse.urlencode({
        "lonlats": pts,
        "profile": profile,
        "alternativeidx": 0,
        "format": fmt,
    })
    return f"{_base_url()}?{query}"


def fetch_route(lonlats, profile: str = "hiking-beta", fmt: str = "geojson") -> bytes:
    """Return the raw route body (GeoJSON or GPX bytes) from the BRouter server.

    ``lonlats`` is an ordered list of (lon, lat) tuples (>= 2). Raises
    :class:`BRouterError` on any failure (bad input, network, or no route).
    """
    if len(lonlats) < 2:
        raise BRouterError("at least two waypoints are required")

    url = build_url(lonlats, profile, fmt)
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            body = resp.read()
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", "replace")[:300]
        except Exception:
            pass
        logger.warning("BRouter HTTP %s: %s", exc.code, detail)
        raise BRouterError(detail or f"BRouter returned HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        logger.warning("BRouter request failed: %s", exc)
        raise BRouterError("could not reach the routing server") from exc

    # BRouter reports routing failures as HTTP 200 with a plain-text body
    # (e.g. "operation killed by thread-priority-watchdog" or "no route"),
    # so a GeoJSON request that doesn't start with '{' is an error message.
    if fmt == "geojson" and not body.lstrip().startswith(b"{"):
        raise BRouterError(body.decode("utf-8", "replace")[:300] or "no route found")
    return body


def _thin(path: list[list[float]], spacing_m: float, max_points: int) -> list[list[float]]:
    """Reduce a dense [[lon,lat],...] path to ~``spacing_m`` vertex spacing.

    Keeps the first and last vertex; drops intermediate points closer than
    ``spacing_m`` to the last kept one. If the result still exceeds
    ``max_points`` it is evenly subsampled. Keeps the corridor buffer/filter
    cheap instead of running over the thousands of vertices BRouter returns.
    """
    if len(path) <= 2:
        return path
    kept = [path[0]]
    for lon, lat in path[1:-1]:
        plon, plat = kept[-1]
        if haversine_m(plat, plon, lat, lon) >= spacing_m:
            kept.append([lon, lat])
    kept.append(path[-1])

    if len(kept) > max_points:
        step = len(kept) / max_points
        sub = [kept[int(i * step)] for i in range(max_points)]
        sub[-1] = kept[-1]
        kept = sub
    return kept


def route_summary(
    lonlats,
    profile: str = "hiking-beta",
    *,
    spacing_m: float = 200.0,
    max_points: int = 600,
) -> dict:
    """Fetch a GeoJSON route and return a compact summary for the map.

    Returns ``{"path": [[lon,lat],...], "distance_m", "duration_s", "ascend_m"}``
    where ``path`` is thinned (see :func:`_thin`) so it can drive the corridor
    buffer/filter without thousands of vertices.
    """
    data = json.loads(fetch_route(lonlats, profile, "geojson"))
    try:
        feat = data["features"][0]
        coords = feat["geometry"]["coordinates"]
        props = feat.get("properties", {})
    except (KeyError, IndexError, TypeError) as exc:
        raise BRouterError("unexpected routing response") from exc

    def _to_int(value):
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return None

    return {
        "path": _thin([[c[0], c[1]] for c in coords], spacing_m, max_points),
        "distance_m": _to_int(props.get("track-length")),
        "duration_s": _to_int(props.get("total-time")),
        "ascend_m": _to_int(props.get("filtered ascend")),
    }
