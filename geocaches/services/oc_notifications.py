"""Service layer for OpenCaching profile-based notifications.

Two shapes:

  * .de family — one notification rule per platform (radius around a
    reference point, ``notify_oconly`` flag).
  * .us — multiple "neighbourhoods" per platform, plus platform-global
    toggles (``notify_logs``) and a delivery ``frequency``.

The model carries a ``server_id`` per row; for .de this is always ``""``
and a uniqueness constraint enforces one row per platform.  For .us the
server_id is the nbh index (``"0"`` for the default, ``"1"``+ for
additional named neighbourhoods).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from geocaches.models import OCNotification
from geocaches.sync import oc_profile

logger = logging.getLogger("geocaches.notify")


def _platform_label(platform: str) -> str:
    return dict(OCNotification.PLATFORM_CHOICES).get(platform, platform)


# ---------------------------------------------------------------------------
# Pull
# ---------------------------------------------------------------------------

def pull(platform: str) -> list[OCNotification]:
    """Refresh local rows for ``platform`` from the server.

    For .de family this still upserts a single row (server_id="").
    For .us it upserts one row per neighbourhood and removes local rows
    whose server_id no longer appears on the server.
    """
    rows = oc_profile.fetch_all(platform)
    is_us = platform == "oc_us"

    if is_us:
        # Drop any pre-multi-nbh legacy row (server_id="") before upserting.
        OCNotification.objects.filter(platform=platform, server_id="").delete()

    server_ids_seen: set[str] = set()
    saved: list[OCNotification] = []

    for detail in rows:
        sid = detail.get("server_id", "")
        server_ids_seen.add(sid)
        n, _ = OCNotification.objects.get_or_create(
            platform=platform,
            server_id=sid,
            defaults={
                "name": detail.get("name", ""),
                "latitude": detail["latitude"],
                "longitude": detail["longitude"],
                "radius_km": detail["radius_km"] or 20,
                "enabled": detail.get("enabled", detail["radius_km"] > 0),
                "notify_oconly": detail["notify_oconly"],
                "notify_logs": detail.get("notify_logs", False),
                "frequency": detail.get("frequency", "daily"),
            },
        )
        n.name = detail.get("name", "")
        n.latitude = detail["latitude"] or n.latitude
        n.longitude = detail["longitude"] or n.longitude
        n.notify_oconly = detail["notify_oconly"]
        n.notify_logs = detail.get("notify_logs", False)
        n.frequency = detail.get("frequency", n.frequency)

        if is_us:
            n.enabled = bool(detail.get("enabled"))
            if detail["radius_km"] > 0:
                n.radius_km = detail["radius_km"]
        else:
            if detail["radius_km"] > 0:
                n.enabled = True
                n.radius_km = detail["radius_km"]
            else:
                n.enabled = False
        n.last_synced_at = datetime.now(timezone.utc)
        n.save()
        saved.append(n)
        logger.info(
            "OC notification pulled: %s/%s name=%r — radius=%dkm enabled=%s "
            "oconly=%s logs=%s freq=%s coords=%.5f,%.5f",
            platform, sid or "-", n.name,
            n.radius_km, n.enabled, n.notify_oconly, n.notify_logs, n.frequency,
            n.latitude, n.longitude,
        )

    # On .us, prune local rows whose server_id no longer exists on the server
    # (someone deleted a neighbourhood elsewhere).
    if is_us:
        stale = OCNotification.objects.filter(platform=platform).exclude(server_id__in=server_ids_seen)
        for n in stale:
            logger.info("OC.us notification disappeared on server, deleting local: id=%s sid=%s name=%r",
                        n.id, n.server_id, n.name)
        stale.delete()

    return saved


# ---------------------------------------------------------------------------
# Push (per-row)
# ---------------------------------------------------------------------------

def push(notification_id: int) -> OCNotification:
    """Send a single local row's state to the OC server.

    On .us the row's coords/radius/enabled go to /MyNeighbourhood/save/<sid>
    plus the appropriate toggle; the platform-global notify_logs + frequency
    are sent separately (this method also pushes those globals to keep them
    consistent).
    """
    n = OCNotification.objects.get(id=notification_id)
    effective_radius = n.radius_km if (n.enabled or n.platform == "oc_us") else 0
    oc_profile.save_row(
        n.platform,
        server_id=n.server_id,
        name=n.name,
        latitude=n.latitude,
        longitude=n.longitude,
        radius_km=effective_radius,
        notify_oconly=n.notify_oconly,
        enabled=n.enabled,
    )
    if n.platform == "oc_us":
        oc_profile.save_globals(n.platform, notify_logs=n.notify_logs, frequency=n.frequency)
    n.last_synced_at = datetime.now(timezone.utc)
    n.save(update_fields=["last_synced_at"])
    logger.info(
        "OC notification pushed: %s/%s name=%r — radius=%dkm enabled=%s oconly=%s logs=%s freq=%s",
        n.platform, n.server_id or "-", n.name,
        effective_radius, n.enabled, n.notify_oconly, n.notify_logs, n.frequency,
    )
    return n


def save_local(
    notification_id: int | None,
    *,
    platform: str,
    server_id: str = "",
    name: str = "",
    latitude: float,
    longitude: float,
    radius_km: int,
    enabled: bool,
    notify_oconly: bool = False,
    notify_logs: bool = False,
    frequency: str = "daily",
    location_id: int | None,
    push_to_server: bool = True,
) -> OCNotification:
    """Update one local row (creating it if needed) and optionally push."""
    if radius_km < 1 or radius_km > OCNotification.MAX_RADIUS_KM:
        raise ValueError(
            f"Radius must be 1..{OCNotification.MAX_RADIUS_KM} km "
            "(use the Enable toggle to disable instead of setting radius to 0)."
        )
    if notification_id:
        n = OCNotification.objects.get(id=notification_id)
    else:
        n = OCNotification(platform=platform, server_id=server_id)
    n.name = name
    n.latitude = latitude
    n.longitude = longitude
    n.radius_km = radius_km
    n.enabled = enabled
    n.notify_oconly = notify_oconly
    n.notify_logs = notify_logs
    n.frequency = frequency
    n.location_id = location_id
    n.save()
    if push_to_server:
        push(n.id)
    return n


# ---------------------------------------------------------------------------
# OC.us-only: create / delete a neighbourhood
# ---------------------------------------------------------------------------

def create_nbh(
    platform: str,
    *,
    name: str,
    latitude: float,
    longitude: float,
    radius_km: int,
    location_id: int | None = None,
) -> OCNotification:
    """Create a new neighbourhood on the server and store it locally."""
    name = (name or "").strip()
    if not name:
        raise ValueError("A new neighbourhood needs a name.")
    new_id = oc_profile.create_nbh(
        platform,
        name=name, latitude=latitude, longitude=longitude, radius_km=radius_km,
    )
    n = OCNotification.objects.create(
        platform=platform,
        server_id=new_id,
        name=name,
        latitude=latitude,
        longitude=longitude,
        radius_km=radius_km,
        enabled=True,
        location_id=location_id,
        last_synced_at=datetime.now(timezone.utc),
    )
    logger.info("OC.us neighbourhood created locally: id=%s sid=%s name=%r", n.id, new_id, name)
    return n


def delete_nbh(notification_id: int) -> None:
    """Delete a neighbourhood (both locally and on the server)."""
    n = OCNotification.objects.get(id=notification_id)
    if not n.is_additional_nbh:
        raise ValueError("Only additional neighbourhoods (server_id >= 1) can be deleted.")
    oc_profile.delete_nbh(n.platform, n.server_id)
    logger.info("OC.us neighbourhood deleted: id=%s sid=%s name=%r", n.id, n.server_id, n.name)
    n.delete()
