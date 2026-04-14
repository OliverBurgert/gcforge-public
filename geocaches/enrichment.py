"""
Cache data enrichment — fills fields not provided by import sources.

Fields enriched:
  elevation  — SRTM tiles via srtm.py (tiles downloaded once, cached locally,
               then fully offline — no rate limit)
  country    — OpenStreetMap Nominatim reverse geocoding
  state      — OpenStreetMap Nominatim reverse geocoding
  county     — OpenStreetMap Nominatim reverse geocoding

Enrichment is non-destructive: only blank / null fields are filled.
``elevation_user`` is never touched.

Public API
----------
enrich_geocache(cache, fields)     → bool
enrich_queryset(qs, fields=None)   → EnrichStats
"""
from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

_NOMINATIM_URL   = "https://nominatim.openstreetmap.org/reverse"
_NOMINATIM_UA    = "GCForge/1.0 (geocache management; https://github.com/gcforge/gcforge)"
_NOMINATIM_DELAY = 1.1   # seconds — Nominatim usage policy: max 1 req/s

_OPENTOPODATA_URL   = "https://api.opentopodata.org/v1/"
_OPENTOPODATA_DELAY = 1.1  # same rate limit: 1 req/s, 1000/day
# Dataset priority: EU-DEM 25m (Europe, 2.3m RMSE) then ASTER GDEM v3 (global, independent
# from SRTM — different sensor, different void pattern, better chance of covering SRTM gaps)
_OPENTOPODATA_DATASETS = ("eudem25m", "aster30m")

_srtm_data = None              # lazy singleton — first call triggers tile download
_nominatim_blocked_until = 0.0 # time.time() value; 0 = not blocked; auto-resets after 24 h


def _get_srtm():
    global _srtm_data
    if _srtm_data is None:
        import srtm  # type: ignore[import]
        _srtm_data = srtm.get_data()
    return _srtm_data


# ---------------------------------------------------------------------------
# Fetch helpers
# ---------------------------------------------------------------------------

def fetch_elevation(lat: float, lon: float) -> Optional[float]:
    """Return elevation in metres from SRTM data, or None if unavailable."""
    try:
        ele = _get_srtm().get_elevation(lat, lon)
        return float(ele) if ele is not None else None
    except Exception as exc:
        logger.warning("SRTM lookup error (%.5f, %.5f): %s", lat, lon, exc)
        return None


def fetch_elevation_online(lat: float, lon: float) -> Optional[float]:
    """Return elevation in metres from OpenTopoData, or None if unavailable.

    Tries EU-DEM 25m first (Europe, 2.3 m RMSE south of 60°N), then falls back
    to Copernicus GLO-30 (global).  Always sleeps _OPENTOPODATA_DELAY after each
    request to respect the 1 req/s rate limit.
    """
    for dataset in _OPENTOPODATA_DATASETS:
        try:
            url = f"{_OPENTOPODATA_URL}{dataset}?locations={lat},{lon}"
            req = urllib.request.Request(url, headers={"User-Agent": _NOMINATIM_UA})
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read())
            results = data.get("results", [])
            if results:
                ele = results[0].get("elevation")
                if ele is not None:
                    logger.info(
                        "OpenTopoData %s: (%.5f, %.5f) → %.1f m",
                        dataset, lat, lon, float(ele),
                    )
                    return float(ele)
                # null result = coords outside dataset coverage — expected, not an error
                logger.debug("OpenTopoData %s: no data for (%.5f, %.5f)", dataset, lat, lon)
        except Exception as exc:
            logger.warning("OpenTopoData %s error (%.5f, %.5f): %s", dataset, lat, lon, exc)
            # Don't try next dataset on network/timeout errors — likely transient
            break
        finally:
            time.sleep(_OPENTOPODATA_DELAY)
    return None


def _nominatim_request(lat: float, lon: float, accept_language: str = "") -> dict:
    """Send one Nominatim reverse-geocode request and return the parsed JSON.

    Sleeps _NOMINATIM_DELAY after the request.  Raises on HTTP/network errors
    so callers can handle them in one place.
    """
    params = f"lat={lat}&lon={lon}&format=jsonv2&zoom=13&addressdetails=1"
    if accept_language:
        params += f"&accept-language={accept_language}"
    url = f"{_NOMINATIM_URL}?{params}"
    req = urllib.request.Request(url, headers={"User-Agent": _NOMINATIM_UA})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read())
    finally:
        time.sleep(_NOMINATIM_DELAY)


def _extract_address_fields(addr: dict) -> dict[str, str]:
    return {
        # city fallback covers city-states (Berlin, Hamburg, Bremen) where there
        # is no state key.
        "state":  (addr.get("state") or addr.get("province") or
                   addr.get("region") or addr.get("city") or ""),
        # For city-states (Berlin, Hamburg, Bremen) and kreisfreie Städte,
        # Nominatim has no county/municipality — fall back to city_district
        # and borough which are the county-equivalent level (e.g. Berlin
        # Bezirke: "Mitte", "Pankow"; DC wards, etc.).
        "county": (addr.get("county") or addr.get("municipality") or
                   addr.get("city_district") or addr.get("borough") or ""),
    }


def fetch_location(lat: float, lon: float) -> dict[str, str]:
    """Return {iso_country_code, country, state, county} from Nominatim.

    - iso_country_code is always the ISO 3166-1 alpha-2 code (uppercase) from
      Nominatim's address.country_code — independent of language.
    - country is kept as the local-language name for legacy/display purposes.
    - state / county use the local language, which is correct for most regions
      (e.g. "Baden-Württemberg" for Germany).  If they come back in a non-Latin
      script (Cyrillic, CJK, Arabic …), a second request with accept-language=en
      is issued to get a Latin transliteration.

    Always sleeps _NOMINATIM_DELAY after each request (rate limiting).
    Returns an empty dict on error.
    Sets _nominatim_blocked_until on HTTP 429 to pause requests for 24 hours.
    """
    from geocaches.countries import is_latin, strip_admin_suffix

    global _nominatim_blocked_until
    if time.time() < _nominatim_blocked_until:
        return {}

    result: dict[str, str] = {}
    try:
        data = _nominatim_request(lat, lon)
        addr = data.get("address", {})
        fields = _extract_address_fields(addr)
        state  = fields["state"]
        county = fields["county"]

        # If state or county contain non-Latin characters, re-request in English.
        if (state and not is_latin(state)) or (county and not is_latin(county)):
            try:
                data_en = _nominatim_request(lat, lon, accept_language="en")
                addr_en = data_en.get("address", {})
                fields_en = _extract_address_fields(addr_en)
                if fields_en["state"]:
                    state = fields_en["state"]
                if fields_en["county"]:
                    county = fields_en["county"]
            except Exception as exc:
                logger.warning("Nominatim en-fallback error (%.5f, %.5f): %s", lat, lon, exc)

        iso = addr.get("country_code", "").upper()
        result = {
            "iso_country_code": iso,
            "country":          addr.get("country", ""),
            "state":            strip_admin_suffix(state,  iso, "state"),
            "county":           strip_admin_suffix(county, iso, "county"),
        }
    except urllib.error.HTTPError as exc:
        if exc.code == 429:
            _nominatim_blocked_until = time.time() + 86400  # 24-hour cooldown
            logger.error(
                "--- Nominatim rate limited (HTTP 429) at (%.5f, %.5f) — "
                "daily quota exceeded. Location enrichment paused for 24 hours "
                "(until %s). ---",
                lat, lon,
                time.strftime("%Y-%m-%d %H:%M", time.localtime(_nominatim_blocked_until)),
            )
        else:
            logger.warning("Nominatim lookup error (%.5f, %.5f): %s", lat, lon, exc)
    except Exception as exc:
        logger.warning("Nominatim lookup error (%.5f, %.5f): %s", lat, lon, exc)
    return result


# ---------------------------------------------------------------------------
# Per-cache enrichment
# ---------------------------------------------------------------------------

def enrich_geocache(cache, fields: set[str], overwrite: set[str] | None = None) -> bool:
    """Fill enrichment fields on *cache* and save.

    Returns True if any field was changed.

    Parameters
    ----------
    fields:
        Fields to enrich.
    overwrite:
        Subset of *fields* where existing values should be replaced.
        ``elevation_user`` is never overwritten regardless of this setting.
        Defaults to the empty set (fill-blanks-only mode).
    """
    if overwrite is None:
        overwrite = set()

    changed = False

    if "elevation" in fields:
        # elevation_hires: skip SRTM entirely, go straight to online source
        # elevation (overwrite): re-fetch SRTM, clear if void
        # elevation (fill): SRTM first, online fallback on None
        # elevation_user is never touched regardless of mode
        hires = "elevation_hires" in overwrite
        code = cache.gc_code or cache.oc_code or str(cache.pk)

        if cache.elevation_user is None and (hires or "elevation" in overwrite or cache.elevation is None):
            if hires:
                ele = fetch_elevation_online(cache.latitude, cache.longitude)
            else:
                ele = fetch_elevation(cache.latitude, cache.longitude)
                if ele is None:
                    # SRTM void or missing tile — try online fallback
                    online = fetch_elevation_online(cache.latitude, cache.longitude)
                    if online is not None:
                        logger.info("%s SRTM void — online fallback: %.1f m", code, online)
                    ele = online

            if ele is not None:
                logger.info("%s elevation → %.1f m", code, ele)
                cache.elevation = ele
                changed = True
            elif ("elevation" in overwrite or hires) and cache.elevation is not None:
                # Both sources returned None — clear stale GSAK default
                logger.info("%s no elevation data — cleared stale value %.1f m", code, cache.elevation)
                cache.elevation = None
                changed = True
            else:
                logger.info("%s no elevation data (%.5f, %.5f)", code, cache.latitude, cache.longitude)

    loc_overwrite = "location" in overwrite
    needs_location = (
        "location" in fields and (
            loc_overwrite
            or not (cache.country and cache.state and cache.county)
            or not cache.iso_country_code
        )
    )
    if needs_location:
        lcode = cache.gc_code or cache.oc_code or str(cache.pk)
        loc = fetch_location(cache.latitude, cache.longitude)
        if loc:
            filled = {}
            if (loc_overwrite or not cache.iso_country_code) and loc.get("iso_country_code"):
                cache.iso_country_code = loc["iso_country_code"]
                filled["iso_country_code"] = cache.iso_country_code
                changed = True
            if (loc_overwrite or not cache.country) and loc.get("country"):
                cache.country = loc["country"]
                filled["country"] = cache.country
                changed = True
            if (loc_overwrite or not cache.state) and loc.get("state"):
                cache.state = loc["state"]
                filled["state"] = cache.state
                changed = True
            if (loc_overwrite or not cache.county) and loc.get("county"):
                cache.county = loc["county"]
                filled["county"] = cache.county
                changed = True
            if filled:
                logger.info("%s location → %s", lcode, ", ".join(f"{k}={v}" for k, v in filled.items()))
            else:
                missing = [f for f in ("iso_country_code", "country", "state", "county") if not getattr(cache, f)]
                logger.info(
                    "%s location no data (%.5f, %.5f) — Nominatim missing: %s",
                    lcode, cache.latitude, cache.longitude, ", ".join(missing),
                )

    if changed:
        save_fields: list[str] = []
        if "elevation" in fields:
            save_fields.append("elevation")
        if "location" in fields:
            save_fields += ["iso_country_code", "country", "state", "county"]
        cache.save(update_fields=save_fields)

    return changed


# ---------------------------------------------------------------------------
# Queryset-level enrichment
# ---------------------------------------------------------------------------

@dataclass
class EnrichStats:
    updated: int = 0
    no_data: int = 0           # source returned nothing (SRTM void, OTD null, Nominatim empty)
    already_complete: int = 0  # pre-filtered: fields already populated, nothing to do
    errors: list[str] = field(default_factory=list)


def _needs_work_q(fields: set[str], overwrite: set[str]):
    """Return a Q object matching caches that need at least one enriched field."""
    from django.db.models import Q
    q = Q(pk__in=[])  # falsy base
    if "elevation" in fields:
        if "elevation" in overwrite or "elevation_hires" in overwrite:
            # Re-fetch for all caches that don't have a user override
            q |= Q(elevation_user__isnull=True)
        else:
            q |= Q(elevation__isnull=True, elevation_user__isnull=True)
    if "location" in fields:
        if "location" in overwrite:
            q |= Q(pk__isnull=False)  # all caches
        else:
            q |= Q(country="") | Q(state="") | Q(county="") | Q(iso_country_code="")
    return q


def enrich_queryset(
    qs,
    fields: set[str] | None = None,
    overwrite: set[str] | None = None,
    progress_callback=None,
    cancel_event=None,
) -> EnrichStats:
    """Enrich all caches in *qs* that are missing (or, in overwrite mode, have stale) fields.

    Parameters
    ----------
    qs:
        Geocache queryset.
    fields:
        ``{"elevation"}``, ``{"location"}``, or ``None`` (both).
    overwrite:
        Subset of *fields* to re-fetch even when a value already exists.
        ``elevation_user`` is always protected.
    """
    if fields is None:
        fields = {"elevation", "location"}
    if overwrite is None:
        overwrite = set()

    from django.db.models.functions import Floor
    stats = EnrichStats()
    to_enrich = qs.filter(_needs_work_q(fields, overwrite))
    stats.already_complete = qs.count() - to_enrich.count()

    # Sort by tile (1°×1° SRTM cell) so each tile is loaded from disk once and
    # stays in memory for all caches within it before moving to the next tile.
    if "elevation" in fields:
        to_enrich = to_enrich.order_by(Floor("latitude"), Floor("longitude"))

    if "elevation_hires" in overwrite:
        mode = "hires-online"
    elif overwrite:
        mode = "overwrite"
    else:
        mode = "fill-missing"
    logger.info(
        "--- Enrichment start: %d to process, %d already complete, fields=%s, mode=%s ---",
        to_enrich.count(), stats.already_complete, sorted(fields), mode,
    )

    count = 0
    for cache in to_enrich.iterator():
        if cancel_event and cancel_event.is_set():
            logger.info("Enrichment cancelled by user after %d caches", count)
            break

        if progress_callback:
            progress_callback(count, "")

        try:
            if enrich_geocache(cache, fields, overwrite):
                stats.updated += 1
            else:
                stats.no_data += 1
        except Exception as exc:
            label = cache.gc_code or cache.oc_code or str(cache.pk)
            stats.errors.append(f"{label}: {exc}")
            logger.warning("Error enriching %s: %s", label, exc)
        count += 1
        time.sleep(0.01)  # yield to other DB writers between saves

    if progress_callback:
        progress_callback(count, "done")

    logger.info(
        "--- Enrichment done: %d updated, %d no data from source, %d already complete, %d errors ---",
        stats.updated, stats.no_data, stats.already_complete, len(stats.errors),
    )
    if time.time() < _nominatim_blocked_until and "location" in fields:
        logger.error(
            "Nominatim daily quota was exceeded during this run. "
            "Remaining caches were not location-enriched. Enrichment will resume after %s.",
            time.strftime("%Y-%m-%d %H:%M", time.localtime(_nominatim_blocked_until)),
        )
    return stats
