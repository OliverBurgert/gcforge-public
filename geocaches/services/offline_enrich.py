"""Polygon-based location enrichment from downloaded boundary files.

Fills missing ``iso_country_code`` / ``country`` / ``state`` / ``county`` on
geocaches by point-in-polygon-testing each cache's coordinate against the
bundled world-country GeoJSON and any per-country region/county boundaries
the user has already downloaded.  No network calls.

By default only fills empty fields (the spatial result might disagree with a
good Nominatim answer near border lines because the polygons are
geometry-simplified).  Pass ``override=True`` to replace existing values too.
"""
from __future__ import annotations

import json
from pathlib import Path

from django.conf import settings as dj_settings
from django.db.models import Q

from geocaches.geo.countries import iso_to_name
from geocaches.models import Geocache
from preferences.services import boundaries
from preferences.services.boundaries import _point_in_geometry

WORLD_GEOJSON = Path(dj_settings.BASE_DIR) / "static" / "geo" / "world-countries.geojson"


def _feature_bbox(geom: dict) -> tuple[float, float, float, float]:
    """Return (minx, miny, maxx, maxy) for any GeoJSON polygon-y geometry."""
    minx, miny, maxx, maxy = 180.0, 90.0, -180.0, -90.0
    def scan(c):
        nonlocal minx, miny, maxx, maxy
        if not c:
            return
        if isinstance(c[0], (int, float)):
            if c[0] < minx:
                minx = c[0]
            if c[0] > maxx:
                maxx = c[0]
            if c[1] < miny:
                miny = c[1]
            if c[1] > maxy:
                maxy = c[1]
        else:
            for sub in c:
                scan(sub)
    scan((geom or {}).get("coordinates"))
    return minx, miny, maxx, maxy


def _index(features: list) -> list[tuple[tuple[float, float, float, float], dict]]:
    """Pair each feature with its precomputed bbox for cheap pre-filtering."""
    return [(_feature_bbox(f.get("geometry") or {}), f) for f in features]


def _find_containing(indexed, lon: float, lat: float):
    """First feature whose bbox contains (lon, lat) AND whose polygon contains
    it under the point-in-polygon ray cast.  None when nothing matches."""
    for (minx, miny, maxx, maxy), feat in indexed:
        if lon < minx or lon > maxx or lat < miny or lat > maxy:
            continue
        if _point_in_geometry(lon, lat, feat["geometry"]):
            return feat
    return None


def enrich_all(override: bool = False, progress=None, queryset=None) -> dict:
    """Run polygon-based enrichment across the given queryset (default: every
    cache with coordinates).

    Returns ``{scanned, countries_set, states_set, counties_set, no_country,
    no_region_data, no_county_data}``.  ``progress(i, total)`` is called every
    ~500 caches so the task runner can update its progress bar.
    """
    world = _index(json.loads(WORLD_GEOJSON.read_text(encoding="utf-8"))["features"])
    region_indexes: dict[str, list] = {}
    county_indexes: dict[str, list] = {}

    def region_index(iso2: str):
        if iso2 in region_indexes:
            return region_indexes[iso2]
        path = boundaries.boundary_path(iso2, boundaries.effective_level(iso2))
        if path and path.exists():
            gj = json.loads(path.read_text(encoding="utf-8"))
            region_indexes[iso2] = _index(gj.get("features", []))
        else:
            region_indexes[iso2] = []
        return region_indexes[iso2]

    def county_index(iso2: str):
        if iso2 in county_indexes:
            return county_indexes[iso2]
        path = boundaries.boundary_path(iso2, boundaries.effective_county_level(iso2))
        if path and path.exists():
            gj = json.loads(path.read_text(encoding="utf-8"))
            county_indexes[iso2] = _index(gj.get("features", []))
        else:
            county_indexes[iso2] = []
        return county_indexes[iso2]

    qs = queryset if queryset is not None else Geocache.objects.all()
    qs = qs.exclude(latitude=None).exclude(longitude=None)
    # Caches marked manual_location were edited by hand — never touch them.
    qs = qs.filter(manual_location=False)
    if not override:
        # Fill-only mode: skip rows where every field is already populated.
        qs = qs.filter(
            Q(iso_country_code="") | Q(state="") | Q(county="")
        )
    total = qs.count()
    stats = {
        "scanned": 0, "countries_set": 0, "states_set": 0, "counties_set": 0,
        "no_country": 0, "no_region_data": 0, "no_county_data": 0,
    }

    for i, cache in enumerate(qs.iterator(chunk_size=2000), start=1):
        lon = cache.longitude
        lat = cache.latitude
        if lon is None or lat is None:
            continue
        stats["scanned"] += 1
        changed: list[str] = []

        # Country
        if override or not cache.iso_country_code:
            feat = _find_containing(world, lon, lat)
            iso = (feat["properties"].get("iso_a2") if feat else "") or ""
            if iso:
                if cache.iso_country_code != iso:
                    cache.iso_country_code = iso
                    cache.country = iso_to_name(iso) or cache.country
                    changed += ["iso_country_code", "country"]
                    stats["countries_set"] += 1
            elif not cache.iso_country_code:
                stats["no_country"] += 1

        iso = cache.iso_country_code
        if iso:
            # State — point-in-state-polygon first.
            if override or not cache.state:
                ridx = region_index(iso)
                if not ridx:
                    stats["no_region_data"] += 1
                else:
                    feat = _find_containing(ridx, lon, lat)
                    new = (feat["properties"].get("name") if feat else "") or ""
                    if new and cache.state != new:
                        cache.state = new
                        changed.append("state")
                        stats["states_set"] += 1
            # County — also a backup source for state.  Counties sit at the
            # finer admin level so border points (a cache by the coast / on a
            # lake / in a tiny territorial sliver) hit a county polygon even
            # when the simplified state geometry doesn't cover them.  Every
            # county polygon already carries its parent_state from the
            # download-time spatial enrichment.
            if override or not cache.county or not cache.state:
                cidx = county_index(iso)
                if not cidx:
                    stats["no_county_data"] += 1
                else:
                    feat = _find_containing(cidx, lon, lat)
                    if feat:
                        props = feat["properties"]
                        new_county = props.get("name", "") or ""
                        if new_county and (override or not cache.county) \
                                and cache.county != new_county:
                            cache.county = new_county
                            changed.append("county")
                            stats["counties_set"] += 1
                        parent = props.get("parent_state", "") or ""
                        if parent and (override or not cache.state) \
                                and cache.state != parent:
                            cache.state = parent
                            if "state" not in changed:
                                changed.append("state")
                                stats["states_set"] += 1
                    # When even the county polygon misses the point (cache on
                    # an offshore island or in a lake outside the simplified
                    # geometries) but the cache *already* knows its county
                    # name from a prior Nominatim run, look that county up by
                    # name and grab its parent_state.  Only fires for an
                    # unambiguous name match so the US "Lincoln" trap can't
                    # mis-assign across 24 states.
                    elif cache.county and (override or not cache.state):
                        target = boundaries.normalize_name(cache.county, iso)
                        matches = [
                            f for _b, f in cidx
                            if boundaries.normalize_name(
                                f["properties"].get("name", ""), iso) == target
                        ]
                        # Accept the name-based fallback when every matching
                        # polygon points to the same state — handles the
                        # several countries (e.g. DE) that ship multiple
                        # "Stadt X" / "X" polygons within one Landkreis, while
                        # still skipping the US "24 Lincolns" trap.
                        parents = {
                            m["properties"].get("parent_state", "")
                            for m in matches
                            if m["properties"].get("parent_state")
                        }
                        if len(parents) == 1:
                            parent = next(iter(parents))
                            if cache.state != parent:
                                cache.state = parent
                                if "state" not in changed:
                                    changed.append("state")
                                    stats["states_set"] += 1

        if changed:
            cache.save(update_fields=changed)
        if progress and i % 500 == 0:
            progress(i, total)

    if progress:
        progress(total, total)
    return stats
