"""
Sync service — orchestrates API sync operations.

Connects platform clients (GCClient, OCClient) to save_geocache() with
quota tracking, rate limiting, and background-task integration.
"""

import logging
import threading

from .base import BasePlatformClient, SyncMode, SyncResult
from .rate_limiter import QuotaTracker, RateLimiter

logger = logging.getLogger(__name__)
sync_log = logging.getLogger("geocaches.sync")

# One limiter per platform; GC allows ~1 req/s, OC is more generous.
_rate_limiters: dict[str, RateLimiter] = {}
_rl_lock = threading.Lock()


def _get_limiter(platform: str) -> RateLimiter:
    with _rl_lock:
        if platform not in _rate_limiters:
            rps = 4.0 if platform.startswith("oc") else 1.0
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
            fields = data.get("fields", {})
            code = data.get("gc_code") or data.get("oc_code", "")
            previews.append({
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
                "platform": client.platform,
            })

        if task_info:
            task_info.completed = min(i + len(batch_codes), len(codes))

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
    """Search by center + radius and return lightweight cache data without saving."""
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


def check_quota(platform: str, mode: SyncMode, count: int) -> tuple[bool, int]:
    """Check if quota allows 'count' more requests. Returns (ok, remaining)."""
    return QuotaTracker.check(platform, mode, count)


def consume_quota(platform: str, mode: SyncMode, count: int) -> None:
    """Record quota usage."""
    QuotaTracker.consume(platform, mode, count)


def refresh_membership_level() -> int:
    """
    Call GC API GET /users/me to check membership level.
    Updates the UserAccount and adjusts quota limits accordingly.

    Returns the membership level id (0-3).
    """
    from accounts.models import UserAccount
    from geocaches.sync.gc_client import GCClient

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
