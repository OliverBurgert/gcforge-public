"""
Digital Treasures service — read-only scrape of geocaching.com's Treasures.

There is no partner API for Treasures (see ``docs/reference/geocaching-com.md``
§5), so this uses the authenticated web session (like Pocket Queries).  Each
collection's earning criteria (cache types / sizes / favourite points) is parsed
into a filter so GCForge can list candidate caches from the local DB — the
equivalent of gc.com's "Treasure map".

Brittle by nature: it parses gc.com markup + undocumented endpoints, and most
collections are premium-gated.
"""

from __future__ import annotations

import logging
from datetime import date
from urllib.parse import parse_qs, urlparse

logger = logging.getLogger(__name__)

_BASE = "https://www.geocaching.com/play/treasure"


# ---------------------------------------------------------------------------
# HTML / criteria parsing
# ---------------------------------------------------------------------------

def _soup(html: str):
    from bs4 import BeautifulSoup
    return BeautifulSoup(html or "", "html.parser")


def _access_token(page_html: str) -> str:
    import re
    m = re.search(r'window\.accessToken\s*=\s*"([^"]+)"', page_html)
    return m.group(1) if m else ""


def _to_int(v) -> int:
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return 0


def _parse_date(v):
    try:
        return date.fromisoformat(str(v)[:10])
    except (TypeError, ValueError):
        return None


def _parse_collection_el(el) -> dict | None:
    """Parse one collection element (active accordion or grid card) → dict."""
    def attr(*names):
        for n in names:
            v = el.get(n)
            if v:
                return v
        return ""

    cid = attr("data-collection-id", "data-id")
    title = attr("data-title")
    if not (cid and str(cid).isdigit() and title):
        return None
    found, total = _to_int(attr("data-found")), _to_int(attr("data-total"))
    # Grid cards carry a clean data-iscompleted; the accordion's
    # data-collection-iscompleted is a server-template artefact, so only trust
    # an explicit "true" (+ found>=total fallback).
    is_completed = str(attr("data-iscompleted")).lower() == "true"
    if total and found >= total:
        is_completed = True
    return {
        "collection_id": int(cid),
        "name": title.strip(),
        "description": attr("data-description").strip(),
        "image_url": attr("data-image"),
        "found": found,
        "total": total,
        "is_completed": is_completed,
        "premium_only": str(attr("data-premiumonly")).lower() == "true",
        "start_date": _parse_date(attr("data-startdate")),
        "criteria_url": attr("data-treasuremapurl"),  # only on the accordion
    }


def parse_criteria_url(url: str) -> dict:
    """gc.com search/map URL → criteria dict.

    Reads ``ct`` (cache-type ids), ``cs`` (size ids) and ``fp`` (min favourite
    points), mapping ids to GCForge values via the GC client tables.
    """
    from geocaches.sync.gc_reference import _SIZE_MAP, _TYPE_MAP
    q = parse_qs(urlparse(url or "").query)

    def ids(key):
        raw = (q.get(key, [""])[0] or "")
        return [int(x) for x in raw.split(",") if x.strip().isdigit()]

    type_ids, size_ids = ids("ct"), ids("cs")
    return {
        "type_ids": type_ids,
        "types": [_TYPE_MAP[i] for i in type_ids if i in _TYPE_MAP],
        "size_ids": size_ids,
        "sizes": [_SIZE_MAP[i] for i in size_ids if i in _SIZE_MAP],
        "min_fp": _to_int(q.get("fp", ["0"])[0]),
    }


def criteria_to_where_sql(criteria: dict) -> str:
    """Build a list-view ``where_sql`` selecting unfound candidate caches."""
    def lit(s):
        return "'" + str(s).replace("'", "''") + "'"

    parts = []
    if criteria.get("types"):
        parts.append("cache_type IN (" + ", ".join(lit(t) for t in criteria["types"]) + ")")
    if criteria.get("sizes"):
        parts.append("size IN (" + ", ".join(lit(s) for s in criteria["sizes"]) + ")")
    if criteria.get("min_fp"):
        parts.append(f"fav_points >= {int(criteria['min_fp'])}")
    if not parts:
        return "1 = 0"
    parts += ["found = 0", "completed = 0", "status = 'Active'"]
    return " AND ".join(parts)


# ---------------------------------------------------------------------------
# Refresh (scrape + upsert)
# ---------------------------------------------------------------------------

def _fetch_criteria_url(session, hdr, cid: int) -> str:
    try:
        r = session.get(
            _BASE + "/collections/GetPlayerTreasureCollectionJson",
            params={"collectionId": cid, "hideLayout": "false"},
            headers=hdr, timeout=30,
        )
        j = r.json()
        return j.get("geocacheSearchUrl") or j.get("treasureMapUrl") or ""
    except Exception as exc:  # noqa: BLE001
        logger.warning("treasure criteria fetch failed for %s: %s", cid, exc)
        return ""


def refresh() -> dict:
    """Scrape all collections + criteria and upsert.  Returns a summary."""
    from gcprivate.gc_web_session import get_session
    from geocaches.models import TreasureCollection

    session = get_session()
    page = session.get(_BASE, timeout=30).text
    hdr = {"AccessToken": _access_token(page), "X-Requested-With": "XMLHttpRequest"}

    collections: dict[int, dict] = {}

    def merge(c):
        if not c:
            return
        cur = collections.get(c["collection_id"])
        if cur is None:
            collections[c["collection_id"]] = c
        else:
            for k, v in c.items():
                if v not in (None, "", [], 0) or k not in cur or cur[k] in (None, ""):
                    cur[k] = v

    # 1. Active / priority collections (JSON accordionHtml; carry treasuremapurl)
    try:
        j = session.post(_BASE + "/GetActiveCollections", headers=hdr, timeout=30).json()
        for el in _soup(j.get("accordionHtml", "")).select("[data-collection-id]"):
            merge(_parse_collection_el(el))
    except Exception as exc:  # noqa: BLE001
        logger.warning("GetActiveCollections failed: %s", exc)

    # 2. Available + completed grids (paged)
    for completed in ("false", "true"):
        page_no = 1
        while page_no <= 50:
            html = session.post(
                _BASE + "/GetFilteredCollections",
                data={"sort": "StartDate", "asc": "false",
                      "showCompletedOnly": completed, "pageNumber": str(page_no)},
                headers=hdr, timeout=30,
            ).text
            els = _soup(html).select("[data-id][data-title]")
            before = len(collections)
            for el in els:
                merge(_parse_collection_el(el))
            if not els or len(collections) == before:
                break
            page_no += 1

    # 3. Criteria for incomplete collections only (completed need no candidates)
    for c in collections.values():
        if c.get("is_completed"):
            continue
        url = c.get("criteria_url") or _fetch_criteria_url(session, hdr, c["collection_id"])
        if url:
            c["criteria_url"] = url
            c["criteria"] = parse_criteria_url(url)

    # 4. Upsert
    seen = set()
    for c in collections.values():
        seen.add(c["collection_id"])
        TreasureCollection.objects.update_or_create(
            collection_id=c["collection_id"],
            defaults={
                "name": c["name"],
                "description": c.get("description", ""),
                "image_url": c.get("image_url", ""),
                "found": c.get("found", 0),
                "total": c.get("total", 0),
                "is_completed": c.get("is_completed", False),
                "premium_only": c.get("premium_only", False),
                "start_date": c.get("start_date"),
                "criteria": c.get("criteria", {}),
                "criteria_url": c.get("criteria_url", ""),
            },
        )
    TreasureCollection.objects.exclude(collection_id__in=seen).delete()
    return {"total": len(seen)}


# ---------------------------------------------------------------------------
# Read helpers for the dashboard tab
# ---------------------------------------------------------------------------

def list_collections():
    from geocaches.models import TreasureCollection
    return TreasureCollection.objects.all()


# Cache the scraped AccessToken so opening a detail modal doesn't re-fetch the
# whole treasure page every time. Cleared + retried if a call comes back bad.
_token_cache = None


def _session_and_token():
    from gcprivate.gc_web_session import get_session
    global _token_cache
    session = get_session()
    if not _token_cache:
        _token_cache = _access_token(session.get(_BASE, timeout=30).text)
    return session, _token_cache


def _fetch_treasure_type(session, token, tid) -> dict:
    """Per-treasure-type artwork (works for *locked* items — gc.com hides it in
    the collection view but exposes it here).  Returns ``{name, image_url}``."""
    try:
        r = session.get(
            _BASE + "/GetTreasureTypeDetails",
            params={"treasureTypeId": tid, "hideLayout": "false", "ownershipView": "false"},
            headers={"AccessToken": token, "X-Requested-With": "XMLHttpRequest"},
            timeout=20,
        )
        img = _soup(r.text).select_one("img[src*='gs-geo-images']")
        if img is None:
            return {}
        return {"name": (img.get("alt") or "").strip(), "image_url": img.get("src") or ""}
    except Exception:  # noqa: BLE001
        return {}


def fetch_detail(collection_id: int, reveal_locked: bool = False) -> dict:
    """Live-fetch one collection's detail (criteria text + treasure artwork).

    Returns ``{rules_desc, criteria, items: [{tid, locked, name, image_url}],
    has_locked}``.  Owned items carry their artwork; locked ones have no image
    unless *reveal_locked* is set, when their artwork is fetched in parallel
    from the per-treasure-type endpoint.
    """
    global _token_cache
    session, token = _session_and_token()

    def _call(tok):
        return session.get(
            _BASE + "/collections/GetPlayerTreasureCollectionJson",
            params={"collectionId": collection_id, "hideLayout": "false"},
            headers={"AccessToken": tok, "X-Requested-With": "XMLHttpRequest"},
            timeout=30,
        )

    try:
        j = _call(token).json()
    except Exception:  # noqa: BLE001 — stale token? refetch once
        _token_cache = None
        session, token = _session_and_token()
        j = _call(token).json()

    soup = _soup(j.get("html", ""))
    rules = soup.select_one("#how-to-find-rules-description")
    items = []
    for el in soup.select("[data-treasure-type-id]"):
        img = el.select_one("img")
        items.append({
            "tid": el.get("data-treasure-type-id"),
            "locked": str(el.get("data-locked", "")).lower() == "true",
            "name": (img.get("alt") if img else "") or "",
            "image_url": (img.get("src") if img else "") or "",
        })

    if reveal_locked:
        from concurrent.futures import ThreadPoolExecutor
        locked = [it for it in items if it["locked"] and it["tid"]]
        if locked:
            with ThreadPoolExecutor(max_workers=8) as ex:
                arts = list(ex.map(
                    lambda it: _fetch_treasure_type(session, token, it["tid"]), locked))
            for it, art in zip(locked, arts, strict=False):
                if art.get("image_url"):
                    it["image_url"] = art["image_url"]
                    it["name"] = art.get("name") or it["name"]

    return {
        "rules_desc": rules.get_text(" ", strip=True) if rules else "",
        "criteria": [" ".join(li.get_text(" ", strip=True).split())
                     for li in soup.select("li.prerequisite")],
        "items": items,
        "has_locked": any(it["locked"] for it in items),
    }


def dashboard_context() -> dict:
    from django.db.models import Count, Q, Sum

    from accounts.models import UserAccount
    from geocaches.models import TreasureCollection
    agg = TreasureCollection.objects.aggregate(
        collected=Sum("found"),
        completed=Count("id", filter=Q(is_completed=True)),
        n=Count("id"),
    )
    return {
        "treasure_total": agg["n"] or 0,
        "treasure_collected": agg["collected"] or 0,
        "treasure_completed": agg["completed"] or 0,
        "treasure_web_available": UserAccount.objects.filter(platform="gc").exists(),
    }
