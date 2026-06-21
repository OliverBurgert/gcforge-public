"""On-demand administrative-boundary downloads for the dashboard Maps tab.

Country-region (ADM1) and county (ADM2) polygons are fetched from geoBoundaries
(gbOpen, CC-BY 4.0) the first time the user enables that map, then stored locally
under ``<db_dir>/boundaries`` so rendering stays offline afterwards.  Polygons
join to the find set by name (``shapeName`` → cache ``state`` / ``county``),
compared after light normalisation.
"""
from __future__ import annotations

import json
import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

import pycountry
import requests

from ..models import UserPreference

GEOBOUNDARIES_API = "https://www.geoboundaries.org/api/current/gbOpen/{iso3}/{level}/"
MANIFEST_KEY = "dashboard_boundaries"
LEVELS = ("ADM1", "ADM2", "ADM3")

# Region flags are fetched on demand and cached locally so the dashboard works
# offline after the first boundary download.  flagcdn.com is tried first
# (fast, no attribution) but only covers US states + UK constituents; for
# everything else we fall back to a PNG thumbnail of the subdivision's
# Wikidata-linked Commons flag.  Subdivisions with no flag are silently skipped.
FLAGCDN_URL = "https://flagcdn.com/h20/{code}.png"
FLAGS_DIRNAME = "flags"
WIKIDATA_SPARQL_URL = "https://query.wikidata.org/sparql"
_HTTP_UA = "GCForge/0.2 (boundary + flag fetch)"

# Countries whose region-level polygons sit at an unexpected geoBoundaries
# admin level.  Italy's gbOpen ADM1 is the 5 NUTS-1 macro-regions; the 20 real
# ``regioni`` live in ADM2.  Looked up via ``effective_level``.
_REGION_LEVEL_OVERRIDE: dict[str, str] = {
    "IT": "ADM2",
}
# Same idea, for the county-equivalent (second-tier) subdivision.  gbOpen DE
# ADM2 is the 38 Regierungsbezirke; the 401 Landkreise live in ADM3.  IT's
# regioni are already at ADM2, so its provincie are at ADM3.
_COUNTY_LEVEL_OVERRIDE: dict[str, str] = {
    "DE": "ADM3",
    "IT": "ADM3",
}


def effective_level(iso2: str) -> str:
    """geoBoundaries admin level for the country's first-tier subdivision
    (states / Bundesländer / regioni)."""
    return _REGION_LEVEL_OVERRIDE.get((iso2 or "").upper(), "ADM1")


def effective_county_level(iso2: str) -> str:
    """geoBoundaries admin level for the country's second-tier subdivision
    (counties / Landkreise / provincie)."""
    return _COUNTY_LEVEL_OVERRIDE.get((iso2 or "").upper(), "ADM2")


def iso3_for(iso2: str) -> str | None:
    if not iso2:
        return None
    country = pycountry.countries.get(alpha_2=iso2.upper())
    return country.alpha_3 if country else None


def boundaries_dir() -> Path:
    from django.conf import settings as dj_settings
    db_path = Path(dj_settings.DATABASES["default"]["NAME"])
    return db_path.parent / "boundaries"


def boundary_path(iso2: str, level: str | None = None) -> Path | None:
    iso3 = iso3_for(iso2)
    if not iso3:
        return None
    return boundaries_dir() / f"{iso3}_{level or effective_level(iso2)}.geojson"


def is_downloaded(iso2: str, level: str | None = None) -> bool:
    path = boundary_path(iso2, level)
    return bool(path and path.exists())


def flag_path(iso_3166_2: str) -> Path:
    """Local cache path for one ISO 3166-2 region's flag PNG."""
    return boundaries_dir() / FLAGS_DIRNAME / f"{iso_3166_2.lower()}.png"


def flag_exists(iso_3166_2: str) -> bool:
    return bool(iso_3166_2) and flag_path(iso_3166_2).exists()


def _wikidata_flag_urls(iso2: str) -> dict[str, str]:
    """Return ``{ISO_3166_2 (upper): Commons Special:FilePath URL}`` for every
    subdivision of *iso2* that has a flag image on Wikidata (P41).  One SPARQL
    call per country; empty dict on any error so the rest of the download still
    succeeds."""
    iso2 = (iso2 or "").upper()
    if len(iso2) != 2:
        return {}
    query = (
        'SELECT ?code ?flag WHERE { '
        f'?item wdt:P300 ?code. FILTER(STRSTARTS(?code, "{iso2}-")). '
        '?item wdt:P41 ?flag. }'
    )
    try:
        resp = requests.get(
            WIKIDATA_SPARQL_URL,
            params={"query": query, "format": "json"},
            headers={"User-Agent": _HTTP_UA, "Accept": "application/sparql-results+json"},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
    except (requests.RequestException, ValueError):
        return {}
    out: dict[str, str] = {}
    for binding in data.get("results", {}).get("bindings", []):
        code = (binding.get("code", {}).get("value") or "").upper()
        flag = binding.get("flag", {}).get("value") or ""
        if code and flag and code not in out:
            out[code] = flag
    return out


def _resolve_iso_3166_2(iso2: str, name: str) -> str:
    """Find an ISO 3166-2 code for *name* within *iso2* via pycountry.

    Useful where geoBoundaries leaves ``shapeISO`` blank (e.g. its ITA ADM2
    dataset).  Uses the aggressive ``normalize_name`` join on both sides.
    """
    if not name or not iso2:
        return ""
    try:
        subs = list(pycountry.subdivisions.get(country_code=iso2.upper()) or [])
    except (KeyError, LookupError):
        return ""
    target = normalize_name(name, iso2)
    for sub in subs:
        if normalize_name(sub.name or "", iso2) == target:
            return (sub.code or "")
    return ""


def _try_cache_flag(iso_3166_2: str, wikidata_url: str | None = None) -> bool:
    """Best-effort fetch of one region flag, cached as ``<code>.png``.

    Sources, tried in order:
      1. flagcdn.com (covers US states + UK constituents; small + fast).
      2. Wikidata-linked Commons flag, requested as a 40px-wide PNG thumbnail
         via Special:FilePath.

    Returns True if a flag is now on disk for *iso_3166_2*.
    """
    if not iso_3166_2:
        return False
    path = flag_path(iso_3166_2)
    if path.exists():
        return True
    urls = [FLAGCDN_URL.format(code=iso_3166_2.lower())]
    if wikidata_url:
        sep = "&" if "?" in wikidata_url else "?"
        urls.append(wikidata_url + sep + "width=40")
    for url in urls:
        try:
            resp = requests.get(url, headers={"User-Agent": _HTTP_UA}, timeout=15)
        except requests.RequestException:
            continue
        if resp.status_code != 200 or not resp.content:
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(resp.content)
        return True
    return False


# Common admin-1 suffix words to strip after basic normalisation (lowercase +
# diacritic-free).  Longer suffixes are listed first so "s lan" wins over " lan".
_ADMIN1_SUFFIXES = (
    "s lan",                  # Swedish genitive: "Stockholms län"
    " lan",                   # Swedish: "län"
    " prefecture",            # Japanese: some JP polygons carry it, DB doesn't
    " region",
    " regio",
    " county",
    " province",
    " provincia",
    " departement",
    " departamento",
    " distrito",
    " sysla",                 # Icelandic: "sýsla"
    ", stadtkreis",           # German kreisfreie Städte at ADM3
    ", kreisfreie stadt",
    ", landkreis",
)
_ADMIN1_PREFIXES = (
    "islas ",                 # Spanish "Islands of"
    "county of ",
    "principality of ",
    "stadt ",                 # German "Stadt Leipzig" / "Stadt München"
    "landkreis ",             # German Landkreis (already stripped at enrichment
                              # most of the time, kept here as a safety net)
    "kreis ",                 # German "Kreis X" (NRW / SH / Hessen use "Kreis"
                              # rather than "Landkreis"); safe — never matches a
                              # compound "X-Kreis" or "Kreisfreie …".
)

# Per-country alias map for cases where the polygon and Nominatim conventions
# use different languages — most notably Iceland, where gbOpen polygons are
# English ("Eastern Region") and Nominatim returns Icelandic ("Austurland").
# Both halves of each pair map to the same canonical key so the join lands.
# Applied BEFORE suffix stripping so the "region" suffix doesn't collapse all
# five English IS variants into a single key.
_REGION_ALIASES: dict[str, dict[str, str]] = {
    # United Kingdom — gbOpen ADM1 is the 4 constituent countries.  Nominatim
    # sometimes returns a finer area as ``state`` (notably "London" inside
    # England, or "South Wales" inside Wales); roll those up to the parent
    # so they land somewhere instead of "unmapped".
    "GB": {
        "london":         "england",
        "greater london": "england",
        "south wales":    "wales",
        "north wales":    "wales",
        "mid wales":      "wales",
        "west wales":     "wales",
    },
    "IS": {
        "capital region":       "hofudborgarsvaedi",
        "hofudborgarsvaedi":    "hofudborgarsvaedi",
        "southern peninsula":   "sudurnes",
        "sudurnes":             "sudurnes",
        "western region":       "vesturland",
        "vesturland":           "vesturland",
        "westfjords":           "vestfirdir",
        "vestfirdir":           "vestfirdir",
        "northwestern region":  "nordurland vestra",
        "nordurland vestra":    "nordurland vestra",
        "northeastern region":  "nordurland eystra",
        "nordurland eystra":    "nordurland eystra",
        "eastern region":       "austurland",
        "austurland":           "austurland",
        "southern region":      "sudurland",
        "sudurland":            "sudurland",
    },
}

# Per-country alias map for second-tier (county) names where the geoBoundaries
# ``shapeName`` and the stored Nominatim/imported value abbreviate the same
# Landkreis differently.  Both spellings map to one canonical key so the join
# lands.  Applied (alongside ``_REGION_ALIASES``) AFTER parenthetical stripping
# but BEFORE suffix/prefix stripping.  Keys are already normalised (lowercase,
# diacritic-free, hyphens→spaces).
_COUNTY_ALIASES: dict[str, dict[str, str]] = {
    "DE": {
        # geoBoundaries spells it out; Nominatim/imports abbreviate "Sächsische".
        "sachs. schweiz osterzgebirge": "sachsische schweiz osterzgebirge",
        # geoBoundaries abbreviates "im" to "i."; the stored value spells it out.
        "wunsiedel i. fichtelgebirge": "wunsiedel im fichtelgebirge",
    },
}

# Multi-county city-states: their finds need rolling onto one polygon, but —
# unlike Berlin/Hamburg/Washington DC — the state has more than one county
# polygon, so it can't be auto-detected as single-county (see the rollup in
# ``_tier_map_data``).  Bremen is the city of Bremen + Bremerhaven; finds under
# either are folded onto the polygon whose name matches the state.  Single-
# county states need NO entry here.  Keys are normalised state names.
_CITY_STATES: dict[str, set[str]] = {
    "DE": {"bremen"},
}

# Standalone-letter substitutions Nominatim and many gazetteers apply when
# transliterating Nordic / German names ("Höfuðborgarsvæði" → "Höfudborgarsvaedi"
# in Nominatim).  Combining diacritics are stripped separately via NFKD.
_LETTER_SUBSTS = (("ð", "d"), ("þ", "th"), ("æ", "ae"), ("ø", "o"), ("ß", "ss"))


def normalize_name(value: str, iso2: str = "") -> str:
    """Aggressively normalise an admin-1 name into a key suitable for joining
    a geoBoundaries ``shapeName`` to a Nominatim ``state`` value.

    Steps (in order):
      1. Take the first half of a bilingual alias — slash-separated
         ("Cataluña/Catalunya") or " - "-separated (the Lusatian Sorbian dual
         names Nominatim returns, e.g. "Bautzen - Budyšin" → "Bautzen").
      2. NFKD decompose + strip combining diacritics + casefold.
      3. Replace hyphens / en-dashes / em-dashes with spaces, then substitute
         standalone letters (ð→d, þ→th, æ→ae, ø→o, ß→ss).
      4. Collapse whitespace.
      5. Apply the country-specific alias maps (region: IS English↔Icelandic;
         county: DE Landkreis abbreviations).
      6. Strip a common admin-1 suffix (" län", " region", …).
      7. Strip a common admin-1 prefix ("islas ", …).
      8. Strip a trailing parenthetical disambiguator ("Friesland (DE)" →
         "Friesland", "Leer (Ostfriesland)" → "Leer").

    Applied to BOTH sides of the join.  ``iso2`` is optional; without it the
    alias map is skipped but suffix/prefix stripping still runs.
    """
    if not value:
        return ""
    # Split on "/" or a spaced " - " (bilingual separator) — never a compound
    # hyphen like "Mayen-Koblenz", which has no surrounding spaces.
    first = re.split(r"\s*/\s*|\s+-\s+", value, maxsplit=1)[0].strip()
    decomposed = unicodedata.normalize("NFKD", first)
    no_diacritics = "".join(c for c in decomposed if not unicodedata.combining(c))
    n = no_diacritics.casefold()
    for ch in ("-", "–", "—"):
        n = n.replace(ch, " ")
    for old, new in _LETTER_SUBSTS:
        n = n.replace(old, new)
    n = " ".join(n.split())
    upper = iso2.upper()
    aliased = (_REGION_ALIASES.get(upper, {}).get(n)
               or _COUNTY_ALIASES.get(upper, {}).get(n))
    if aliased:
        return aliased
    for suffix in _ADMIN1_SUFFIXES:
        if n.endswith(suffix):
            n = n[: -len(suffix)].rstrip()
            break
    for prefix in _ADMIN1_PREFIXES:
        if n.startswith(prefix):
            n = n[len(prefix):].lstrip()
            break
    # Drop a trailing parenthetical disambiguator last, so it also clears one
    # that sat in front of an already-stripped suffix ("Halle (Saale),
    # Kreisfreie Stadt" → "halle", matching the bare "Halle (Saale)").
    n = re.sub(r"\s*\([^)]*\)\s*$", "", n).strip()
    return n


def _manifest() -> dict:
    return UserPreference.get(MANIFEST_KEY, {}) or {}


def _key(iso2: str, level: str) -> str:
    return f"{iso2.upper()}_{level}"


def download_boundary(iso2: str, level: str | None = None) -> int:
    """Download + store one country's boundary GeoJSON. Returns feature count.

    Slims each feature to a single ``name`` property (the geoBoundaries
    ``shapeName``) and records a manifest entry for the settings UI.  When
    ``level`` is omitted the per-country effective level is used (Italy → ADM2,
    everyone else → ADM1).
    """
    iso3 = iso3_for(iso2)
    if not iso3:
        raise ValueError(f"Unknown country code: {iso2!r}")
    level = level or effective_level(iso2)
    if level not in LEVELS:
        raise ValueError(f"Unknown level: {level!r}")

    meta = requests.get(
        GEOBOUNDARIES_API.format(iso3=iso3, level=level), timeout=30
    )
    meta.raise_for_status()
    meta = meta.json()
    gj_url = meta.get("simplifiedGeometryGeoJSON") or meta.get("gjDownloadURL")
    if not gj_url:
        raise RuntimeError(f"geoBoundaries has no download URL for {iso3}/{level}")

    resp = requests.get(gj_url, timeout=180)
    resp.raise_for_status()
    gj = resp.json()

    feats = []
    for raw in gj.get("features", []):
        props = raw.get("properties", {})
        feats.append({
            "type": "Feature",
            "properties": {
                "name": props.get("shapeName") or "",
                "iso_3166_2": props.get("shapeISO") or "",
            },
            "geometry": raw.get("geometry"),
        })
    # Backfill ISO 3166-2 from pycountry where geoBoundaries left it blank
    # (notably ITA ADM2) — needed for the flag lookup AND for any future
    # code-based features.
    for f in feats:
        if not f["properties"]["iso_3166_2"]:
            f["properties"]["iso_3166_2"] = _resolve_iso_3166_2(
                iso2, f["properties"]["name"]
            )
    out = boundary_path(iso2, level)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps({"type": "FeatureCollection", "features": feats},
                   separators=(",", ":"), ensure_ascii=False),
        encoding="utf-8",
    )
    # Best-effort: pull region flags too, while we still have a network up.
    # One SPARQL call per country gets the Commons URLs in bulk.
    wikidata_flags = _wikidata_flag_urls(iso2)
    for f in feats:
        code = f["properties"]["iso_3166_2"]
        _try_cache_flag(code, wikidata_flags.get(code.upper()))

    manifest = _manifest()
    new_key = _key(iso2, level)
    # A country can legitimately have both a region entry and a county entry —
    # only drop entries whose level is neither the current region nor county
    # effective level (e.g. an old IT_ADM1 from before IT's region override
    # pointed to ADM2).
    valid_levels = {effective_level(iso2), effective_county_level(iso2)}
    for old_key in list(manifest):
        if not old_key.startswith(f"{iso2.upper()}_") or old_key == new_key:
            continue
        old_level = old_key.split("_", 1)[1] if "_" in old_key else ""
        if old_level not in valid_levels:
            manifest.pop(old_key, None)
    manifest[new_key] = {
        "iso2": iso2.upper(),
        "level": level,
        "count": len(feats),
        "downloaded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "license": meta.get("boundaryLicense", ""),
        "source": meta.get("boundarySource", "geoBoundaries"),
    }
    UserPreference.set(MANIFEST_KEY, manifest)
    # When the county tier was the one just downloaded, also tag each county
    # with its parent state so the drill-down view can slice the data.  Cheap
    # one-shot computation; skipped silently if the region file isn't there.
    if level == effective_county_level(iso2) and level != effective_level(iso2):
        try:
            _enrich_with_parent_state(iso2)
        except (OSError, ValueError):
            pass
    return len(feats)


def update_all() -> dict:
    """Re-download every boundary already in the manifest, using the current
    effective level (auto-corrects countries whose override moved them to a
    different admin level since the last download). Returns {key: count|error}.
    """
    results = {}
    # De-duplicate by ISO so we don't redownload the same country twice when a
    # stale entry exists at a different level.
    seen_iso = set()
    for info in list(_manifest().values()):
        iso2 = info["iso2"]
        if iso2 in seen_iso:
            continue
        seen_iso.add(iso2)
        key = _key(iso2, effective_level(iso2))
        try:
            results[key] = download_boundary(iso2)
        except (requests.RequestException, ValueError, RuntimeError, OSError) as exc:
            results[key] = f"error: {exc}"
    return results


def status() -> dict:
    """Manifest keyed by ``ISO2_LEVEL`` for the settings UI."""
    return _manifest()


def _point_in_ring(x: float, y: float, ring: list) -> bool:
    """Ray-cast point-in-polygon for a single ring (list of [x,y] points)."""
    inside = False
    n = len(ring)
    j = n - 1
    for i in range(n):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi + 1e-12) + xi:
            inside = not inside
        j = i
    return inside


def _point_in_polygon(x: float, y: float, polygon_coords: list) -> bool:
    """GeoJSON Polygon coordinates: outer ring first, then any holes."""
    if not polygon_coords or not _point_in_ring(x, y, polygon_coords[0]):
        return False
    for hole in polygon_coords[1:]:
        if _point_in_ring(x, y, hole):
            return False
    return True


def _point_in_geometry(x: float, y: float, geom: dict) -> bool:
    t = (geom or {}).get("type")
    if t == "Polygon":
        return _point_in_polygon(x, y, geom["coordinates"])
    if t == "MultiPolygon":
        return any(_point_in_polygon(x, y, poly) for poly in geom["coordinates"])
    return False


def _polygon_centroid(coords: list) -> tuple[float, float]:
    """Area-weighted centroid of a polygon's outer ring (GeoJSON shape)."""
    outer = coords[0]
    n = len(outer)
    if n < 3:
        return outer[0][0], outer[0][1]
    A = 0.0
    cx = 0.0
    cy = 0.0
    for i in range(n):
        x0, y0 = outer[i]
        x1, y1 = outer[(i + 1) % n]
        cross = x0 * y1 - x1 * y0
        A += cross
        cx += (x0 + x1) * cross
        cy += (y0 + y1) * cross
    A *= 0.5
    if A == 0:
        return outer[0][0], outer[0][1]
    return cx / (6 * A), cy / (6 * A)


def _geometry_centroid(geom: dict) -> tuple[float, float]:
    """Centroid of a GeoJSON geometry; for MultiPolygon use the largest part."""
    t = (geom or {}).get("type")
    if t == "Polygon":
        return _polygon_centroid(geom["coordinates"])
    if t == "MultiPolygon":
        # Pick the polygon with the largest absolute signed area.
        best = None
        best_area = -1.0
        for poly in geom["coordinates"]:
            outer = poly[0]
            a = 0.0
            n = len(outer)
            for i in range(n):
                x0, y0 = outer[i]
                x1, y1 = outer[(i + 1) % n]
                a += x0 * y1 - x1 * y0
            a = abs(a) * 0.5
            if a > best_area:
                best_area = a
                best = poly
        if best is not None:
            return _polygon_centroid(best)
    return 0.0, 0.0


def _enrich_with_parent_state(iso2: str) -> None:
    """Re-write the downloaded county boundary so each feature carries its
    parent state's ``name`` and ``iso_3166_2`` code (when available).

    The drill-down "counties grouped by state" view on each country's sub-tab
    reads these to slice the county dataset.  No-op when either boundary file
    is missing.
    """
    iso2 = iso2.upper()
    region_path = boundary_path(iso2, effective_level(iso2))
    county_path = boundary_path(iso2, effective_county_level(iso2))
    if not (region_path and region_path.exists()
            and county_path and county_path.exists()):
        return
    region = json.loads(region_path.read_text(encoding="utf-8"))
    county = json.loads(county_path.read_text(encoding="utf-8"))
    states = [
        (
            f["properties"].get("name", ""),
            f["properties"].get("iso_3166_2", ""),
            f["geometry"],
        )
        for f in region.get("features", [])
        if f.get("geometry")
    ]
    # Pre-compute each state's centroid for the nearest-state fallback below.
    state_centroids = [
        (name, code, sgeom, _geometry_centroid(sgeom)) for name, code, sgeom in states
    ]
    for feat in county.get("features", []):
        geom = feat.get("geometry") or {}
        cx, cy = _geometry_centroid(geom)
        match_name = ""
        match_code = ""
        for name, code, sgeom, _scent in state_centroids:
            if _point_in_geometry(cx, cy, sgeom):
                match_name = name
                match_code = code
                break
        # Fallback: when no state polygon contains the centroid (typically a
        # border county whose centroid falls in a lake the simplified state
        # geometry doesn't cover — Konstanz on the Bodensee is the canonical
        # case), pick the state whose centroid is nearest.
        if not match_name and state_centroids:
            best = min(state_centroids,
                       key=lambda s: (s[3][0] - cx) ** 2 + (s[3][1] - cy) ** 2)
            match_name, match_code = best[0], best[1]
        feat["properties"]["parent_state"] = match_name
        feat["properties"]["parent_state_iso_3166_2"] = match_code
    county_path.write_text(
        json.dumps(county, separators=(",", ":"), ensure_ascii=False),
        encoding="utf-8",
    )


def _union_geometry(a: dict | None, b: dict | None) -> dict:
    """Combine two GeoJSON Polygon/MultiPolygon geometries into one
    MultiPolygon — used to fold geoBoundaries' duplicate same-name features."""
    def polys(g):
        if not g:
            return []
        if g.get("type") == "Polygon":
            return [g["coordinates"]]
        if g.get("type") == "MultiPolygon":
            return list(g["coordinates"])
        return []
    return {"type": "MultiPolygon", "coordinates": polys(a) + polys(b)}


def _merge_duplicate_features(features: list) -> list:
    """geoBoundaries occasionally emits several features for one area (a split
    multipolygon, same ``parent_state``/``name``).  Fold them into a single
    feature so each area is counted, coloured and listed exactly once."""
    merged: dict = {}
    order: list = []
    for feat in features:
        props = feat.get("properties", {})
        ident = (props.get("parent_state", ""), props.get("name", ""))
        if ident in merged:
            kept = merged[ident]
            kept["geometry"] = _union_geometry(kept.get("geometry"),
                                               feat.get("geometry"))
        else:
            merged[ident] = feat
            order.append(ident)
    return [merged[i] for i in order]


def _norm_key(k, iso2: str):
    """Normalise a key — either a single name string or a tuple of names."""
    if isinstance(k, tuple):
        return tuple(normalize_name(p or "", iso2) for p in k)
    return normalize_name(k or "", iso2)


def _tier_map_data(iso2: str, level: str, count_fn, key_fn, all_fn) -> dict | None:
    """Shared body for ``region_map_data`` and ``county_map_data``.

    Loads the stored boundary GeoJSON at *level*, joins each feature to the
    find counts returned by ``count_fn(iso2)`` via ``key_fn`` — both produce
    keys (string or tuple) that are normalised the same way, so the lookup
    works whether the join is on bare name (regions) or ``(state, county)``
    composite (counties, to disambiguate name collisions across states).
    Decorates each feature with a flag URL when one is cached, and adds
    ``meta = {total, unmatched}``.  ``all_fn(iso2)`` returns the same keys over
    *all* caches (not just finds) so each feature carries the stored values
    needed to filter the list view — even where there are no finds yet.
    """
    path = boundary_path(iso2, level)
    if not (path and path.exists()):
        return None

    gj = json.loads(path.read_text(encoding="utf-8"))
    features = _merge_duplicate_features(gj.get("features", []))
    gj["features"] = features
    raw_counts = count_fn(iso2)
    norm_counts: dict = {}
    for k, count in raw_counts.items():
        nk = _norm_key(k, iso2)
        norm_counts[nk] = norm_counts.get(nk, 0) + count

    # Normalised key for every polygon, grouped by parent state so we can find
    # each state's single "whole-state" county polygon for the rollup below.
    city_states = _CITY_STATES.get(iso2.upper(), set())
    poly_keys = set()
    state_polys: dict = {}  # state-name key -> set of its polygon keys
    for feat in features:
        nk = _norm_key(key_fn(feat["properties"]), iso2)
        poly_keys.add(nk)
        if isinstance(nk, tuple) and len(nk) == 2 and nk[0]:
            state_polys.setdefault(nk[0], set()).add(nk)

    # A state's single rollup target.  A state with exactly ONE county polygon
    # is unambiguously single-county (Berlin, Hamburg, Washington DC, …) — any
    # find in it lies in that county, so rolling up is always safe and needs no
    # curation.  Multi-county city-states can't be detected that way (Bremen =
    # Bremen + Bremerhaven), so they're listed in ``_CITY_STATES`` and roll up
    # onto the polygon whose name matches the state.
    whole_state: dict = {}  # state-name key -> that state's single polygon key
    for sk, polys in state_polys.items():
        if len(polys) == 1:
            whole_state[sk] = next(iter(polys))
        elif sk in city_states:
            for pk in polys:
                if pk[1] == sk:
                    whole_state[sk] = pk
                    break

    # City-state rollup: Nominatim files a find under the Bezirk (e.g.
    # "Berlin Mitte") but the boundary set has only the whole city.  Redirect
    # any unmatched composite-key find onto its state's single polygon.
    if whole_state:
        for k in list(norm_counts):
            if (isinstance(k, tuple) and k not in poly_keys
                    and k[0] in whole_state and k != whole_state[k[0]]):
                tgt = whole_state[k[0]]
                norm_counts[tgt] = norm_counts.get(tgt, 0) + norm_counts.pop(k)

    # Original (state, county) keys behind each normalised key, so a region can
    # link to the list view filtered to exactly its caches — the stored values
    # differ from the polygon's geoBoundaries name.  Built over ALL caches (not
    # just finds) so a find-less region can still be filtered for trip planning.
    key_to_originals: dict = {}
    for k in all_fn(iso2):
        nk = _norm_key(k, iso2)
        if (isinstance(nk, tuple) and nk not in poly_keys
                and nk[0] in whole_state):
            nk = whole_state[nk[0]]
        key_to_originals.setdefault(nk, []).append(
            list(k) if isinstance(k, tuple) else [k, ""])

    matched_keys = set()
    for feat in features:
        nk = _norm_key(key_fn(feat["properties"]), iso2)
        count = norm_counts.get(nk, 0)
        feat["properties"]["count"] = count
        feat["properties"]["filter_keys"] = key_to_originals.get(nk, [])
        code = feat["properties"].get("iso_3166_2", "")
        feat["properties"]["flag"] = (
            f"/dashboard/region-flag/{code.lower()}/" if flag_exists(code) else ""
        )
        parent_code = feat["properties"].get("parent_state_iso_3166_2", "")
        if parent_code:
            feat["properties"]["parent_state_flag"] = (
                f"/dashboard/region-flag/{parent_code.lower()}/"
                if flag_exists(parent_code) else ""
            )
        if count:
            matched_keys.add(nk)

    total = sum(raw_counts.values())
    matched = sum(v for k, v in norm_counts.items() if k in matched_keys)

    # Original find keys that still didn't land on a polygon, mapped back to the
    # caches behind them so the dashboard can list them and link to a filtered
    # list view for cleanup (their state/county is what failed to join).
    unmatched_keys = []
    for orig in raw_counts:
        nk = _norm_key(orig, iso2)
        if (isinstance(nk, tuple) and nk not in poly_keys
                and nk[0] in whole_state):
            nk = whole_state[nk[0]]
        if nk not in matched_keys:
            unmatched_keys.append(orig)

    from geocaches.geo.countries import iso_to_name
    from geocaches.services import stats
    gj["meta"] = {
        "total": total,
        "unmatched": total - matched,
        "iso": iso2.upper(),
        "country": iso_to_name(iso2),
        "unmatched_caches": stats.finds_in_state_county_keys(iso2, unmatched_keys),
    }
    return gj


def region_map_data(iso2: str, level: str | None = None) -> dict | None:
    """Region-tier (states/Bundesländer) choropleth data for *iso2*.  ``None``
    when the boundary hasn't been downloaded yet."""
    from geocaches.services import stats
    return _tier_map_data(
        iso2, level or effective_level(iso2),
        stats.finds_by_state,
        lambda props: props.get("name", ""),
        stats.all_by_state,
    )


def county_map_data(iso2: str, level: str | None = None) -> dict | None:
    """County-tier (Landkreise / provincie / US counties) choropleth data for
    *iso2*.  Joined by ``(parent_state, county_name)`` so name collisions
    across states (24 US "Lincoln County"s, both San Juan UT and San Juan PR)
    land on the correct polygon. ``None`` when the boundary isn't on disk yet.
    """
    from geocaches.services import stats
    return _tier_map_data(
        iso2, level or effective_county_level(iso2),
        stats.finds_by_state_county,
        lambda props: (props.get("parent_state", ""), props.get("name", "")),
        stats.all_by_state_county,
    )


# ---------------------------------------------------------------------------
# District (sub-county) boundaries for single-county states.
#
# geoBoundaries has no sub-ADM3 level, so the Bezirke / wards of single-county
# states (Berlin, Hamburg, Washington DC, …) come from OpenStreetMap
# (admin_level 9) via the ``fetch_districts`` command: the boundary relations
# are assembled into polygons and cached next to the other boundaries.
# ---------------------------------------------------------------------------
# Overpass is load-shedding-prone (504s); try mirrors in turn.
OVERPASS_URLS = (
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.osm.ch/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
)
DISTRICTS_DIRNAME = "districts"


def districts_dir() -> Path:
    return boundaries_dir() / DISTRICTS_DIRNAME


def _state_slug(state_name: str) -> str:
    s = unicodedata.normalize("NFKD", state_name)
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")


def district_path(iso2: str, state_name: str) -> Path | None:
    iso3 = iso3_for(iso2) or (iso2 or "").upper()
    if not iso3 or not state_name:
        return None
    return districts_dir() / f"{iso3}_{_state_slug(state_name)}.geojson"


def districts_downloaded(iso2: str, state_name: str) -> bool:
    p = district_path(iso2, state_name)
    return bool(p and p.exists())


def single_county_states(iso2: str) -> list[str]:
    """Parent-state names that have exactly one county polygon — the ones a
    district (sub-county) map makes sense for."""
    path = boundary_path(iso2, effective_county_level(iso2))
    if not (path and path.exists()):
        return []
    gj = json.loads(path.read_text(encoding="utf-8"))
    feats = _merge_duplicate_features(gj.get("features", []))
    per_state: dict[str, set] = {}
    for f in feats:
        ps = f["properties"].get("parent_state", "")
        if ps:
            per_state.setdefault(ps, set()).add(f["properties"].get("name", ""))
    return sorted(s for s, names in per_state.items() if len(names) == 1)


def _stitch_rings(ways: list) -> list:
    """Stitch OSM way coordinate lists (each ``[(lon, lat), …]``) into rings,
    connecting on shared endpoints regardless of order/direction."""
    ways = [list(w) for w in ways if len(w) > 1]
    rings = []
    while ways:
        ring = ways.pop(0)
        while ring and ring[0] != ring[-1]:
            for i, w in enumerate(ways):
                if ring[-1] == w[0]:
                    ring += w[1:]
                elif ring[-1] == w[-1]:
                    ring += w[-2::-1]
                elif ring[0] == w[-1]:
                    ring[:0] = w[:-1]
                elif ring[0] == w[0]:
                    ring[:0] = w[:0:-1]
                else:
                    continue
                ways.pop(i)
                break
            else:
                break  # open ring (data gap) — keep what we have
        if len(ring) >= 4 and ring[0] == ring[-1]:
            rings.append(ring)
    return rings


def _relation_geometry(rel: dict) -> dict | None:
    """Assemble an OSM admin-boundary relation (with ``out geom`` members) into
    a GeoJSON Polygon/MultiPolygon."""
    def rings(role):
        ways = [[(p["lon"], p["lat"]) for p in m["geometry"]]
                for m in rel.get("members", [])
                if m.get("type") == "way" and m.get("role") == role and m.get("geometry")]
        return _stitch_rings(ways)
    outers, inners = rings("outer"), rings("inner")
    if not outers:
        return None
    if len(outers) == 1:
        return {"type": "Polygon", "coordinates": [outers[0]] + inners}
    # Multiple outers: attach each inner ring to the first outer that contains
    # its first point (holes in admin areas are rare; this is a safe default).
    polys = [[o] for o in outers]
    for hole in inners:
        for poly in polys:
            if _point_in_ring(hole[0][0], hole[0][1], poly[0]):
                poly.append(hole)
                break
    return {"type": "MultiPolygon", "coordinates": polys}


def download_districts(iso2: str, state_name: str) -> int:
    """Fetch the admin_level-9 districts of *state_name* from OSM, assemble and
    cache them.  Returns the feature count.

    Uses an administratively precise ``area[...]`` lookup; mirrors that lack
    the precomputed area index answer with an empty set, so an empty response
    is treated as a miss and the next mirror is tried.
    """
    out = district_path(iso2, state_name)
    if out is None:
        raise ValueError(f"Unknown country code: {iso2!r}")
    query = (
        "[out:json][timeout:180];"
        f'area["name"="{state_name}"]["admin_level"="4"]->.a;'
        'relation(area.a)["boundary"="administrative"]["admin_level"="9"];'
        "out geom;"
    )
    last_exc: Exception | None = None
    elements = None
    for url in OVERPASS_URLS:
        try:
            resp = requests.post(url, data={"data": query},
                                 headers={"User-Agent": _HTTP_UA}, timeout=180)
            resp.raise_for_status()
            els = resp.json().get("elements", [])
            if els:  # empty == mirror without an area index; try the next
                elements = els
                break
        except (requests.RequestException, ValueError) as exc:
            last_exc = exc
    if elements is None:
        raise RuntimeError(f"no districts for {state_name!r} (Overpass: {last_exc})")
    feats = []
    for rel in elements:
        geom = _relation_geometry(rel)
        name = rel.get("tags", {}).get("name", "")
        if geom and name:
            feats.append({"type": "Feature",
                          "properties": {"name": name},
                          "geometry": geom})
    if not feats:
        # No admin_level-9 districts (or a transient empty response) — don't
        # cache an empty file so the next run retries.
        raise RuntimeError(f"no districts found for {state_name!r}")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps({"type": "FeatureCollection", "features": feats},
                   separators=(",", ":"), ensure_ascii=False),
        encoding="utf-8",
    )
    return len(feats)


def district_map_data(iso2: str, state_name: str) -> dict | None:
    """Sub-county district choropleth for one single-county state.  ``None``
    when its districts haven't been fetched yet."""
    path = district_path(iso2, state_name)
    if not (path and path.exists()):
        return None
    from geocaches.services import stats

    gj = json.loads(path.read_text(encoding="utf-8"))
    raw = stats.finds_by_district(iso2, state_name)
    norm_counts: dict = {}
    for k, c in raw.items():
        norm_counts[normalize_name(k, iso2)] = norm_counts.get(normalize_name(k, iso2), 0) + c

    key_to_originals: dict = {}
    for k in stats.all_by_district(iso2, state_name):
        key_to_originals.setdefault(normalize_name(k, iso2), []).append([state_name, k])

    matched_keys = set()
    for feat in gj.get("features", []):
        nk = normalize_name(feat["properties"].get("name", ""), iso2)
        count = norm_counts.get(nk, 0)
        feat["properties"]["count"] = count
        feat["properties"]["parent_state"] = state_name
        feat["properties"]["filter_keys"] = key_to_originals.get(nk, [])
        if count:
            matched_keys.add(nk)

    total = sum(raw.values())
    matched = sum(v for k, v in norm_counts.items() if k in matched_keys)
    gj["meta"] = {"total": total, "unmatched": total - matched,
                  "iso": iso2.upper(), "state": state_name}
    return gj
