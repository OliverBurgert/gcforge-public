"""
Rate limiting for API sync operations.

RateLimiter: per-second request throttle (thread-safe, cancellation-aware).
QuotaTracker: daily quota management via the SyncQuota model.
"""

import threading
import time
from datetime import date


class RateLimiter:
    """
    Token-bucket rate limiter — enforces a minimum interval between requests.

    Thread-safe. Supports cancellation via a threading.Event so background
    tasks can be stopped without waiting for the full sleep interval.
    """

    def __init__(self, requests_per_second: float = 1.0):
        self._interval = 1.0 / requests_per_second
        self._last = 0.0
        self._lock = threading.Lock()

    def wait(self, cancel_event: threading.Event | None = None) -> bool:
        """
        Block until the next request is allowed.

        Returns True if the wait completed normally, False if cancelled.
        """
        with self._lock:
            now = time.monotonic()
            wait_time = self._interval - (now - self._last)
            if wait_time > 0:
                if cancel_event:
                    if cancel_event.wait(wait_time):
                        return False  # cancelled
                else:
                    time.sleep(wait_time)
            self._last = time.monotonic()
            return True


class QuotaTracker:
    """
    Daily quota tracking backed by the SyncQuota model.

    Provides check/consume operations that are safe for concurrent use
    (uses database-level atomicity via F expressions).
    """

    @staticmethod
    def remaining(platform: str, mode: str) -> int:
        """Return remaining quota for today. Creates the record if needed."""
        from geocaches.models import SyncQuota
        quota, _ = SyncQuota.objects.get_or_create(
            platform=platform, mode=mode, date=date.today(),
            defaults={"used": 0, "limit": QuotaTracker._default_limit(platform, mode)},
        )
        return max(0, quota.limit - quota.used)

    @staticmethod
    def check(platform: str, mode: str, count: int) -> tuple[bool, int]:
        """
        Check if quota allows 'count' more requests.

        Returns (ok, remaining) where ok is True if count <= remaining.
        """
        rem = QuotaTracker.remaining(platform, mode)
        return (count <= rem, rem)

    @staticmethod
    def consume(platform: str, mode: str, count: int) -> None:
        """Record quota usage. Uses F() for atomic increment."""
        from django.db.models import F
        from geocaches.models import SyncQuota
        SyncQuota.objects.filter(
            platform=platform, mode=mode, date=date.today(),
        ).update(used=F("used") + count)

    @staticmethod
    def set_limit(platform: str, mode: str, limit: int) -> None:
        """Update the daily limit (e.g. after refreshing membership level)."""
        from geocaches.models import SyncQuota
        quota, _ = SyncQuota.objects.get_or_create(
            platform=platform, mode=mode, date=date.today(),
            defaults={"used": 0, "limit": limit},
        )
        if quota.limit != limit:
            quota.limit = limit
            quota.save(update_fields=["limit"])

    # Known OC platforms (must match okapi_client._OC_NODES keys)
    _KNOWN_OC_PLATFORMS = {"oc_de", "oc_pl", "oc_uk", "oc_nl", "oc_us"}

    @staticmethod
    def _default_limit(platform: str, mode: str) -> int:
        """Default quota limits. Overridden by set_limit() after membership check."""
        if platform == "gc":
            if mode == "light":
                return 10_000
            # Check stored membership level to pick the right full-mode default
            from accounts.models import UserAccount
            gc_account = UserAccount.objects.filter(platform="gc").first()
            level = gc_account.membership_level if gc_account else 0
            return 16_000 if level >= 2 else 3
        if platform in QuotaTracker._KNOWN_OC_PLATFORMS:
            return 100_000  # OC has no formal per-app daily quota
        raise ValueError(f"Unknown platform: {platform!r}")
