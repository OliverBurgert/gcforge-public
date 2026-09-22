"""
Geocaching.com website search client (unofficial /api/proxy/web/search/v2).

Used for criteria-based discovery the partner API v1 can't express — e.g.
"all mysteries hidden by user X". Driven by the authenticated website session
(``geocaches.sync.gc_web.session``) plus the bearer token scraped from a
logged-in page, the same mechanism the Treasures scraper uses.

The endpoint returns lightweight cache rows inline, so a criteria search doubles
as the light fetch (no per-cache detail call) and does NOT consume the
partner-API quota. Full sync of the discovered codes still goes through the
partner ``GCClient`` (descriptions, logs, waypoints).

Brittle by nature: it depends on an undocumented endpoint + gc.com markup. This
is the same accepted trade-off as Pocket-Query triggering and Treasures.

Relocated from ``gcprivate/gc_web_client.py`` (2026-08-13) — public build, needs
only the user's own GC password. See ``docs/reference/geocaching-com-web.md``.
"""

import logging

from geocaches.sync.gc_reference import _SIZE_MAP, _TYPE_MAP

logger = logging.getLogger(__name__)

_SEARCH_URL = "https://www.geocaching.com/api/proxy/web/search/v2"

# GCForge cache_type value → GC numeric geocacheType id (for the `ct` param).
_REVERSE_TYPE_ID: dict[str, int] = {}
for _id, _val in _TYPE_MAP.items():
    if _id > 0:  # skip the synthetic -1 (Adventure Lab)
        _REVERSE_TYPE_ID.setdefault(_val, _id)

# GCForge size value → GC numeric size id (for the `cs` param).
_REVERSE_SIZE_ID: dict[str, int] = {}
for _id, _val in _SIZE_MAP.items():
    _REVERSE_SIZE_ID.setdefault(_val, _id)


class GCWebClient:
    """Criteria-search client for geocaching.com via the website API proxy.

    Only implements the criteria-search surface — full sync uses ``GCClient``.
    """

    platform = "gc"
    _PAGE = 500  # take per request (endpoint allows up to 1000)

    def search_criteria_lite(
        self,
        criteria: dict,
        *,
        bbox: tuple[float, float, float, float] | None = None,
        max_results: int = 500,
        cancel_event=None,
        limiter=None,
    ) -> list[dict]:
        """Return normalized LIGHT cache dicts matching ``criteria``.

        Shape matches ``GCClient.normalize(..., LIGHT)`` so the preview service's
        ``_previews_from_lite`` can flatten it like the partner-API lite path.
        """
        from geocaches.sync.gc_web.session import get_api_token, get_session, reset_session

        session = get_session()
        token = get_api_token()
        if not token:
            raise RuntimeError(
                "Could not obtain a geocaching.com web API token — "
                "check the GC account password in Settings → Accounts."
            )

        base_params = self._build_params(criteria, bbox)
        logger.info("  GC web search params: %s", base_params)

        out: list[dict] = []
        skip = 0
        retried_auth = False
        while len(out) < max_results:
            if cancel_event and cancel_event.is_set():
                break
            if limiter:
                limiter.wait(cancel_event)
            take = min(self._PAGE, max_results - len(out))
            params = dict(base_params, skip=str(skip), take=str(take))
            resp = session.get(
                _SEARCH_URL,
                params=params,
                headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
                timeout=30,
            )
            if resp.status_code in (401, 403) and not retried_auth:
                # Stale session/token — re-login once and retry this page.
                retried_auth = True
                reset_session()
                session = get_session()
                token = get_api_token(force=True)
                continue
            resp.raise_for_status()
            data = resp.json()
            results = data.get("results") or []
            if not results:
                break
            for r in results:
                row = self._normalize_row(r)
                if row:
                    out.append(row)
            total = data.get("total")
            skip += len(results)
            if len(results) < take or (total is not None and skip >= total):
                break
        logger.info("  GC web search returned %d caches", len(out))
        return out

    def search_lite_by_bbox(
        self,
        south: float, west: float, north: float, east: float,
        *,
        max_results: int = 500,
        cancel_event=None,
        limiter=None,
    ) -> list[dict]:
        """Plain bounding-box search, no criteria filters.

        **Confirmed live 2026-08-15**: ``search_criteria_lite({}, bbox=...)``
        works as a general-purpose area search — the ``box`` query param is
        independent of every other filter param, so an empty criteria dict
        just omits ``ct``/``cs``/``d``/``t``/etc. and leaves ``box`` as the
        only real constraint. Same duck-typed shape as
        ``BasePlatformClient.search_lite_by_bbox()`` (this class doesn't
        inherit it — see the module docstring — but ``preview_by_bbox()`` in
        ``geocaches.sync.service`` calls it structurally either way).
        """
        return self.search_criteria_lite(
            {}, bbox=(south, west, north, east), max_results=max_results,
            cancel_event=cancel_event, limiter=limiter,
        )

    def search_lite_by_center(
        self,
        lat: float, lon: float, radius_m: float,
        *,
        max_results: int = 500,
        cancel_event=None,
        limiter=None,
    ) -> list[dict]:
        """Plain center+radius search, no criteria filters — converts to a
        bounding box the same way ``BasePlatformClient.search_by_center()``
        does, then delegates to ``search_lite_by_bbox()``."""
        import math
        r_km = radius_m / 1000
        d_lat = r_km / 111.32
        d_lon = r_km / (111.32 * math.cos(math.radians(lat)))
        return self.search_lite_by_bbox(
            lat - d_lat, lon - d_lon, lat + d_lat, lon + d_lon,
            max_results=max_results, cancel_event=cancel_event, limiter=limiter,
        )

    def search_by_bbox(
        self, south: float, west: float, north: float, east: float, *, max_results: int = 500,
    ) -> list[str]:
        """Bare bbox search returning codes only.

        Only needed by ``preview_by_boxes()``'s corridor path in
        ``geocaches.sync.service``, which (unlike ``preview_by_bbox()``/
        ``preview_by_center()``) calls this directly with no lite-first
        check — every other region type goes through ``search_lite_by_bbox()``
        above and never reaches this method.
        """
        rows = self.search_lite_by_bbox(south, west, north, east, max_results=max_results)
        return [r["gc_code"] for r in rows]

    def search_by_center(
        self, lat: float, lon: float, radius_m: float, *, max_results: int = 500,
    ) -> list[str]:
        """Bare center+radius search returning codes only — see
        ``search_by_bbox()``'s docstring for why this exists."""
        rows = self.search_lite_by_center(lat, lon, radius_m, max_results=max_results)
        return [r["gc_code"] for r in rows]

    # ── param building ──────────────────────────────────────────────────────

    def _build_params(self, criteria: dict, bbox) -> dict:
        params: dict[str, str] = {"app": "gcforge"}

        types = [str(_REVERSE_TYPE_ID[t]) for t in criteria.get("types", []) if t in _REVERSE_TYPE_ID]
        if types:
            params["ct"] = ",".join(types)
        sizes = [str(_REVERSE_SIZE_ID[s]) for s in criteria.get("sizes", []) if s in _REVERSE_SIZE_ID]
        if sizes:
            params["cs"] = ",".join(sizes)

        d = self._range(criteria.get("d_min"), criteria.get("d_max"))
        if d:
            params["d"] = d
        t = self._range(criteria.get("t_min"), criteria.get("t_max"))
        if t:
            params["t"] = t

        owner = (criteria.get("owner") or "").strip()
        if owner:
            params["hb"] = owner
        found_by = (criteria.get("found_by") or "").strip()
        if found_by:
            params["fb"] = found_by
        not_found_by = (criteria.get("not_found_by") or "").strip()
        if not_found_by:
            params["nfb"] = not_found_by

        if criteria.get("min_fav"):
            try:
                params["fp"] = str(int(criteria["min_fav"]))
            except (TypeError, ValueError):
                pass

        name = (criteria.get("name") or "").strip()
        if name:
            params["cn"] = name

        fs = criteria.get("found_status")
        if fs == "found_only":
            params["hf"] = "0"   # show only found
        elif fs == "notfound_only":
            params["hf"] = "1"   # hide found

        if criteria.get("exclude_disabled"):
            params["sd"] = "0"   # enabled only
        if criteria.get("include_archived"):
            params["sa"] = "1"

        if bbox:
            s, w, n, e = bbox
            params["box"] = f"{n},{w},{s},{e}"  # latMax,lonMin,latMin,lonMax
            params["sort"] = "distance"
        else:
            params["sort"] = "geocachename"
            params["asc"] = "true"
        return params

    @staticmethod
    def _range(lo, hi) -> str:
        """Format a D/T range as ``min-max`` (e.g. ``2-4.5``); '' when full 1–5."""
        lo = 1 if lo is None else float(lo)
        hi = 5 if hi is None else float(hi)
        if (lo, hi) == (1.0, 5.0):
            return ""

        def fmt(x):
            return str(int(x)) if float(x).is_integer() else str(x)

        return f"{fmt(lo)}-{fmt(hi)}"

    # ── response normalization ────────────────────────────────────────────────

    def _normalize_row(self, r: dict) -> dict | None:
        code = (r.get("code") or "").strip()
        if not code:
            return None
        coords = r.get("postedCoordinates") or {}
        lat, lon = coords.get("latitude"), coords.get("longitude")
        if lat is None or lon is None:
            return None
        return {
            "gc_code": code,
            "fields": {
                "name": r.get("name") or "",
                "cache_type": _TYPE_MAP.get(r.get("geocacheType"), "Unknown"),
                "size": _SIZE_MAP.get(r.get("containerType"), "Unknown"),
                "difficulty": r.get("difficulty"),
                "terrain": r.get("terrain"),
                "latitude": lat,
                "longitude": lon,
                "status": "Active",
            },
            "found": bool(r.get("userFound")),
        }
