"""
Shared log-fetching functions for GC and OC platforms.

Used by both detail-page per-cache buttons and list-view bulk update.
"""
import logging
from geocaches.models import Log

logger = logging.getLogger(__name__)

_LOG_FIELDS = "referenceCode,geocacheLogType,loggedDate,owner,text"



def fetch_recent_gc_logs(gc_client, code: str, count: int = 50) -> tuple[int, int]:
    """Fetch the N most recent GC logs (skip=0), deduplicating against existing.

    Returns (new_logs_saved, api_count) where api_count is how many the API
    returned (used by callers to decide whether more logs are available).
    """
    from geocaches.models import Geocache
    cache = Geocache.objects.filter(gc_code=code).first()
    if not cache:
        return 0, 0

    existing_ids = set(cache.logs.filter(source="gc").values_list("source_id", flat=True))
    raw_logs = gc_client._api.get(
        f"/geocaches/{code}/geocachelogs",
        fields=_LOG_FIELDS, skip=0, take=count,
    )
    if not raw_logs:
        return 0, 0

    normalized = gc_client._normalize_logs(raw_logs)
    new_logs, upgraded = _dedup_gc_logs(cache, normalized, existing_ids)
    if upgraded:
        logger.info("Upgraded %d GC log source_id(s) from numeric to API ref for %s", upgraded, code)
    saved = _save_logs(cache, new_logs)
    logger.info("fetch_recent_gc_logs %s: API returned %d, saved %d new", code, len(raw_logs), saved)
    return saved, len(raw_logs)


def fetch_more_gc_logs(gc_client, code: str, skip: int = 0, count: int = 50) -> tuple[int, int]:
    """Fetch `count` GC logs starting at `skip` offset.

    Returns (new_logs_saved, api_count).
    """
    from geocaches.models import Geocache
    cache = Geocache.objects.filter(gc_code=code).first()
    if not cache:
        return 0, 0

    existing_ids = set(cache.logs.filter(source="gc").values_list("source_id", flat=True))

    raw_logs = gc_client._api.get(
        f"/geocaches/{code}/geocachelogs",
        fields=_LOG_FIELDS, skip=skip, take=count,
    )
    if not raw_logs:
        return 0, 0

    normalized = gc_client._normalize_logs(raw_logs)
    new_logs, upgraded = _dedup_gc_logs(cache, normalized, existing_ids)
    if upgraded:
        logger.info("Upgraded %d GC log source_id(s) from numeric to API ref for %s", upgraded, code)
    saved = _save_logs(cache, new_logs)
    logger.info("fetch_more_gc_logs %s: skip=%d, API returned %d, saved %d new", code, skip, len(raw_logs), saved)
    return saved, len(raw_logs)


def fetch_all_gc_logs(gc_client, code: str) -> int:
    """Page through ALL logs for a cache until exhausted.

    Returns total number of new logs saved.
    """
    from geocaches.models import Geocache
    cache = Geocache.objects.filter(gc_code=code).first()
    if not cache:
        return 0

    existing_ids = set(cache.logs.filter(source="gc").values_list("source_id", flat=True))
    total_saved = 0
    skip = 0

    while True:
        raw_logs = gc_client._api.get(
            f"/geocaches/{code}/geocachelogs",
            fields=_LOG_FIELDS, skip=skip, take=_BATCH_SIZE,
        )
        if not raw_logs:
            break

        normalized = gc_client._normalize_logs(raw_logs)
        new_logs, upgraded = _dedup_gc_logs(cache, normalized, existing_ids)
        if upgraded:
            logger.info("Upgraded %d GC log source_id(s) from numeric to API ref for %s", upgraded, code)
        if new_logs:
            total_saved += _save_logs(cache, new_logs)
            existing_ids.update(log["source_id"] for log in new_logs)

        skip += len(raw_logs)
        if len(raw_logs) < _BATCH_SIZE:
            break

    logger.info("fetch_all_gc_logs %s: paged %d, saved %d new log(s)", code, skip, total_saved)
    return total_saved


def fetch_oc_logs(oc_client, code: str, count: int = 50) -> int:
    """Fetch N newest OC logs via OKAPI.

    The OKAPI endpoint returns logs embedded in the cache response with lpc=N.
    Dedup is handled by _save_logs.
    Returns the number of new logs saved.
    """
    from geocaches.models import Geocache
    from geocaches.sync.base import SyncMode

    cache = Geocache.objects.filter(oc_code=code).first()
    if not cache:
        return 0

    # OC GPX imports store numeric log IDs as source_id, but the OKAPI
    # returns UUIDs — they never match.  Use (logged_date, user_name) fallback
    # only when one ID is numeric and the other is a UUID.  If both IDs are
    # the same format but differ, keep both (some caches encourage multiple
    # logs on the same day).
    existing_logs = list(
        cache.logs.exclude(source="gc").values_list("source_id", "logged_date", "user_name")
    )
    existing_source_ids = {row[0] for row in existing_logs}
    existing_numeric_date_user = set()
    for sid, dt, uname in existing_logs:
        if sid.isdigit():
            existing_numeric_date_user.add((dt, uname))

    # Fetch cache with logs embedded
    params = {
        "cache_code": code,
        "fields": "code|latest_logs",
        "lpc": str(count),
    }
    raw = oc_client._get("/services/caches/geocache", params)
    raw_logs = raw.get("latest_logs", []) or []
    if not raw_logs:
        return 0

    # Normalize using OC log format
    normalized = []
    from geocaches.sync.oc_client import _LOG_TYPE_MAP
    from datetime import date as _date
    for log in raw_logs:
        log_type = _LOG_TYPE_MAP.get(log.get("type", ""), "Write note")
        date_str = log.get("date", "")[:10] if log.get("date") else ""
        if not date_str:
            continue
        user = log.get("user", {})
        normalized.append({
            "log_type": log_type,
            "logged_date": date_str,
            "user_name": user.get("username", ""),
            "user_id": user.get("uuid", ""),
            "text": log.get("comment", ""),
            "source_id": log.get("uuid", ""),
            "source": oc_client.platform,
        })

    new_logs = []
    upgraded = 0
    for log in normalized:
        if log["source_id"] in existing_source_ids:
            continue
        # Fallback: if this is a UUID and we have a numeric ID for the same
        # (date, user_name), it's the same log from a different source format.
        # Upgrade the existing log's source_id to the correct UUID.
        if not log["source_id"].isdigit():
            try:
                y, m, d = log["logged_date"].split("-")
                log_date = _date(int(y), int(m), int(d))
            except (ValueError, AttributeError):
                log_date = None
            if (log_date, log["user_name"]) in existing_numeric_date_user:
                _upgrade_source_id(cache, log_date, log["user_name"], log["source_id"])
                upgraded += 1
                continue
        new_logs.append(log)
    saved = _save_logs(cache, new_logs)
    if upgraded:
        logger.info("Upgraded %d OC log source_id(s) from numeric to UUID for %s", upgraded, code)
    return saved


_BATCH_SIZE = 50


def ensure_my_gc_logs(gc_client, code: str) -> int:
    """If userData says the user found this cache but we have no local found
    log, page through all logs until we find it (+ one more batch).

    All new logs encountered along the way are saved too.
    Returns total number of new logs saved.
    """
    from geocaches.models import Geocache
    from accounts.models import UserAccount

    cache = Geocache.objects.filter(gc_code=code).first()
    if not cache or not cache.found:
        return 0

    # Collect user identities for matching
    gc_accounts = list(UserAccount.objects.filter(platform="gc"))
    if not gc_accounts:
        return 0
    my_ids = {a.user_id for a in gc_accounts if a.user_id}
    my_names = {a.username for a in gc_accounts if a.username}

    # Already have a found log from me?
    from django.db.models import Q
    finder_q = Q()
    if my_ids:
        finder_q |= Q(user_id__in=my_ids)
    if my_names:
        finder_q |= Q(user_name__in=my_names)
    if cache.logs.filter(source="gc", log_type="Found it").filter(finder_q).exists():
        return 0

    # Page through logs until we find our found log (or exhaust all logs)
    existing_ids = set(cache.logs.filter(source="gc").values_list("source_id", flat=True))
    total_saved = 0
    skip = 0
    found_mine = False
    extra_batch_done = False

    while True:
        raw_logs = gc_client._api.get(
            f"/geocaches/{code}/geocachelogs",
            fields=_LOG_FIELDS, skip=skip, take=_BATCH_SIZE,
        )
        if not raw_logs:
            break

        normalized = gc_client._normalize_logs(raw_logs)
        new_logs, _ = _dedup_gc_logs(cache, normalized, existing_ids)
        if new_logs:
            total_saved += _save_logs(cache, new_logs)
            existing_ids.update(log["source_id"] for log in new_logs)

        # Check if this batch contained our found log
        for log in normalized:
            if log["log_type"] == "Found it":
                fid = log.get("user_id", "")
                fname = log.get("user_name", "")
                if fid in my_ids or fname in my_names:
                    found_mine = True
                    break

        skip += len(raw_logs)

        if found_mine:
            if extra_batch_done:
                break
            # Fetch one more batch to catch potential duplicates
            extra_batch_done = True
            continue

        if len(raw_logs) < _BATCH_SIZE:
            break  # No more logs available

    if total_saved:
        logger.info("ensure_my_gc_logs: saved %d log(s) for %s (paged %d)", total_saved, code, skip)
    return total_saved


def _dedup_gc_logs(cache, normalized: list[dict], existing_ids: set) -> tuple[list[dict], int]:
    """Deduplicate GC API logs against existing DB logs.

    GPX imports store numeric source_ids (e.g. '1350146474'), while the
    API returns reference codes (e.g. 'GL1G5CCX0').  When formats differ,
    fall back to (logged_date, user_name, log_type) matching and upgrade the
    existing record's source_id to the API reference code.

    Returns (new_logs, upgraded_count).
    """
    from datetime import date as _date

    # Build fallback set: (date, user_name, log_type) for logs with numeric source_ids
    existing_logs = list(
        cache.logs.filter(source="gc").values_list("source_id", "logged_date", "user_name", "log_type")
    )
    numeric_date_user_type = set()
    for sid, dt, uname, lt in existing_logs:
        if sid.isdigit():
            numeric_date_user_type.add((dt, uname, lt))

    new_logs = []
    upgraded = 0
    for log in normalized:
        if log["source_id"] in existing_ids:
            continue
        # Fallback: API ref code vs numeric GPX id for same log
        if not log["source_id"].isdigit():
            try:
                y, m, d = log["logged_date"].split("-")
                log_date = _date(int(y), int(m), int(d))
            except (ValueError, AttributeError):
                log_date = None
            if (log_date, log.get("user_name", ""), log["log_type"]) in numeric_date_user_type:
                _upgrade_gc_source_id(
                    cache, log_date, log.get("user_name", ""), log["log_type"], log["source_id"],
                )
                upgraded += 1
                continue
        new_logs.append(log)
    return new_logs, upgraded


def _upgrade_gc_source_id(cache, logged_date, user_name, log_type, new_source_id):
    """Replace a numeric GC source_id with the API reference code."""
    Log.objects.filter(
        geocache=cache,
        source="gc",
        logged_date=logged_date,
        user_name=user_name,
        log_type=log_type,
        source_id__regex=r'^\d+$',
    ).update(source_id=new_source_id)


def _upgrade_source_id(cache, logged_date, user_name, new_source_id):
    """Replace a numeric OC source_id with the correct UUID."""
    Log.objects.filter(
        geocache=cache,
        logged_date=logged_date,
        user_name=user_name,
        source_id__regex=r'^\d+$',
    ).exclude(source="gc").update(source_id=new_source_id)


def _save_logs(cache, logs: list[dict]) -> int:
    """Bulk-create new log entries. Returns count saved."""
    if not logs:
        return 0
    objs = [Log(geocache=cache, **data) for data in logs]
    Log.objects.bulk_create(objs)
    return len(objs)
