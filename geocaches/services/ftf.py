"""FTF (First-To-Find) detection service.

Pure, HTTP-free functions that produce FTF candidate rows for the
tools_ftf_markers view and re-verify a single cache after fetching logs.
"""
import logging
from dataclasses import dataclass
from typing import Optional


from ..models import Geocache, Log

logger = logging.getLogger(__name__)


@dataclass
class FTFCandidate:
    cache: Geocache
    reasons: list
    current_ftf: bool
    suggestion: Optional[str]
    needs_verify: bool
    verified: bool = False
    verify_msg: str = ""

    def as_dict(self):
        return {
            "cache": self.cache,
            "reasons": self.reasons,
            "current_ftf": self.current_ftf,
            "suggestion": self.suggestion,
            "needs_verify": self.needs_verify,
            "verified": self.verified,
            "verify_msg": self.verify_msg,
        }


def detect_ftf_candidates(found_caches, finder_q):
    """Scan found caches for FTF candidates.

    Three sources:
      1. Caches already flagged ftf=True
      2. User's found logs containing "[FTF]"
      3. User's found log logged on the same date as the earliest found log
    """
    from geocaches.filters import FOUND_LOG_TYPES

    items_by_pk = {}
    order = []

    def _add_reason(cache, reason, suggestion_if_unset=None, needs_verify=False):
        existing = items_by_pk.get(cache.pk)
        if existing:
            if reason not in existing.reasons:
                existing.reasons.append(reason)
            if suggestion_if_unset and not existing.current_ftf and existing.suggestion is None:
                existing.suggestion = suggestion_if_unset
            return
        cand = FTFCandidate(
            cache=cache,
            reasons=[reason],
            current_ftf=cache.ftf,
            suggestion=suggestion_if_unset if not cache.ftf else None,
            needs_verify=needs_verify,
        )
        items_by_pk[cache.pk] = cand
        order.append(cache.pk)

    # 1) Already flagged
    for cache in found_caches.filter(ftf=True):
        _add_reason(cache, "Flag already set")

    # 2) "[FTF]" in user's own found-log text
    ftf_text_logs = Log.objects.filter(
        finder_q,
        log_type__in=FOUND_LOG_TYPES,
        text__icontains="[ftf]",
        geocache__found=True,
    ).select_related("geocache")
    for log in ftf_text_logs:
        _add_reason(log.geocache, "[FTF] in log text", suggestion_if_unset="set")

    # 3) User's found log same-day as earliest found log
    my_found_logs = (
        Log.objects.filter(
            finder_q,
            log_type__in=FOUND_LOG_TYPES,
            geocache__found=True,
        )
        .select_related("geocache")
        .order_by("geocache_id", "logged_date")
    )
    checked_cache_ids = set()
    for log in my_found_logs:
        if log.geocache_id in checked_cache_ids:
            continue
        checked_cache_ids.add(log.geocache_id)

        earliest = (
            Log.objects.filter(
                geocache_id=log.geocache_id,
                log_type__in=FOUND_LOG_TYPES,
            )
            .order_by("logged_date")
            .first()
        )
        if earliest and log.logged_date == earliest.logged_date:
            _add_reason(
                log.geocache, "First found log",
                suggestion_if_unset="set", needs_verify=True,
            )

    items = [items_by_pk[pk] for pk in order]
    items.sort(key=lambda c: c.cache.display_code)
    return items


def _build_finder_q():
    """Return (q, has_accounts) matching any of the user's known ids/usernames."""
    from ..query import mine_finder_q
    return mine_finder_q()


def fetch_logs_for_verification(cache):
    """Fetch all logs for a cache from its source platform. Returns number saved."""
    from accounts.models import UserAccount

    try:
        if cache.gc_code and cache.gc_code.startswith("GC"):
            from gcprivate.gc_client import GCClient
            from geocaches.sync.log_fetch import fetch_all_gc_logs
            client = GCClient()
            return fetch_all_gc_logs(client, cache.gc_code)
        if cache.oc_code:
            from geocaches.sync.oc_client import OCClient
            from geocaches.sync.log_fetch import fetch_oc_logs
            platform = cache.primary_source or cache.oc_platform or "oc_de"
            acct = UserAccount.objects.filter(platform=platform).first()
            user_id = acct.user_id if acct else ""
            client = OCClient(platform=platform, user_id=user_id)
            return fetch_oc_logs(client, cache.oc_code, count=1000)
    except Exception as exc:
        logger.warning("Log fetch for FTF verification failed (%s): %s",
                        cache.display_code, exc)
        return 0
    return 0


def reverify_ftf_for_cache(cache, finder_q, saved_log_count):
    """Re-evaluate FTF reasons for a single cache after log re-fetch."""
    from geocaches.filters import FOUND_LOG_TYPES

    reasons = []
    if cache.ftf:
        reasons.append("Flag already set")

    has_ftf_text = Log.objects.filter(
        finder_q,
        geocache=cache,
        log_type__in=FOUND_LOG_TYPES,
        text__icontains="[ftf]",
    ).exists()
    if has_ftf_text:
        reasons.append("[FTF] in log text")

    my_log = (
        Log.objects.filter(finder_q, geocache=cache, log_type__in=FOUND_LOG_TYPES)
        .order_by("logged_date")
        .first()
    )
    earliest = (
        Log.objects.filter(geocache=cache, log_type__in=FOUND_LOG_TYPES)
        .order_by("logged_date")
        .first()
    )
    is_first = my_log and earliest and my_log.logged_date == earliest.logged_date
    if is_first:
        reasons.append("First found log")

    suggestion = None
    if reasons and not cache.ftf:
        suggestion = "set"
    elif not reasons and cache.ftf:
        suggestion = "unset"

    status_msg = ""
    if is_first:
        status_msg = (
            f"Verified ({saved_log_count} new log(s) fetched)"
            if saved_log_count else "Verified (no new logs)"
        )
    elif my_log and earliest:
        status_msg = "Not first — earlier log exists"
        if not cache.ftf and not has_ftf_text:
            reasons = []
            suggestion = None

    return FTFCandidate(
        cache=cache,
        reasons=reasons,
        current_ftf=cache.ftf,
        suggestion=suggestion,
        needs_verify=False,
        verified=True,
        verify_msg=status_msg,
    )
