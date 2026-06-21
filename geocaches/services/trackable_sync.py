"""Trackable sync service — Phase 2.

Wraps the GC trackable API (`gcprivate.trackable_client.TrackableClient`)
into upsert operations against the local ``Trackable`` and ``TrackableLog``
tables. Callers:

  - ``sync_trackable(ref)`` — fetch /trackables/{ref}, upsert the row.
  - ``sync_trackable_logs(ref)`` — paginate the log history, upsert log rows
    by ``source_id`` (GC log GUID).
  - ``recompute_trackable_denorms(tb)`` — refresh the cached
    ``current_geocache_*`` / ``current_lat`` / ``last_log_date`` / ``total_visits``
    fields from local logs. Respects ``coords_user_override``.

Phase-2 scope: data fetch + persistence only. List/detail/map views read from
the local DB.  No auto-import of geocaches just because a TB log references
them — we always populate the ``geocache_ref_code`` string fallback and only
fill the FK when the cache happens to be in our local DB.
"""
from __future__ import annotations

import logging
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Optional

from geocaches.models import (
    Geocache,
    Trackable,
    TrackableHolderState,
    TrackableImage,
    TrackableKind,
    TrackableLog,
)
from geocaches.sync.trackable_constants import DEFAULT_TRACKABLE_LOG_TYPE_IDS

if TYPE_CHECKING:
    from gcprivate.trackable_client import TrackableClient

logger = logging.getLogger(__name__)


# Fields requested from /trackables/{ref}. The supported set is narrow — see
# plan §2.8 and the live probe summarised in `_KNOWN_UNSUPPORTED_FIELDS`. Add
# new fields here only after confirming the endpoint accepts them; the API
# returns HTTP 500 on the whole call if any single field is unrecognised.
_TRACKABLE_FIELDS = ",".join([
    "referenceCode",
    "name",
    "iconUrl",
    "trackableType",
    "owner",
    "holder",
    "currentGeocacheCode",
    "inHolderCollection",
    "trackingNumber",
    "releasedDate",
    "originCountry",
    "goal",
    "description",
    "kilometersTraveled",
])

# Fields the API is documented (or empirically known) NOT to accept on
# /trackables/{ref}. Kept here so we don't reintroduce them by accident:
#   - isArchived (returns "isArchived is not a supported property")
#   - currentHolderCode (replaced by holder.referenceCode)
#   - isMissing (untested; left out until we have a missing TB to probe with)

# Fields requested per trackable log row. Probed live 2026-05-11 — the endpoint
# rejects the whole call (HTTP 400/500) if any single field is unrecognised.
# Notable rejections so far: `loggedDateUtc`, `geocacheName`, `isArchived`,
# `geocache` (use `geocacheCode` instead), `ownerName`, `postedCoordinates`.
# Dotted subselection works (`owner.username`) and is used to avoid pulling
# the bloated full-profile payload that an `owner` request returns.
_TRACKABLE_LOG_FIELDS = ",".join([
    "referenceCode",
    "trackableLogType",
    "loggedDate",
    "text",
    "owner.username",
    "ownerCode",
    "geocacheCode",
    "coordinates",
    "imageCount",
])

# Default page size for the log history fetch. The GC API caps server-side at
# 50 per page in our experience; using a smaller number wastes quota.
_LOG_PAGE_SIZE = 50

# Log types that count as a "visit" for total_visits on the detail page. Mirrors
# the way GC.com renders the stat — discoveries + drops + retrievals + visits.
_VISIT_LOG_TYPES = frozenset({
    "Discovered It",
    "Retrieve It from a Cache",
    "Dropped Off",
    "Grab It (Not from a Cache)",
    "Visited",
})


def sync_trackable(
    ref: str,
    *,
    client: Optional[TrackableClient] = None,
) -> Trackable:
    """Fetch /trackables/{ref} and upsert the local Trackable row.

    Returns the saved Trackable. Raises on API/network failure (caller decides
    how to report). ``coords_user_override`` blocks current_lat/lon overwrite.
    """
    ref = (ref or "").strip().upper()
    if not ref:
        raise ValueError("Empty trackable reference")

    from gcprivate.trackable_client import TrackableClient
    cli = client or TrackableClient()
    raw = cli._gc._api.get(f"/trackables/{ref}", fields=_TRACKABLE_FIELDS)
    if not isinstance(raw, dict) or not raw.get("referenceCode"):
        raise ValueError(f"Unexpected /trackables/{ref} response: {raw!r}")

    tb, created = Trackable.objects.get_or_create(
        reference_code=ref,
        defaults={"name": raw.get("name") or ref},
    )

    tb.name        = raw.get("name") or tb.name
    src_icon = raw.get("iconUrl") or ""
    if src_icon:
        tb.icon_url = src_icon
        from geocaches.services.image_cache import prefetch
        prefetch(src_icon, category="tb_icon", linked={"trackable": tb})
    tb_type = raw.get("trackableType") or {}
    if isinstance(tb_type, dict) and tb_type.get("name"):
        tb.series = tb_type["name"]
    tb.kind        = _derive_kind(tb_type)
    # is_active / is_archived / is_missing — not exposed via the /trackables
    # field set we can request. Phase 2 leaves them at their model defaults
    # until we find a working source (web-session scrape or an as-yet-unknown
    # field).

    owner = raw.get("owner") or {}
    if isinstance(owner, dict):
        tb.owner_name = owner.get("username") or tb.owner_name
        owner_ref = owner.get("referenceCode") or ""
        if owner_ref and owner_ref.startswith("PR") and owner_ref[2:].isdigit():
            try:
                tb.owner_gc_id = int(owner_ref[2:])
            except ValueError:
                pass

    if "releasedDate" in raw and raw["releasedDate"]:
        tb.released_date = _parse_date(raw["releasedDate"])
    if "originCountry" in raw and raw["originCountry"]:
        tb.origin = str(raw["originCountry"])
    if "goal" in raw and raw["goal"] is not None:
        tb.goal = str(raw["goal"])
    if "description" in raw and raw["description"] is not None:
        tb.about = str(raw["description"])

    tracking_number = raw.get("trackingNumber") or ""
    if tracking_number:
        tb.tracking_code = tracking_number

    km = raw.get("kilometersTraveled")
    if isinstance(km, (int, float)):
        tb.total_distance_km = float(km)

    holder = raw.get("holder") or {}
    holder_name = holder.get("username") if isinstance(holder, dict) else ""
    current_code = (raw.get("currentGeocacheCode") or "").strip()
    in_collection = bool(raw.get("inHolderCollection"))
    is_missing    = False  # API doesn't expose isMissing here; see _TRACKABLE_FIELDS note

    tb.current_holder_name = holder_name or ""
    tb.current_geocache_code = current_code
    if current_code:
        cache = Geocache.objects.filter(gc_code=current_code).first()
        tb.current_geocache = cache
        tb.current_geocache_name = cache.name if cache else tb.current_geocache_name
    else:
        tb.current_geocache = None

    tb.holder_state = _derive_holder_state(
        is_missing=is_missing,
        in_collection=in_collection,
        we_hold_it=bool(tracking_number),
        holder_name=holder_name,
        current_geocache_code=current_code,
    )

    tb.save()
    logger.info("Synced trackable %s (%s, holder_state=%s)",
                ref, "created" if created else "updated", tb.holder_state)
    _sync_trackable_images(tb, client=cli)
    return tb


def _sync_trackable_images(tb: Trackable, *, client: TrackableClient) -> None:
    """Upsert TB listing images by source_id; remove rows no longer upstream.

    Failures here are non-fatal: a TB with no readable images shouldn't break
    the metadata sync.
    """
    try:
        raw = client.get_trackable_images(tb.reference_code)
    except Exception as exc:  # noqa: BLE001
        logger.warning("get_trackable_images(%s) failed: %s", tb.reference_code, exc)
        return

    # API quirk (2026-05-12): /trackables/{ref}/images returns `referenceCode`
    # = the *trackable's* code for every row, not a per-image id. The real
    # unique identifier is `guid`.
    from geocaches.services.image_cache import prefetch as _img_prefetch
    seen_source_ids: set[str] = set()
    for img in raw:
        sid = (img.get("guid") or "").strip()
        if not sid:
            continue
        seen_source_ids.add(sid)
        url        = img.get("largeUrl") or img.get("url") or ""
        thumb_url  = img.get("thumbnailUrl") or ""
        large_url  = img.get("largeUrl") or ""
        TrackableImage.objects.update_or_create(
            trackable=tb,
            source_id=sid,
            defaults={
                "log":           None,
                "url":           url,
                "thumbnail_url": thumb_url,
                "large_url":     large_url,
                "caption":       (img.get("name") or "")[:255],
                "description":   img.get("description") or "",
                "uploaded_at":   _parse_datetime(img.get("createdDate")),
            },
        )
        for u in (url, thumb_url, large_url):
            if u:
                _img_prefetch(u, category="tb_listing", linked={"trackable": tb})
    # Drop locally-stored images that are no longer present upstream. Skip
    # rows with empty source_id (locally uploaded, not yet API-known) and
    # rows attached to a specific log (per-log gallery, handled elsewhere).
    TrackableImage.objects.filter(trackable=tb, log__isnull=True) \
        .exclude(source_id="") \
        .exclude(source_id__in=seen_source_ids) \
        .delete()


def sync_trackable_logs(
    ref: str,
    *,
    full: bool = False,
    client: Optional[TrackableClient] = None,
    page_size: int = _LOG_PAGE_SIZE,
    max_pages: int = 200,
) -> int:
    """Paginate the TB log history and upsert local TrackableLog rows.

    ``full=False`` (default) stops once a page contains only logs we already
    have (incremental sync). ``full=True`` walks until the API returns an
    empty page — used for the first import.

    Returns the number of *new* logs persisted. Existing rows are left
    untouched (logs are immutable on GC.com once written). After sync, the
    parent Trackable's denormalised fields are refreshed.
    """
    ref = (ref or "").strip().upper()
    if not ref:
        raise ValueError("Empty trackable reference")

    tb = Trackable.objects.filter(reference_code=ref).first()
    if tb is None:
        tb = sync_trackable(ref, client=client)

    from gcprivate.trackable_client import TrackableClient
    cli = client or TrackableClient()
    type_id_to_name = _invert_log_type_ids(cli.get_trackable_log_types())

    existing_source_ids = set(
        TrackableLog.objects.filter(trackable=tb)
        .exclude(source_id="")
        .values_list("source_id", flat=True)
    )

    new_count = 0
    skip = 0
    for _ in range(max_pages):
        raw = cli._gc._api.get(
            f"/trackables/{ref}/trackablelogs",
            fields=_TRACKABLE_LOG_FIELDS,
            take=page_size,
            skip=skip,
        )
        page = list(raw or [])
        if not page:
            break

        page_new = 0
        to_create: list[TrackableLog] = []
        for entry in page:
            sid = (entry.get("referenceCode") or "").strip()
            if not sid or sid in existing_source_ids:
                continue
            log = _build_trackable_log(tb, entry, type_id_to_name)
            if log is None:
                continue
            to_create.append(log)
            existing_source_ids.add(sid)
            page_new += 1

        if to_create:
            TrackableLog.objects.bulk_create(to_create)
            new_count += page_new
            from geocaches.services.image_cache import prefetch_html_for_tb_log
            for log in to_create:
                if log.text and "<img" in log.text.lower():
                    prefetch_html_for_tb_log(log.text, tb, log=log)

        # Incremental: stop the first time a page is fully redundant.
        if not full and page_new == 0:
            break
        if len(page) < page_size:
            break
        skip += len(page)

    recompute_trackable_denorms(tb)
    logger.info("Synced %d new logs for %s (skip=%d, full=%s)",
                new_count, ref, skip, full)
    return new_count


def recompute_trackable_denorms(tb: Trackable) -> None:
    """Refresh denormalised current_* / last_log_date / total_visits.

    Reads only from the local ``TrackableLog`` table — never hits the network.
    ``coords_user_override=True`` blocks lat/lon overwrites; everything else
    is always refreshed.
    """
    logs = list(
        TrackableLog.objects.filter(trackable=tb).order_by("-logged_date", "-id")
    )
    if not logs:
        update_fields = []
        if tb.last_log_date or tb.total_visits:
            tb.last_log_date = None
            tb.total_visits = None
            update_fields += ["last_log_date", "total_visits"]
        if not tb.coords_user_override and tb.current_lat is None:
            coords = _geocache_coords_for_tb(tb)
            if coords:
                tb.current_lat, tb.current_lon = coords
                update_fields += ["current_lat", "current_lon"]
        if update_fields:
            tb.save(update_fields=update_fields + ["updated_at"])
        return

    last_log = logs[0]
    tb.last_log_date = last_log.logged_date

    tb.total_visits = sum(1 for log in logs if log.log_type in _VISIT_LOG_TYPES) or None

    # Use the newest log that has a known geocache to project current location.
    located = next(
        (log for log in logs if log.geocache_ref_code and log.geocache_lat is not None),
        None,
    )

    update_fields = ["last_log_date", "total_visits", "updated_at"]
    if not tb.coords_user_override:
        if located:
            tb.current_lat = located.geocache_lat
            tb.current_lon = located.geocache_lon
            update_fields += ["current_lat", "current_lon"]
        elif tb.current_lat is None:
            # No log has coords; fall back to the current geocache if we have it locally.
            coords = _geocache_coords_for_tb(tb)
            if coords:
                tb.current_lat, tb.current_lon = coords
                update_fields += ["current_lat", "current_lon"]

    tb.save(update_fields=update_fields)


def resolve_tb_locations(qs=None) -> dict:
    """Fetch coordinates from the GC API for TBs whose current cache is not in
    our local DB (current_geocache_code set, current_lat null).

    ``qs`` is an optional pre-filtered Trackable queryset; defaults to all.
    Returns {"resolved": N, "total_missing": M}.
    """
    from gcprivate.gc_client import GCClient
    from django.utils import timezone

    if qs is None:
        qs = Trackable.objects.all()

    candidates = list(
        qs.filter(
            current_geocache_code__gt="",
            current_lat__isnull=True,
            coords_user_override=False,
        ).values("id", "current_geocache_code")
    )
    total_missing = len(candidates)
    if not total_missing:
        return {"resolved": 0, "total_missing": 0}

    ids_by_code: dict[str, list] = {}
    for row in candidates:
        ids_by_code.setdefault(row["current_geocache_code"], []).append(row["id"])

    coord_map = GCClient().fetch_geocache_coords(list(ids_by_code.keys()))

    now = timezone.now()
    resolved = 0
    for code, tb_ids in ids_by_code.items():
        if code not in coord_map:
            continue
        lat, lon = coord_map[code]
        updated = Trackable.objects.filter(
            id__in=tb_ids,
            current_lat__isnull=True,
            coords_user_override=False,
        ).update(current_lat=lat, current_lon=lon, updated_at=now)
        resolved += updated

    return {"resolved": resolved, "total_missing": total_missing}


def _geocache_coords_for_tb(tb: Trackable) -> Optional[tuple[float, float]]:
    """Return (lat, lon) for tb's current geocache from the local DB, or None."""
    cache = tb.current_geocache
    if cache is None and tb.current_geocache_code:
        cache = Geocache.objects.filter(gc_code=tb.current_geocache_code).first()
    if cache:
        return cache.latitude, cache.longitude
    return None


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------

def _build_trackable_log(
    tb: Trackable,
    entry: dict,
    type_id_to_name: dict[int, str],
) -> Optional[TrackableLog]:
    """Map one GC API log payload to an unsaved TrackableLog instance."""
    sid = (entry.get("referenceCode") or "").strip()
    if not sid:
        return None

    type_obj = entry.get("trackableLogType") or {}
    type_id = type_obj.get("id") if isinstance(type_obj, dict) else None
    type_name = (
        type_id_to_name.get(int(type_id)) if type_id is not None else None
    ) or (type_obj.get("name") if isinstance(type_obj, dict) else "") or "Write note"

    logged_at = _parse_datetime(entry.get("loggedDate"))
    logged_date = (
        logged_at.date() if logged_at
        else _parse_date(entry.get("loggedDate")) or date.today()
    )

    owner = entry.get("owner") or {}
    user_name = owner.get("username") if isinstance(owner, dict) else ""
    user_ref  = entry.get("ownerCode") or ""

    code = (entry.get("geocacheCode") or "").strip()
    cache = Geocache.objects.filter(gc_code=code).first() if code else None

    # `coordinates` is returned inline by the API for any log that references
    # a cache, so movement maps work even when we haven't imported the cache
    # locally. Local cache coords take precedence (more up-to-date if the
    # user has corrected coordinates).
    coords = entry.get("coordinates") or {}
    api_lat = coords.get("latitude") if isinstance(coords, dict) else None
    api_lon = coords.get("longitude") if isinstance(coords, dict) else None
    cache_lat = cache.latitude if cache else api_lat
    cache_lon = cache.longitude if cache else api_lon

    return TrackableLog(
        trackable=tb,
        log_type=type_name,
        logged_date=logged_date,
        logged_at=logged_at,
        text=entry.get("text") or "",
        user_name=user_name or "",
        user_id=str(user_ref or ""),
        geocache=cache,
        geocache_ref_code=code,
        geocache_lat=cache_lat,
        geocache_lon=cache_lon,
        source_id=sid,
        is_local=False,
    )


def _derive_kind(trackable_type) -> str:
    """Map a GC trackableType payload to our TrackableKind."""
    name = ""
    if isinstance(trackable_type, dict):
        name = (trackable_type.get("name") or "").lower()
    elif isinstance(trackable_type, str):
        name = trackable_type.lower()
    if not name:
        return TrackableKind.TRAVEL_BUG
    if "travel bug" in name:
        return TrackableKind.TRAVEL_BUG
    if "geocoin" in name or "coin" in name:
        return TrackableKind.GEOCOIN
    return TrackableKind.OTHER


def _derive_holder_state(
    *,
    is_missing: bool,
    in_collection: bool,
    we_hold_it: bool,
    holder_name: str,
    current_geocache_code: str,
) -> str:
    if is_missing:
        return TrackableHolderState.MISSING
    if we_hold_it and in_collection:
        return TrackableHolderState.COLLECTION
    if we_hold_it:
        return TrackableHolderState.HELD_BY_USER
    if current_geocache_code:
        return TrackableHolderState.IN_CACHE
    if holder_name:
        return TrackableHolderState.HELD_BY_OTHER
    return TrackableHolderState.UNKNOWN


def _invert_log_type_ids(name_to_id: dict[str, int]) -> dict[int, str]:
    """Return {id: name}. Falls back to bundled defaults if the API map is empty."""
    src = name_to_id or DEFAULT_TRACKABLE_LOG_TYPE_IDS
    return {int(v): k for k, v in src.items()}


def _parse_date(value) -> Optional[date]:
    if not value:
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    s = str(value)[:10]
    try:
        y, m, d = s.split("-")
        return date(int(y), int(m), int(d))
    except (ValueError, AttributeError):
        return None


def _parse_datetime(value) -> Optional[datetime]:
    """Parse an ISO datetime; assume UTC if the string is naive.

    The GC API returns trackable log dates like "2026-05-11T12:00:00.000" with
    no offset. Per Groundspeak convention these are UTC noon stamps, so we
    attach UTC rather than treating them as local time.
    """
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    s = str(value).replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
