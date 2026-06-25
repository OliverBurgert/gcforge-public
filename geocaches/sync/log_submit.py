"""Log submission service — submit logs to GC/OC platforms and store locally."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from zoneinfo import ZoneInfo

from geocaches.models import FOUND_LOG_TYPES as _FOUND_TYPES

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


# _FOUND_TYPES (found-state log types) imported above — single source of truth:
# geocaches.models.FOUND_LOG_TYPES (LogType.found_types()).

# Log types that mark a cache as DNFed
_DNF_TYPES = {"Didn't find it"}


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
    tb_results: list["TrackableLogSubmitResult"] = field(default_factory=list)


@dataclass
class TrackableLogSubmitResult:
    ref_code: str
    action: str              # discover | retrieve | drop | grab
    success: bool = False
    source_id: str = ""
    error: str = ""


_TB_ACTION_TO_LOG_TYPE: dict[str, str] = {
    "discover": "Discovered It",
    "retrieve": "Retrieve It from a Cache",
    "drop":     "Dropped Off",
    "grab":     "Grab It (Not from a Cache)",
    "visit":    "Visited",
}

# Actions that require knowing the private tracking code on submit.
# "visit" doesn't need a tracking code from the user — the TB must already
# be in our inventory, which means GC accepts the request without one.
# "drop" likewise needs no tracking code (we're putting it down, not taking it).
_TB_ACTIONS_REQUIRING_TRACKING = frozenset({"discover", "retrieve", "grab"})

# Actions that expand into a multi-step chain.
# Local holder_state to set after a successful action (actions not listed here
# leave holder_state unchanged — discover/visit don't move the TB).
_TB_ACTION_STATE: dict[str, str] = {
    "drop":     "in_cache",
    "retrieve": "held_by_user",
    "grab":     "held_by_user",
}

_TB_CHAIN_EXPANSIONS: dict[str, list[str]] = {
    # Grab-then-drop-then-retrieve records the current cache in the TB's
    # travel history while still leaving the TB in our inventory.
    "grab_chain": ["grab", "drop", "retrieve"],
}


def _update_local_tb_state(ref: str, action: str, cache) -> None:
    """Update holder_state (and location fields) on the local Trackable row."""
    new_state = _TB_ACTION_STATE.get(action)
    if new_state is None:
        return

    from geocaches.models import Trackable
    tb = Trackable.objects.filter(reference_code=ref).first()
    if tb is None:
        return

    update_fields = ["holder_state", "updated_at"]
    tb.holder_state = new_state

    if action == "drop":
        tb.current_geocache = cache if (cache and cache.pk) else None
        tb.current_geocache_code = cache.gc_code or ""
        tb.current_geocache_name = cache.name or ""
        tb.current_holder_name = ""
        update_fields += ["current_geocache", "current_geocache_code",
                          "current_geocache_name", "current_holder_name"]
        if not tb.coords_user_override:
            tb.current_lat = cache.latitude
            tb.current_lon = cache.longitude
            update_fields += ["current_lat", "current_lon"]
    elif action in ("retrieve", "grab"):
        tb.current_geocache = None
        tb.current_geocache_code = ""
        tb.current_geocache_name = ""
        from accounts.models import UserAccount as _UA
        from preferences.models import UserPreference
        _gc_acc = _UA.objects.filter(platform="gc").first()
        tb.current_holder_name = (
            _gc_acc.username if (_gc_acc and _gc_acc.username)
            else UserPreference.get("gc_username", "")
        )
        update_fields += ["current_geocache", "current_geocache_code",
                          "current_geocache_name", "current_holder_name"]

    tb.save(update_fields=update_fields)


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
    tb_actions: list[dict] | None = None,
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

    from geocaches.log_format import to_gc, to_oc

    # --- GC submission ---
    if "gc" in platforms and cache.gc_code:
        result.gc_success = False
        try:
            from gcprivate.gc_client import GCClient
            gc = GCClient()
            resp = gc.submit_log(
                cache.gc_code, log_type, iso_str, to_gc(text),
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
                    resp = oc.submit_log(cache.oc_code, log_type, iso_str, to_oc(text),
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
        from geocaches.image_upload import process_image
        image_list = list(images)

        if result.gc_success and result.gc_ref_code:
            try:
                from gcprivate.gc_client import GCClient
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
            from geocaches.services.adventures import ensure_not_al_parent_found
            cache.found = True
            if not cache.found_date or logged_at.date() < cache.found_date:
                cache.found_date = logged_at.date()
            ensure_not_al_parent_found(cache)
            cache.save(update_fields=["found", "found_date"])
        elif log_type in _DNF_TYPES:
            cache.dnf = True
            if not cache.dnf_date or logged_at.date() > cache.dnf_date:
                cache.dnf_date = logged_at.date()
            cache.save(update_fields=["dnf", "dnf_date"])
    else:
        result.messages.append("Log was not submitted to any platform.")

    # --- Trackable actions (chain after cache log) ---
    if tb_actions:
        _submit_tb_actions(
            cache=cache, logged_at=logged_at, tb_actions=tb_actions, result=result,
        )

    return result


def _submit_tb_actions(
    *, cache, logged_at: datetime, tb_actions: list[dict], result: LogSubmitResult,
) -> None:
    """Submit each TB action via the GC API; persist a local TrackableLog row.

    Failures are non-fatal: each row gets its own ``TrackableLogSubmitResult``
    appended to ``result.tb_results``. We only attempt TB submits when the GC
    cache log succeeded (or no cache log was attempted on GC), because GC
    requires the cache log to exist for retrieve/drop log linkage.
    """
    if result.gc_success is False:
        for raw in tb_actions:
            result.tb_results.append(TrackableLogSubmitResult(
                ref_code=raw.get("ref_code") or "",
                action=raw.get("action") or "",
                success=False,
                error="cache log failed — TB actions skipped",
            ))
        return

    iso_str = logged_at.strftime("%Y-%m-%dT%H:%M:%S.000Z")

    try:
        from gcprivate.trackable_client import TrackableClient
        client = TrackableClient()
    except Exception as exc:  # noqa: BLE001
        for raw in tb_actions:
            result.tb_results.append(TrackableLogSubmitResult(
                ref_code=raw.get("ref_code") or "",
                action=raw.get("action") or "",
                success=False,
                error=f"GC API unavailable: {exc}",
            ))
        return

    for raw in tb_actions:
        action = (raw.get("action") or "").strip()
        if action in _TB_CHAIN_EXPANSIONS:
            _submit_tb_chain(
                cache=cache, logged_at=logged_at, iso_str=iso_str,
                raw=raw, action=action, client=client, result=result,
            )
        else:
            _submit_single_tb_action(
                cache=cache, logged_at=logged_at, iso_str=iso_str,
                raw=raw, action=action, client=client, result=result,
            )


def _submit_single_tb_action(
    *, cache, logged_at: datetime, iso_str: str, raw: dict, action: str,
    client, result: LogSubmitResult,
) -> "TrackableLogSubmitResult":
    """Submit a single TB action (no chaining). Appends to result.tb_results."""
    from geocaches.models import Trackable, TrackableLog

    ref = (raw.get("ref_code") or "").strip().upper()
    tracking = (raw.get("tracking_code") or "").strip().upper()
    tb_text = raw.get("text") or ""

    tb_result = TrackableLogSubmitResult(ref_code=ref, action=action)
    result.tb_results.append(tb_result)

    log_type = _TB_ACTION_TO_LOG_TYPE.get(action)
    if log_type is None:
        tb_result.error = f"unknown TB action: {action!r}"
        return tb_result
    if action in _TB_ACTIONS_REQUIRING_TRACKING and not tracking:
        tb_result.error = "tracking code required"
        return tb_result

    if not ref and tracking:
        try:
            meta = client.verify_tracking_code(tracking)
            ref = (meta.get("reference_code") or "").strip().upper()
            tb_result.ref_code = ref
        except Exception as exc:  # noqa: BLE001
            tb_result.error = f"verify failed: {exc}"
            return tb_result
    if not ref:
        tb_result.error = "missing trackable reference"
        return tb_result

    # Expand [name]/[gc_code]/[tb_*] placeholders. The inventory endpoint
    # pre-expands the auto-visit text for the textarea, but we re-expand
    # here as a safety net for manually-typed placeholders.
    if tb_text and "[" in tb_text:
        from geocaches.log_format import expand_placeholders
        from geocaches.models import Trackable as _Trackable
        local_tb = _Trackable.objects.filter(reference_code=ref).first()
        tb_text = expand_placeholders(
            tb_text,
            cache=cache,
            log_type=log_type,
            log_date=logged_at.date(),
            trackable={
                "reference_code": ref,
                "name":           local_tb.name if local_tb else "",
                "owner_name":     local_tb.owner_name if local_tb else "",
            },
        )

    try:
        resp = client.submit_trackable_log(
            ref, log_type, iso_str, tb_text,
            geocache_code=cache.gc_code or None,
            tracking_code=tracking or None,
        )
        tb_result.source_id = resp.get("referenceCode", "")
        tb_result.success = True
        logger.info("Submitted TB log %s on %s (%s)", tb_result.source_id, ref, action)
    except Exception as exc:  # noqa: BLE001
        tb_result.error = str(exc)
        logger.warning("TB log submission failed for %s (%s): %s", ref, action, exc)
        return tb_result

    # Upload any image attachments after the TB log has a referenceCode.
    tb_images = raw.get("images") or []
    if tb_images and tb_result.source_id:
        from geocaches.image_upload import upload_images_to_gc_trackable_log
        for img_res in upload_images_to_gc_trackable_log(tb_result.source_id, tb_images):
            if not img_res.ok:
                result.image_errors.append(
                    f"TB {ref}: {img_res.filename}: {img_res.error}"
                )

    try:
        from accounts.models import UserAccount as _UA
        from preferences.models import UserPreference
        _gc_acc = _UA.objects.filter(platform="gc").first()
        if _gc_acc and _gc_acc.username:
            tb_username = _gc_acc.username
        else:
            tb_username = UserPreference.get("gc_username", "")

        trackable, _ = Trackable.objects.get_or_create(
            reference_code=ref,
            defaults={"name": ref},
        )
        TrackableLog.objects.create(
            trackable=trackable,
            log_type=log_type,
            logged_date=logged_at.date(),
            logged_at=logged_at,
            text=tb_text,
            user_name=tb_username,
            geocache=cache if cache.pk else None,
            geocache_ref_code=cache.gc_code or "",
            geocache_lat=cache.latitude,
            geocache_lon=cache.longitude,
            source_id=tb_result.source_id,
            is_local=True,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to persist local TB log for %s: %s", ref, exc)

    # Update local holder_state to reflect what just happened on GC.
    try:
        _update_local_tb_state(ref, action, cache)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to update local TB state for %s (%s): %s", ref, action, exc)

    # Optional auto-fetch: when the user logs a TB with a tracking code, pull
    # the full metadata from the GC API and store the tracking code locally.
    # Setting lives under preferences > Logging.
    if tracking and action in _TB_ACTIONS_REQUIRING_TRACKING:
        try:
            from preferences.models import UserPreference
            if UserPreference.get("auto_fetch_tb_on_log", False):
                from geocaches.services.trackable_sync import sync_trackable
                try:
                    sync_trackable(ref)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("auto-fetch %s after log submission failed: %s", ref, exc)
                # sync_trackable only writes tracking_code when the API returns
                # one (i.e. we hold the TB). For discover/grab where we typed
                # the code but don't hold the TB, persist it from here so it
                # shows up on the detail page next time.
                Trackable.objects.filter(reference_code=ref, tracking_code="").update(
                    tracking_code=tracking
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning("auto-fetch flow error for %s: %s", ref, exc)

    return tb_result


def _submit_tb_chain(
    *, cache, logged_at: datetime, iso_str: str, raw: dict, action: str,
    client, result: LogSubmitResult,
) -> None:
    """Expand a chain action (e.g. grab_chain) into sequential single actions.

    Aborts the chain at the first failure — later actions in the chain
    depend on the earlier ones (drop only works if grab succeeded, etc).
    Each sub-action is appended to ``result.tb_results`` so the user sees
    exactly what happened.
    """
    sub_actions = _TB_CHAIN_EXPANSIONS[action]
    chain_text = raw.get("text") or ""

    for i, sub in enumerate(sub_actions):
        sub_raw = dict(raw)
        sub_raw["action"] = sub
        sub_raw["text"] = chain_text
        # Steps after the first inherit the ref_code we resolved during step 1.
        # ``raw`` carries the verified ref already (set by the JS verify) so
        # this is usually a no-op, but we re-attach in case of discover-style
        # rows where the JS didn't populate it.
        sub_res = _submit_single_tb_action(
            cache=cache, logged_at=logged_at, iso_str=iso_str,
            raw=sub_raw, action=sub, client=client, result=result,
        )
        if not sub_res.success:
            for skipped in sub_actions[i + 1:]:
                result.tb_results.append(TrackableLogSubmitResult(
                    ref_code=sub_res.ref_code,
                    action=skipped,
                    success=False,
                    error=f"chain aborted after {sub} failed",
                ))
            return
