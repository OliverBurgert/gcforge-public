"""Log submission service — submit logs to GC/OC platforms and store locally."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

# Lazy-init singleton (timezonefinder takes ~0.5s to initialize)
_tf = None


def _get_tf():
    global _tf
    if _tf is None:
        from timezonefinder import TimezoneFinder
        _tf = TimezoneFinder()
    return _tf


def cache_timezone(lat: float, lon: float) -> ZoneInfo:
    """Return the timezone for a cache's coordinates."""
    tz_name = _get_tf().timezone_at(lat=lat, lng=lon)
    return ZoneInfo(tz_name or "UTC")


# Log types that mark a cache as "found"
_FOUND_TYPES = {"Found it", "Attended", "Webcam Photo Taken"}


@dataclass
class LogSubmitResult:
    gc_success: bool | None = None  # None = not attempted
    gc_ref_code: str = ""
    gc_error: str = ""
    oc_success: bool | None = None
    oc_ref_code: str = ""
    oc_error: str = ""
    messages: list[str] = field(default_factory=list)
    image_errors: list[str] = field(default_factory=list)


def submit_log(
    cache,
    log_type: str,
    logged_at: datetime,
    text: str,
    platforms: list[str],
    *,
    sequence_number: int | None = None,
    passphrase: str = "",
    images=(),  # list[ImageAttachment]
    give_favourite: bool = False,
    recommend: bool = False,
) -> LogSubmitResult:
    """Submit a log to the selected platform(s) and store locally.

    Args:
        cache: Geocache instance
        log_type: LogType value (e.g. "Found it")
        logged_at: Aware datetime in UTC
        text: Log text
        platforms: List of platform identifiers to submit to (e.g. ["gc", "oc_de"])
        sequence_number: Optional user-assigned find sequence number
    """
    from geocaches.models import Log

    result = LogSubmitResult()

    # ALC caches cannot be logged via API
    if cache.cache_type == "Adventure Lab":
        result.messages.append(
            "Adventure Lab caches cannot be logged via API. "
            "Use the Geocaching app or refresh to sync found status."
        )
        return result

    iso_str = logged_at.strftime("%Y-%m-%dT%H:%M:%S.000Z")

    # --- GC submission ---
    if "gc" in platforms and cache.gc_code:
        result.gc_success = False
        try:
            from geocaches.sync.gc_client import GCClient
            gc = GCClient()
            resp = gc.submit_log(
                cache.gc_code, log_type, iso_str, text,
                use_favourite_point=give_favourite,
            )
            result.gc_ref_code = resp.get("referenceCode", "")
            result.gc_success = True
            logger.info("Submitted GC log %s for %s", result.gc_ref_code, cache.gc_code)
            if give_favourite:
                cache.user_favorited = True
                cache.save(update_fields=["user_favorited"])
        except Exception as exc:
            result.gc_error = str(exc)
            logger.warning("GC log submission failed for %s: %s", cache.gc_code, exc)

    # --- OC submission ---
    oc_platforms = [p for p in platforms if p.startswith("oc_")]
    if oc_platforms and cache.oc_code:
        result.oc_success = False
        # Pre-check: if the cache requires a passphrase but none was provided, fail early
        oc_ext = getattr(cache, "oc_extension", None)
        req_passwd = getattr(oc_ext, "req_passwd", False)
        if req_passwd and not passphrase:
            result.oc_error = f"Missing passphrase for {cache.oc_code}"
            logger.warning("OC log blocked — passphrase required for %s", cache.oc_code)
        else:
            for plat in oc_platforms:
                try:
                    from geocaches.sync.oc_client import OCClient
                    from accounts.models import UserAccount
                    oc_account = UserAccount.objects.filter(platform=plat).first()
                    user_id = oc_account.user_id if oc_account else ""
                    oc = OCClient(platform=plat, user_id=user_id)
                    resp = oc.submit_log(cache.oc_code, log_type, iso_str, text,
                                         password=passphrase, recommend=recommend)
                    result.oc_ref_code = resp.get("log_uuid") or ""
                    result.oc_success = True
                    logger.info("Submitted OC log %s for %s on %s",
                                result.oc_ref_code, cache.oc_code, plat)
                    # Persist passphrase so it pre-fills next time
                    if passphrase:
                        oc_ext = getattr(cache, "oc_extension", None)
                        if oc_ext and oc_ext.passphrase != passphrase:
                            oc_ext.passphrase = passphrase
                            oc_ext.save(update_fields=["passphrase"])
                    # Update local recommendation status
                    if recommend:
                        oc_ext = getattr(cache, "oc_extension", None)
                        if oc_ext:
                            oc_ext.user_recommended = True
                            oc_ext.save(update_fields=["user_recommended"])
                except Exception as exc:
                    result.oc_error = str(exc)
                    logger.warning("OC log submission failed for %s on %s: %s",
                                   cache.oc_code, plat, exc)

    # --- Upload images (non-fatal) ---
    if images:
        from geocaches.image_upload import process_image, ImageUploadResult
        image_list = list(images)

        if result.gc_success and result.gc_ref_code:
            try:
                from geocaches.sync.gc_client import GCClient
                gc_client = GCClient()
                for att in image_list:
                    try:
                        processed, mime = process_image(att)
                        gc_client.upload_log_image(
                            result.gc_ref_code, processed, mime,
                            name=att.title, description=att.description,
                        )
                        logger.info("GC image uploaded for %s: %s", result.gc_ref_code, att.filename)
                    except Exception as exc:
                        msg = f"GC image '{att.filename}': {exc}"
                        result.image_errors.append(msg)
                        logger.warning("GC image upload failed: %s", msg)
            except Exception as exc:
                result.image_errors.append(f"GC image upload setup failed: {exc}")

        if result.oc_success and result.oc_ref_code:
            oc_plat = oc_platforms[0] if oc_platforms else ""
            try:
                from geocaches.sync.oc_client import OCClient
                from accounts.models import UserAccount
                oc_account = UserAccount.objects.filter(platform=oc_plat).first()
                user_id = oc_account.user_id if oc_account else ""
                oc_client = OCClient(platform=oc_plat, user_id=user_id)
                for att in image_list:
                    try:
                        processed, mime = process_image(att)
                        ok, err = oc_client.upload_log_image(
                            result.oc_ref_code, processed, mime,
                            caption=att.title, is_spoiler=att.is_spoiler,
                        )
                        if not ok:
                            result.image_errors.append(f"OC image '{att.filename}': {err}")
                            logger.warning("OC image upload failed: %s", err)
                        else:
                            logger.info("OC image uploaded for %s: %s", result.oc_ref_code, att.filename)
                    except Exception as exc:
                        msg = f"OC image '{att.filename}': {exc}"
                        result.image_errors.append(msg)
                        logger.warning("OC image upload failed: %s", msg)
            except Exception as exc:
                result.image_errors.append(f"OC image upload setup failed: {exc}")

    # --- Store locally ---
    # Use the first successful source_id, or empty if both failed
    source_id = result.gc_ref_code or result.oc_ref_code or ""
    source = ""
    if result.gc_success:
        source = "gc"
    elif result.oc_success:
        source = oc_platforms[0] if oc_platforms else ""

    if result.gc_success or result.oc_success:
        from preferences.models import UserPreference
        from accounts.models import UserAccount as _UA
        # Use the platform-specific username from UserAccount so the "my logs"
        # filter can match by username.  Fall back to preferences if no account.
        _plat_acc = _UA.objects.filter(platform=source).first()
        if _plat_acc and _plat_acc.username:
            username = _plat_acc.username
        else:
            username = UserPreference.get("gc_username", "") or UserPreference.get("oc_username", "")

        Log.objects.create(
            geocache=cache,
            log_type=log_type,
            user_name=username,
            logged_date=logged_at.date(),
            logged_at=logged_at,
            text=text,
            source_id=source_id,
            source=source,
            is_local=True,
            sequence_number=sequence_number,
        )

        # Update found status
        if log_type in _FOUND_TYPES:
            cache.found = True
            if not cache.found_date or logged_at.date() < cache.found_date:
                cache.found_date = logged_at.date()
            cache.save(update_fields=["found", "found_date"])
    else:
        result.messages.append("Log was not submitted to any platform.")

    return result
