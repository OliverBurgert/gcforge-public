"""
Sync service — orchestrates API sync operations.

Connects platform clients (GCClient, OCClient) to save_geocache() with
quota tracking, rate limiting, and background-task integration.
"""

import logging
import re
import threading

from .base import BasePlatformClient, SyncMode, SyncResult
from .rate_limiter import QuotaTracker, RateLimiter

logger = logging.getLogger(__name__)
sync_log = logging.getLogger("geocaches.sync")
_alc_match_log = logging.getLogger("geocaches.sync.alc_match")

# One limiter per platform; GC allows ~1 req/s, OC is more generous.
_rate_limiters: dict[str, RateLimiter] = {}
_rl_lock = threading.Lock()


def _get_limiter(platform: str) -> RateLimiter:
    with _rl_lock:
        if platform not in _rate_limiters:
            rps = 4.0 if platform.startswith("oc") else (2.0 if platform == "al" else 1.0)
            _rate_limiters[platform] = RateLimiter(rps)
        return _rate_limiters[platform]


def sync_caches(
    client: BasePlatformClient,
    codes: list[str],
    mode: SyncMode = SyncMode.LIGHT,
    tag_names: list[str] | None = None,
    cancel_event: threading.Event | None = None,
    task_info=None,
    log_count: int = 5,
) -> SyncResult:
    """
    Fetch caches from a platform API and save them via save_geocache().

    Args:
        client: Platform client (GCClient or OCClient).
        codes: Cache codes to fetch.
        mode: LIGHT (metadata) or FULL (everything).
        tag_names: Optional tags to apply to synced caches.
        cancel_event: Threading event for cancellation.
        task_info: Optional TaskInfo for progress reporting.
        log_count: Max logs per cache in FULL mode (default 5).

    Returns:
        SyncResult with counts and errors.
    """
    from geocaches.services import save_geocache

    result = SyncResult()
    limiter = _get_limiter(client.platform)

    sync_log.info("--- Sync start: %d caches on %s (%s mode)", len(codes), client.platform, mode)

    # Convert tag name strings to Tag objects (get_or_create)
    tag_objs = None
    if tag_names:
        from geocaches.models import Tag
        tag_objs = [Tag.objects.get_or_create(name=n.strip())[0] for n in tag_names if n.strip()]
        sync_log.info("  Tags: %s", ", ".join(tag_names))

    if task_info:
        task_info.total = len(codes)
        task_info.phase = f"Syncing {len(codes)} caches ({mode})"

    # Process in batches
    for i in range(0, len(codes), client.batch_size):
        if cancel_event and cancel_event.is_set():
            break

        batch_codes = codes[i:i + client.batch_size]

        # Quota check
        ok, remaining = QuotaTracker.check(client.platform, mode, 1)
        if not ok:
            msg = f"Daily quota exhausted for {client.platform}/{mode} (0 remaining)"
            logger.warning(msg)
            sync_log.warning("  Quota exhausted for %s/%s — aborting", client.platform, mode)
            result.errors.append(msg)
            result.failed += len(codes) - i
            break

        # Rate limit
        if not limiter.wait(cancel_event):
            break  # cancelled

        # Fetch batch from API
        try:
            normalized = client.get_caches(
                batch_codes, mode, log_count=log_count,
            )
            QuotaTracker.consume(client.platform, mode, 1)
        except Exception as exc:
            msg = f"API error fetching batch {i // client.batch_size}: {exc}"
            logger.error(msg)
            sync_log.error("  %s", msg)
            result.errors.append(msg)
            result.failed += len(batch_codes)
            continue

        # Save each cache
        for data in normalized:
            if cancel_event and cancel_event.is_set():
                break
            try:
                kwargs = dict(data)
                kwargs["fields"] = dict(data["fields"])
                if tag_objs:
                    kwargs["tags"] = tag_objs
                save_result = save_geocache(**kwargs)
                if save_result.created:
                    result.created += 1
                elif save_result.updated:
                    result.updated += 1
                else:
                    result.skipped += 1
                # Ensure user's own logs are present for GC FULL syncs
                if (client.platform == "gc" and mode == SyncMode.FULL
                        and data.get("found")):
                    try:
                        from .log_fetch import ensure_my_gc_logs
                        ensure_my_gc_logs(client, data.get("gc_code", ""))
                    except Exception as log_exc:
                        logger.debug("ensure_my_gc_logs failed for %s: %s",
                                     data.get("gc_code", "?"), log_exc)
            except Exception as exc:
                code = data.get("gc_code") or data.get("oc_code", "?")
                msg = f"Save failed for {code}: {exc}"
                logger.error(msg)
                sync_log.error("  %s", msg)
                result.errors.append(msg)
                result.failed += 1

        if task_info:
            task_info.completed = min(i + len(batch_codes), len(codes))

    sync_log.info(
        "--- Sync done: %s — %d created, %d updated, %d skipped, %d failed (of %d)",
        client.platform, result.created, result.updated, result.skipped, result.failed, len(codes),
    )
    if result.errors:
        sync_log.warning("  Errors: %s", "; ".join(result.errors[:5]))

    if task_info:
        task_info.completed = len(codes)
        task_info.result = {
            "created": result.created,
            "updated": result.updated,
            "skipped": result.skipped,
            "failed": result.failed,
            "errors": result.errors[:20],
        }

    return result


def sync_by_bbox(
    client: BasePlatformClient,
    south: float, west: float, north: float, east: float,
    mode: SyncMode = SyncMode.LIGHT,
    tag_names: list[str] | None = None,
    cancel_event: threading.Event | None = None,
    task_info=None,
    log_count: int = 5,
) -> SyncResult:
    """Search a bounding box for caches, then fetch and save them."""
    if task_info:
        task_info.phase = "Searching area"

    try:
        codes = client.search_by_bbox(south, west, north, east)
    except Exception as exc:
        result = SyncResult()
        result.errors.append(f"Bbox search failed: {exc}")
        result.failed = 1
        return result

    if not codes:
        return SyncResult()

    return sync_caches(
        client, codes, mode,
        tag_names=tag_names,
        cancel_event=cancel_event,
        task_info=task_info,
        log_count=log_count,
    )


def _preview_row(data: dict, platform: str) -> dict:
    """Build a lightweight preview row from a normalized cache dict."""
    fields = data.get("fields", {})
    code = data.get("gc_code") or data.get("oc_code", "")
    return {
        "code": code,
        "name": fields.get("name", ""),
        "lat": fields.get("latitude", 0),
        "lon": fields.get("longitude", 0),
        "type": fields.get("cache_type", ""),
        "size": fields.get("size", ""),
        "difficulty": fields.get("difficulty"),
        "terrain": fields.get("terrain"),
        "status": fields.get("status", ""),
        "found": data.get("found", False),
        "platform": platform,
    }


def _previews_from_lite(platform: str, lite: list[dict], task_info=None) -> list[dict]:
    """Build preview rows from already-normalized lite dicts (search fast path)."""
    previews = [_preview_row(d, platform) for d in lite]
    sync_log.info("--- Preview done (search): %s — %d previews", platform, len(previews))
    if task_info:
        task_info.total = len(previews)
        task_info.completed = len(previews)
        task_info.result = {"caches": previews, "count": len(previews), "errors": []}
    return previews


def _preview_codes(
    client: BasePlatformClient,
    codes: list[str],
    cancel_event: threading.Event | None = None,
    task_info=None,
) -> list[dict]:
    """Fetch lightweight preview data for a list of codes (shared logic)."""
    limiter = _get_limiter(client.platform)

    sync_log.info("--- Preview start: fetching %d caches from %s", len(codes), client.platform)

    if task_info:
        task_info.total = len(codes)
        task_info.phase = f"Fetching previews ({len(codes)} caches)"

    previews: list[dict] = []
    fetch_errors: list[str] = []

    for i in range(0, len(codes), client.batch_size):
        if cancel_event and cancel_event.is_set():
            break

        batch_codes = codes[i:i + client.batch_size]

        # Quota check (light mode)
        ok, remaining = QuotaTracker.check(client.platform, "light", 1)
        if not ok:
            logger.warning("Daily light quota exhausted for %s", client.platform)
            break

        if not limiter.wait(cancel_event):
            break

        try:
            normalized = client.get_caches(batch_codes, SyncMode.LIGHT)
            QuotaTracker.consume(client.platform, "light", 1)
        except Exception as exc:
            msg = str(exc)
            logger.error("Preview fetch error batch %d: %s", i // client.batch_size, msg)
            fetch_errors.append(msg)
            continue

        for data in normalized:
            previews.append(_preview_row(data, client.platform))

        done = min(i + len(batch_codes), len(codes))
        n_batches = (len(codes) + client.batch_size - 1) // client.batch_size
        sync_log.info(
            "  %s batch %d/%d: +%d previews (%d/%d)",
            client.platform, i // client.batch_size + 1, n_batches,
            len(normalized), done, len(codes),
        )
        if task_info:
            task_info.completed = done

    sync_log.info("--- Preview done: %s — %d previews fetched", client.platform, len(previews))
    if fetch_errors:
        sync_log.warning("  Errors: %s", "; ".join(fetch_errors[:5]))

    if task_info:
        task_info.completed = len(codes)
        task_info.result = {
            "caches": previews,
            "count": len(previews),
            "errors": fetch_errors,
        }

    return previews


def _search_region(client, region_type, region_params, task_info=None, max_results=500):
    """Search for codes using the appropriate method for the region type."""
    if task_info:
        task_info.phase = "Searching area"
    if region_type == "circle":
        lat, lon, radius_m = region_params
        sync_log.info("  Searching %s circle (%.4f, %.4f r=%.0fm)", client.platform, lat, lon, radius_m)
        codes = client.search_by_center(lat, lon, radius_m, max_results=max_results)
    else:
        south, west, north, east = region_params
        sync_log.info("  Searching %s rect (%.4f,%.4f → %.4f,%.4f)", client.platform, south, west, north, east)
        codes = client.search_by_bbox(south, west, north, east, max_results=max_results)
    sync_log.info("  Found %d caches", len(codes))
    return codes


def preview_by_bbox(
    client: BasePlatformClient,
    south: float, west: float, north: float, east: float,
    cancel_event: threading.Event | None = None,
    task_info=None,
    max_results: int = 500,
) -> list[dict]:
    """Search a bounding box and return lightweight cache data without saving."""
    if task_info:
        task_info.phase = "Searching area"
    try:
        lite = client.search_lite_by_bbox(
            south, west, north, east, max_results=max_results,
            cancel_event=cancel_event, limiter=_get_limiter(client.platform),
        )
    except Exception as exc:
        sync_log.error("  Search failed on %s: %s", client.platform, exc)
        if task_info:
            task_info.result = {"error": f"Search failed: {exc}"}
        return []
    if lite is not None:
        return _previews_from_lite(client.platform, lite, task_info)
    # Fallback: search codes, then bulk light fetch
    try:
        codes = _search_region(client, "rect", (south, west, north, east), task_info, max_results)
    except Exception as exc:
        sync_log.error("  Search failed on %s: %s", client.platform, exc)
        if task_info:
            task_info.result = {"error": f"Search failed: {exc}"}
        return []
    if not codes:
        if task_info:
            task_info.total = 0
            task_info.completed = 0
            task_info.result = {"caches": [], "count": 0}
        return []
    return _preview_codes(client, codes, cancel_event, task_info)


def preview_by_center(
    client: BasePlatformClient,
    lat: float, lon: float, radius_m: float,
    cancel_event: threading.Event | None = None,
    task_info=None,
    max_results: int = 500,
) -> list[dict]:
    """Search by center + radius and return lightweight cache data without saving.

    Prefers the search fast path (lite data straight from the search endpoint,
    stopping at the radius). Falls back to search + light detail fetch for
    platforms whose search can't supply the preview fields.
    """
    if task_info:
        task_info.phase = "Searching area"
    try:
        lite = client.search_lite_by_center(
            lat, lon, radius_m, max_results=max_results,
            cancel_event=cancel_event, limiter=_get_limiter(client.platform),
        )
    except Exception as exc:
        sync_log.error("  Search failed on %s: %s", client.platform, exc)
        if task_info:
            task_info.result = {"error": f"Search failed: {exc}"}
        return []
    if lite is not None:
        return _previews_from_lite(client.platform, lite, task_info)
    # Fallback: search codes, then bulk light fetch
    try:
        codes = _search_region(client, "circle", (lat, lon, radius_m), task_info, max_results)
    except Exception as exc:
        sync_log.error("  Search failed on %s: %s", client.platform, exc)
        if task_info:
            task_info.result = {"error": f"Search failed: {exc}"}
        return []
    if not codes:
        if task_info:
            task_info.total = 0
            task_info.completed = 0
            task_info.result = {"caches": [], "count": 0}
        return []
    return _preview_codes(client, codes, cancel_event, task_info)


def preview_by_boxes(
    client: BasePlatformClient,
    searches: list[dict],
    cancel_event: threading.Event | None = None,
    task_info=None,
    max_results_per_box: int = 500,
) -> list[dict]:
    """Search multiple regions (rect or circle) with deduplication, return lightweight cache data.

    Each entry in searches is {'type': 'rect', 's', 'w', 'n', 'e'}
    or {'type': 'circle', 'lat', 'lon', 'radius_m'}.
    """
    all_codes: set[str] = set()
    n = len(searches)
    for i, search in enumerate(searches):
        if cancel_event and cancel_event.is_set():
            break
        if task_info:
            task_info.phase = f"Searching area {i + 1}/{n}"
        try:
            if search["type"] == "circle":
                sync_log.info(
                    "  Area %d/%d: %s circle (%.4f,%.4f r=%.0fm)",
                    i + 1, n, client.platform, search["lat"], search["lon"], search["radius_m"],
                )
                codes = client.search_by_center(
                    search["lat"], search["lon"], search["radius_m"],
                    max_results=max_results_per_box,
                )
            else:
                sync_log.info(
                    "  Area %d/%d: %s bbox (%.4f,%.4f → %.4f,%.4f)",
                    i + 1, n, client.platform, search["s"], search["w"], search["n"], search["e"],
                )
                codes = client.search_by_bbox(
                    search["s"], search["w"], search["n"], search["e"],
                    max_results=max_results_per_box,
                )
            prev_unique = len(all_codes)
            all_codes.update(codes)
            sync_log.info("    Found %d codes (+%d unique)", len(codes), len(all_codes) - prev_unique)
        except Exception as exc:
            sync_log.error("    Area %d/%d search failed on %s: %s", i + 1, n, client.platform, exc)

    if not all_codes:
        if task_info:
            task_info.total = 0
            task_info.completed = 0
            task_info.result = {"caches": [], "count": 0}
        return []

    sync_log.info("  Search done: %d unique codes across %d areas", len(all_codes), n)
    return _preview_codes(client, list(all_codes), cancel_event, task_info)


def preview_by_criteria(
    client: BasePlatformClient,
    criteria: dict,
    bbox: tuple[float, float, float, float] | None = None,
    cancel_event: threading.Event | None = None,
    task_info=None,
    max_results: int = 500,
) -> list[dict]:
    """Search by attribute criteria (owner/type/D-T/…) and return lightweight
    cache data without saving.

    Prefers the lite fast path (rows straight from the search endpoint, e.g. GC
    web search/v2). Falls back to code search + bulk LIGHT fetch (e.g. OKAPI
    search/all, which returns codes only).
    """
    if task_info:
        task_info.phase = "Searching"
    try:
        lite = client.search_criteria_lite(
            criteria, bbox=bbox, max_results=max_results,
            cancel_event=cancel_event, limiter=_get_limiter(client.platform),
        )
    except Exception as exc:
        sync_log.error("  Criteria search failed on %s: %s", client.platform, exc)
        if task_info:
            task_info.result = {"error": f"Search failed: {exc}"}
        return []
    if lite is not None:
        return _previews_from_lite(client.platform, lite, task_info)
    # Fallback: codes search → bulk light fetch
    try:
        codes = client.search_criteria(criteria, bbox=bbox, max_results=max_results)
    except Exception as exc:
        sync_log.error("  Criteria search failed on %s: %s", client.platform, exc)
        if task_info:
            task_info.result = {"error": f"Search failed: {exc}"}
        return []
    if not codes:
        if task_info:
            task_info.total = 0
            task_info.completed = 0
            task_info.result = {"caches": [], "count": 0}
        return []
    return _preview_codes(client, codes, cancel_event, task_info)


def check_quota(platform: str, mode: SyncMode, count: int) -> tuple[bool, int]:
    """Check if quota allows 'count' more requests. Returns (ok, remaining)."""
    return QuotaTracker.check(platform, mode, count)


def consume_quota(platform: str, mode: SyncMode, count: int) -> None:
    """Record quota usage."""
    QuotaTracker.consume(platform, mode, count)


def _backfill_al_stage_dates(guids: list[str], task_info=None) -> int:
    """Best-effort backfill of per-stage found_date for *guids* from the labs log
    history.  Returns the number of stages updated.

    No-op (and no GC website login) when there is no GC account with a player
    GUID, or when none of the synced adventures have a found-but-dateless stage.
    Never raises — a missing password or failed labs login just leaves the
    existing "Sync Adventure Lab stage dates" two-step flow intact.
    """
    if not guids:
        return 0
    try:
        from accounts.models import UserAccount
        from geocaches.models import Geocache

        gc_account = UserAccount.objects.filter(platform="gc").first()
        if not gc_account or not gc_account.user_id:
            return 0
        has_targets = Geocache.objects.filter(
            adventure__adventure_guid__in=guids,
            al_detail__isnull=False,
            found=True,
            found_date__isnull=True,
        ).exists()
        if not has_targets:
            return 0

        if task_info is not None:
            task_info.phase = "Fetching Adventure Lab stage dates"

        from gcprivate.labs_client import LabsClient

        updated = LabsClient().sync_stage_dates_for_adventures(gc_account.user_id, guids)
        if updated:
            sync_log.info("AL stage-date backfill: set %d found_date(s)", updated)
        return updated
    except Exception as exc:
        logger.warning("AL stage-date backfill skipped: %s", exc)
        sync_log.warning("AL stage-date backfill skipped: %s", exc)
        return 0


def sync_al_by_guids(
    guids: list[str],
    tags=None,
    cancel_event: threading.Event | None = None,
    task_info=None,
) -> SyncResult:
    """Fetch and save Adventure Lab adventures by GUID list."""
    from geocaches.models import Tag
    from geocaches.services.save_alc import save_adventure_from_api
    from gcprivate.al_client import ALClient

    result = SyncResult()
    client = ALClient()
    limiter = _get_limiter("al")

    # Resolve tag name strings to Tag objects (same as sync_caches)
    tag_objs = None
    if tags:
        tag_objs = [Tag.objects.get_or_create(name=n.strip())[0] for n in tags if n and n.strip()]

    if task_info:
        task_info.total = len(guids)
        task_info.phase = f"Syncing {len(guids)} Adventure Lab adventures"

    sync_log.info("--- AL sync start: %d adventures", len(guids))

    for i, guid in enumerate(guids):
        if cancel_event and cancel_event.is_set():
            break
        if not limiter.wait(cancel_event):
            break
        try:
            data = client.get_adventure(guid)
            _, stats = save_adventure_from_api(data, tags=tag_objs)
            result.created += stats.created
            result.updated += stats.updated
            if stats.errors:
                result.errors.extend(stats.errors)
        except Exception as exc:
            msg = f"AL fetch failed for {guid}: {exc}"
            logger.error(msg)
            sync_log.error("  %s", msg)
            result.errors.append(msg)
            result.failed += 1
        if task_info:
            task_info.completed = i + 1

    # Backfill per-stage found_date for freshly-found stages from the labs log
    # history.  Best-effort: no-op (and no GC website login) when there's no GC
    # account or no found-but-dateless stages among the synced adventures.
    backfilled = _backfill_al_stage_dates(guids, task_info=task_info)

    sync_log.info(
        "--- AL sync done: %d created, %d updated, %d failed, %d stage date(s) backfilled",
        result.created, result.updated, result.failed, backfilled,
    )
    if task_info:
        task_info.result = {
            "created": result.created,
            "updated": result.updated,
            "failed": result.failed,
            "errors": result.errors[:20],
        }
    return result


def _normalise_for_match(s: str) -> str:
    import unicodedata
    # NFKD decomposes accented chars into base + combining mark (é → e + ́),
    # then ASCII encoding drops the combining marks (René → Rene).
    # Remaining non-ASCII (emojis etc.) are also dropped.
    # '?' is then removed because GSAK uses it as a substitution placeholder.
    decomposed = unicodedata.normalize("NFKD", s or "")
    ascii_only = decomposed.encode("ascii", errors="ignore").decode("ascii")
    no_placeholders = re.sub(r"\?+", " ", ascii_only)
    return re.sub(r"\s+", " ", no_placeholders.strip()).lower()


def sync_al_no_guid_adventure(adv, client) -> "object | None":
    """For one Adventure with no GUID: search the AL API in a 10 m radius and
    match by normalised title + owner.

    Matching logic
    --------------
    1. POST /public/adventures/search with a 10 m radius around the adventure's
       coordinates (falls back to the parent Geocache's coords if Adventure has none).
    2. For each GUID returned, fetch the full adventure.
    3. Compare normalised title AND owner — both must match exactly.
    4. On match:
       a. If a canonical Adventure (same GUID) already exists in the DB, call
          merge_duplicate_adventure() to transfer found status + tags, then delete
          the old record.
       b. Otherwise stamp the GUID onto the existing Adventure so the normal GUID
          path takes over (code correction handled by _get_or_create_adventure).
    5. Call save_adventure_from_api() to update all fields from the API response.

    Returns ImportStats on match, None on no-match.

    Logging
    -------
    - INFO  when a match is found (one line per candidate, one summary line).
    - WARNING when no match is found or the location search fails.
    """
    from geocaches.models import Adventure
    from geocaches.lc_code import uuid_to_lc_code
    from geocaches.services.save_alc import merge_duplicate_adventure, save_adventure_from_api

    lat = adv.latitude
    lon = adv.longitude
    if lat is None or lon is None:
        parent = adv.stages.filter(al_detail__isnull=True).first()
        if parent:
            lat, lon = parent.latitude, parent.longitude

    if lat is None or lon is None:
        _alc_match_log.warning(
            "ALC %s: skipping no-GUID location search — no coordinates available", adv.code
        )
        return None

    try:
        guids = client.search_by_circle(lat, lon, radius_m=10)
    except Exception as exc:
        _alc_match_log.warning("ALC %s: location search failed: %s", adv.code, exc)
        return None

    local_title = _normalise_for_match(adv.title)
    local_owner = _normalise_for_match(adv.owner)

    for guid in guids:
        try:
            data = client.get_adventure(guid)
        except Exception as exc:
            _alc_match_log.warning(
                "ALC %s: failed to fetch candidate %s: %s", adv.code, guid, exc
            )
            continue

        api_title = _normalise_for_match(data.get("title", ""))
        api_owner = _normalise_for_match(data.get("owner", ""))

        if api_title != local_title or api_owner != local_owner:
            continue

        canonical_code = uuid_to_lc_code(guid)
        _alc_match_log.info(
            "ALC %s: matched GUID %s → canonical %s (title=%r, owner=%r)",
            adv.code, guid, canonical_code, data["title"], data["owner"],
        )

        # Check for a pre-existing canonical Adventure in the DB
        canonical_adv = (
            Adventure.objects.filter(adventure_guid=guid).exclude(pk=adv.pk).first()
        )
        if canonical_adv:
            _alc_match_log.info(
                "ALC %s: merging into existing canonical adventure %s — transferring data, "
                "deleting old record",
                adv.code, canonical_code,
            )
            merge_duplicate_adventure(adv, canonical_adv)
            # adv is now deleted; save_adventure_from_api will update canonical_adv
        else:
            # Stamp the GUID onto the existing Adventure so the normal GUID path
            # picks it up; _get_or_create_adventure will correct the code + geocache
            # al_codes if the canonical code differs from the current one.
            adv.adventure_guid = guid
            adv.save(update_fields=["adventure_guid"])

        saved_adv, stats = save_adventure_from_api(data)
        return stats, saved_adv.code

    _alc_match_log.warning(
        "ALC %s: no match found in 10 m radius (searched %d candidate(s); "
        "local title=%r, owner=%r)",
        adv.code, len(guids), adv.title, adv.owner,
    )
    return None


def sync_al_in_bbox(
    south: float, west: float, north: float, east: float,
    tags=None,
    cancel_event: threading.Event | None = None,
    task_info=None,
) -> SyncResult:
    """Refresh already-known AL adventures whose parent lat/lon falls in the bbox."""
    from geocaches.models import Adventure

    adventures = list(
        Adventure.objects
        .filter(
            adventure_guid__gt="",
            latitude__gte=south, latitude__lte=north,
            longitude__gte=west, longitude__lte=east,
        )
        .values_list("adventure_guid", flat=True)
    )
    if not adventures:
        return SyncResult()
    return sync_al_by_guids(adventures, tags=tags, cancel_event=cancel_event, task_info=task_info)


def sync_al_by_circles(
    circles: list[dict],
    tags=None,
    cancel_event: threading.Event | None = None,
    task_info=None,
) -> SyncResult:
    """Discover and fetch AL adventures in the given circles.

    Each circle: {'lat': float, 'lon': float, 'radius_m': int}
    Deduplicates across circles before fetching.
    """
    from gcprivate.al_client import ALClient

    client = ALClient()
    result = SyncResult()
    all_guids: set[str] = set()
    n = len(circles)

    for i, circle in enumerate(circles):
        if cancel_event and cancel_event.is_set():
            break
        sync_log.info(
            "  AL search %d/%d: (%.4f,%.4f r=%dm)",
            i + 1, n, circle["lat"], circle["lon"], circle.get("radius_m", 100_000),
        )
        try:
            guids = client.search_by_circle(
                circle["lat"], circle["lon"],
                circle.get("radius_m", 100_000),
            )
            prev = len(all_guids)
            all_guids.update(guids)
            sync_log.info("    Found %d (+%d new)", len(guids), len(all_guids) - prev)
        except Exception as exc:
            msg = f"Circle {i + 1}/{n} search failed: {exc}"
            sync_log.error("    %s", msg)
            result.errors.append(msg)

    if not all_guids:
        return result
    fetch_result = sync_al_by_guids(list(all_guids), tags=tags, cancel_event=cancel_event, task_info=task_info)
    result.created += fetch_result.created
    result.updated += fetch_result.updated
    result.failed += fetch_result.failed
    result.errors.extend(fetch_result.errors)
    return result


def sync_my_al_founds(
    player_guid: str,
    include_completed: bool = True,
    include_partial: bool = True,
    include_unstarted: bool = False,
    tags=None,
    cancel_event: threading.Event | None = None,
    task_info=None,
) -> SyncResult:
    """Fetch all AL adventures the player has interacted with."""
    from gcprivate.al_client import ALClient

    if not player_guid:
        result = SyncResult()
        result.errors.append("No player GUID configured — set it in Settings > Accounts.")
        return result

    client = ALClient()
    try:
        guids = client.get_player_adventures(
            player_guid,
            include_completed=include_completed,
            include_partial=include_partial,
            include_unstarted=include_unstarted,
        )
    except Exception as exc:
        result = SyncResult()
        result.errors.append(f"Failed to fetch player adventures: {exc}")
        return result

    if not guids:
        return SyncResult()
    return sync_al_by_guids(guids, tags=tags, cancel_event=cancel_event, task_info=task_info)


def _save_retired_adventure_from_logs(entry: dict, tag_objs):
    """Build a minimal adventure (parent + logged stages) from log-history data.

    Used when get_adventure() 404s — the adventure is retired/deleted and the AL
    API has no record of it. The log history carries no coordinates, so the parent
    and stages are placed at 0,0 and the adventure is marked Archived. Only the
    stages the player actually logged are created (the full stage list is unknown).
    """
    from geocaches.services.save_alc import save_adventure_from_api

    logs = entry["logs"]
    data = {
        "adventure_guid": entry["adventure_guid"],
        "title":          entry["adventure_title"],
        "description":    "",
        "owner":          "",
        "lat":            0.0,
        "lon":            0.0,
        "status":         "Archived",
        "themes":         [],
        "stage_count":    len(logs),
        "key_image_url":  entry.get("key_image_url", ""),
        "stages": [
            {
                "stage_uuid":         lg["stage_uuid"],
                "stage_number":       idx + 1,
                "name":               lg["stage_title"],
                "lat":                0.0,
                "lon":                0.0,
                "question":           "",
                "description":        "",
                "answer_hash":        "",
                "answer_code_hashes": [],
                "choices":            [],
                "is_complete":        True,
                "key_image_url":      lg.get("key_image_url", ""),
                "geofencing_radius":  None,
                "challenge_type":     "",
                "is_final":           None,
                "journal_message":    "",
                "journal_image_url":  "",
            }
            for idx, lg in enumerate(logs)
        ],
    }
    return save_adventure_from_api(data, tags=tag_objs)


def _stamp_found_from_logs(entry: dict) -> None:
    """Mark every logged stage of *entry* found, with found_date from the log.

    Matches stages by stage UUID (case-insensitively) within the adventure, so it
    works for both the API-created and fallback-created records. Leaves an already
    correct found/found_date untouched.
    """
    from geocaches.models import Geocache
    from geocaches.services.adventures import recompute_adventure_completed

    uuid_to_date = {lg["stage_uuid"]: lg["found_date"] for lg in entry["logs"]}
    guid = entry["adventure_guid"]

    stages = (
        Geocache.objects.filter(adventure__adventure_guid__iexact=guid, al_detail__isnull=False)
        .select_related("al_detail", "adventure")
    )
    adv = None
    for gc in stages:
        adv = gc.adventure or adv
        uuid = (gc.al_detail.al_stage_uuid or "").lower()
        if uuid not in uuid_to_date:
            continue
        changed = []
        if not gc.found:
            gc.found = True
            changed.append("found")
        d = uuid_to_date[uuid]
        if d and not gc.found_date:
            gc.found_date = d
            changed.append("found_date")
        if changed:
            gc.save(update_fields=changed)
    if adv:
        recompute_adventure_completed(adv)


def import_al_founds_from_logs(
    account_guid: str,
    tags=None,
    cancel_event: threading.Event | None = None,
    task_info=None,
) -> SyncResult:
    """Recover AL stages the player has found that are missing from the DB.

    Reads the player's labs.geocaching.com log history (which includes retired
    adventures absent from the search API). For every adventure with at least one
    found stage not yet in the DB, the whole adventure is created:

      - If the adventure still resolves via the AL API → fetch + save it fully
        (parent + all stages, with coordinates).
      - If it 404s (retired/deleted) → build a minimal record from the log data
        (parent + the logged stages at 0,0, status Archived).

    Then found=True + found_date are stamped on every logged stage.
    """
    from geocaches.models import ALStageDetail, Tag
    from geocaches.services.save_alc import save_adventure_from_api
    from gcprivate.al_client import ALClient
    from gcprivate.labs_client import LabsClient

    result = SyncResult()
    if not account_guid:
        result.errors.append("No GC account GUID configured — connect your GC account in Settings.")
        result.failed = 1
        return result

    tag_objs = None
    if tags:
        tag_objs = [Tag.objects.get_or_create(name=n.strip())[0] for n in tags if n and n.strip()]

    if task_info:
        task_info.phase = "Reading Adventure Lab log history"

    try:
        grouping = LabsClient().fetch_found_grouping(account_guid)
    except Exception as exc:
        sync_log.error("AL recover: log history fetch failed: %s", exc)
        result.errors.append(f"Failed to read log history: {exc}")
        result.failed = 1
        return result

    # Stage UUIDs already present in the DB.
    existing = {
        (u or "").lower()
        for u in ALStageDetail.objects.exclude(al_stage_uuid="").values_list("al_stage_uuid", flat=True)
    }

    # Only adventures with at least one logged stage missing from the DB.
    todo = [e for e in grouping if any(lg["stage_uuid"] not in existing for lg in e["logs"])]

    sync_log.info("AL recover: %d adventure(s) with missing found stages (of %d logged)",
                  len(todo), len(grouping))

    al = ALClient()
    limiter = _get_limiter("al")
    if task_info:
        task_info.total = len(todo)
        task_info.phase = f"Recovering {len(todo)} adventure(s)"

    for i, entry in enumerate(todo):
        if cancel_event and cancel_event.is_set():
            break
        guid = entry["adventure_guid"]

        # Prefer the rich API path; fall back to log-only data when retired (404).
        data = None
        if guid:
            if not limiter.wait(cancel_event):
                break
            try:
                data = al.get_adventure(guid)
            except Exception as exc:
                if "404" not in str(exc):
                    sync_log.warning("AL recover: get_adventure(%s) failed: %s", guid, exc)

        try:
            if data and data.get("stages"):
                _, stats = save_adventure_from_api(data, tags=tag_objs)
            else:
                _, stats = _save_retired_adventure_from_logs(entry, tag_objs)
            result.created += stats.created
            result.updated += stats.updated
            if stats.errors:
                result.errors.extend(stats.errors)

            _stamp_found_from_logs(entry)
        except Exception as exc:
            label = guid or entry.get("adventure_title", "?")
            sync_log.error("AL recover: save failed for %s: %s", label, exc)
            result.errors.append(f"{label}: {exc}")
            result.failed += 1

        if task_info:
            task_info.completed = i + 1

    sync_log.info("AL recover done: %d created, %d updated, %d failed",
                  result.created, result.updated, result.failed)
    if task_info:
        task_info.result = {
            "created": result.created,
            "updated": result.updated,
            "failed": result.failed,
            "errors": result.errors[:20],
        }
    return result


def refresh_membership_level() -> int:
    """
    Call GC API GET /users/me to check membership level.
    Updates the UserAccount and adjusts quota limits accordingly.

    Returns the membership level id (0-3).
    """
    from accounts.models import UserAccount
    from gcprivate.gc_client import GCClient

    client = GCClient()
    raw = client._api.get("/users/me", fields="membershipLevelId")
    level = raw.get("membershipLevelId", 0)

    # Update account
    gc_account = UserAccount.objects.filter(platform="gc").first()
    if gc_account and gc_account.membership_level != level:
        gc_account.membership_level = level
        gc_account.save(update_fields=["membership_level"])

    # Adjust full-mode quota based on membership
    if level >= 2:  # Charter or Premium
        QuotaTracker.set_limit("gc", "full", 16_000)
    else:
        QuotaTracker.set_limit("gc", "full", 3)

    return level
