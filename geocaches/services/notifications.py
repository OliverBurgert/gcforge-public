"""Service layer for GC Instant Notifications.

Bridges the ``GCNotification`` model and the website automation in
``geocaches.sync.notify_web``.  Higher-level operations: full sync, bulk
create, region bulk toggles, per-row CRUD.
"""
from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

from geocaches.models import GCNotification
from geocaches.sync import notify_constants

logger = logging.getLogger("geocaches.notify")


def _notify_web():
    """Lazy handle to the private notify_web automation (absent in the public build)."""
    from gcprivate import notify_web
    return notify_web


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _compute_server_hash(payload: dict) -> str:
    """Stable hash of the server-significant fields for change detection."""
    keys = ("name", "latitude", "longitude", "radius_km", "cache_type_id",
            "log_event_ids", "recipient_email", "enabled")
    data = {k: (sorted(payload[k]) if k == "log_event_ids" else payload[k])
            for k in keys if k in payload}
    blob = json.dumps(data, sort_keys=True, separators=(",", ":"))
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()


def _to_server_payload(n: GCNotification) -> dict:
    return {
        "name": n.name,
        "latitude": n.latitude,
        "longitude": n.longitude,
        "radius_km": n.radius_km,
        "cache_type_id": n.cache_type_id,
        "log_event_ids": list(n.log_event_ids or []),
        "recipient_email": n.recipient_email,
        "enabled": n.enabled,
    }


def _type_label(type_id: int) -> str:
    return notify_constants.CACHE_TYPES.get(type_id, f"type {type_id}")


def _event_labels(event_ids) -> str:
    names = [notify_constants.LOG_EVENT_NAMES.get(eid, str(eid)) for eid in (event_ids or [])]
    return ", ".join(names) or "(none)"


# ---------------------------------------------------------------------------
# Sync
# ---------------------------------------------------------------------------

@dataclass
class SyncResult:
    auto_created: int = 0
    auto_updated: int = 0
    matched: int = 0
    local_only: list[int] = field(default_factory=list)
    server_deleted: list[int] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "auto_created": self.auto_created,
            "auto_updated": self.auto_updated,
            "matched": self.matched,
            "local_only_ids": list(self.local_only),
            "server_deleted_ids": list(self.server_deleted),
        }


def sync_with_server(task_info=None) -> dict:
    """Pull server state, classify each notification.

    * Server-only entries are created locally (full detail fetched).
    * Existing matches are updated locally if the server hash changed.
    * Local-only and server-deleted entries are returned for user decision —
      the view surfaces a diff dialog and routes choices through
      ``apply_local_only_action`` / ``apply_server_deleted_action``.
    """
    result = SyncResult()

    if task_info:
        task_info.phase = "Fetching notification list"
        task_info.total = 3
        task_info.completed = 0

    server_rows = _notify_web().list_notifications()
    server_by_nid = {r["nid"]: r for r in server_rows}

    local_qs = GCNotification.objects.filter(source="gc")
    local_by_nid = {n.server_id: n for n in local_qs if n.server_id}
    local_only = [n for n in local_qs if not n.server_id]

    if task_info:
        task_info.phase = f"Reconciling {len(server_rows)} server / {local_qs.count()} local"
        task_info.completed = 1
        task_info.total = 2 + len(server_rows)

    new_nids = [nid for nid in server_by_nid if nid not in local_by_nid]
    for i, nid in enumerate(new_nids):
        if task_info:
            task_info.phase = f"Fetching new server notification {i + 1}/{len(new_nids)}"
            task_info.completed = 2 + i
        detail = _notify_web().fetch_notification_detail(nid)
        _create_local_from_detail(detail)
        result.auto_created += 1

    for nid, n in list(local_by_nid.items()):
        if nid not in server_by_nid:
            result.server_deleted.append(n.id)
            continue
        srv = server_by_nid[nid]
        # Light comparison (without re-fetching detail every sync): type + name
        # + enabled are visible on the list page.
        light = {
            "name": n.name == srv["name"],
            "type": n.cache_type_id == srv["type_id"] or srv["type_id"] == 0,
            "enabled": n.enabled == srv["enabled"],
        }
        if all(light.values()):
            result.matched += 1
            continue
        # Mismatch on a visible field → re-fetch detail, overwrite local.
        detail = _notify_web().fetch_notification_detail(nid)
        _update_local_from_detail(n, detail)
        result.auto_updated += 1

    for n in local_only:
        result.local_only.append(n.id)

    if task_info:
        task_info.phase = "Done"
        task_info.completed = task_info.total

    logger.info(
        "Notify sync: %d auto-created, %d auto-updated, %d matched, "
        "%d local-only, %d server-deleted",
        result.auto_created, result.auto_updated, result.matched,
        len(result.local_only), len(result.server_deleted),
    )
    return result.to_dict()


def _create_local_from_detail(detail: dict) -> GCNotification:
    payload = {
        "name": detail["name"],
        "latitude": detail["latitude"],
        "longitude": detail["longitude"],
        "radius_km": detail["radius_km"],
        "cache_type_id": detail["cache_type_id"],
        "log_event_ids": detail["log_event_ids"],
        "recipient_email": detail["recipient_email"],
        "enabled": detail["enabled"],
    }
    n = GCNotification.objects.create(
        source="gc",
        server_id=detail["nid"],
        last_synced_at=datetime.now(timezone.utc),
        server_hash=_compute_server_hash(payload),
        **payload,
    )
    return n


def _update_local_from_detail(n: GCNotification, detail: dict) -> None:
    n.name = detail["name"]
    n.latitude = detail["latitude"]
    n.longitude = detail["longitude"]
    n.radius_km = detail["radius_km"]
    n.cache_type_id = detail["cache_type_id"]
    n.log_event_ids = detail["log_event_ids"]
    n.recipient_email = detail["recipient_email"]
    n.enabled = detail["enabled"]
    n.last_synced_at = datetime.now(timezone.utc)
    n.server_hash = _compute_server_hash(_to_server_payload(n))
    from geocaches.db_lock import db_write
    with db_write():
        n.save()


# ---------------------------------------------------------------------------
# Per-row sync decisions
# ---------------------------------------------------------------------------

def apply_local_only_action(notification_id: int, action: str) -> None:
    """Resolve a local-only row.

    action ∈ {'create_on_server', 'delete_locally', 'discard'}.
    'discard' is a no-op (row stays unsynced).
    """
    n = GCNotification.objects.get(id=notification_id, source="gc", server_id="")
    if action == "create_on_server":
        _push_create(n)
    elif action == "delete_locally":
        n.delete()
    elif action == "discard":
        return
    else:
        raise ValueError(f"Unknown local-only action: {action!r}")


def apply_server_deleted_action(notification_id: int, action: str) -> None:
    """Resolve a row whose server NID has gone.

    action ∈ {'recreate', 'delete_locally'}.
    """
    n = GCNotification.objects.get(id=notification_id, source="gc")
    if action == "recreate":
        n.server_id = ""  # forget the dead NID; push as a new create
        n.save(update_fields=["server_id"])
        _push_create(n)
    elif action == "delete_locally":
        n.delete()
    else:
        raise ValueError(f"Unknown server-deleted action: {action!r}")


def _push_create(n: GCNotification) -> None:
    # If the caller didn't pick a recipient, mirror what the server will pick
    # (primary alt-email) so the local row matches the server state.
    if not n.recipient_email:
        n.recipient_email = _notify_web().get_primary_email()
    nid = _notify_web().create_notification(
        name=n.name,
        latitude=n.latitude,
        longitude=n.longitude,
        radius_km=n.radius_km,
        cache_type_id=n.cache_type_id,
        log_event_ids=n.log_event_ids or [],
        recipient_email=n.recipient_email,
        enabled=n.enabled,
    )
    n.server_id = nid
    n.last_synced_at = datetime.now(timezone.utc)
    n.server_hash = _compute_server_hash(_to_server_payload(n))
    from geocaches.db_lock import db_write
    with db_write():
        n.save(update_fields=[
            "server_id", "recipient_email", "last_synced_at", "server_hash",
        ])
    logger.info(
        "GC notification created: NID=%s %r (%s, %d km, events=[%s])",
        nid, n.name, _type_label(n.cache_type_id), n.radius_km,
        _event_labels(n.log_event_ids),
    )


def _push_update(n: GCNotification) -> None:
    if not n.recipient_email:
        n.recipient_email = _notify_web().get_primary_email()
    _notify_web().update_notification(
        n.server_id,
        name=n.name,
        latitude=n.latitude,
        longitude=n.longitude,
        radius_km=n.radius_km,
        cache_type_id=n.cache_type_id,
        log_event_ids=n.log_event_ids or [],
        recipient_email=n.recipient_email,
        enabled=n.enabled,
    )
    n.last_synced_at = datetime.now(timezone.utc)
    n.server_hash = _compute_server_hash(_to_server_payload(n))
    from geocaches.db_lock import db_write
    with db_write():
        n.save(update_fields=["recipient_email", "last_synced_at", "server_hash"])
    logger.info(
        "GC notification updated: NID=%s %r (%s, %d km, events=[%s])",
        n.server_id, n.name, _type_label(n.cache_type_id), n.radius_km,
        _event_labels(n.log_event_ids),
    )


# ---------------------------------------------------------------------------
# Per-row CRUD (via this app)
# ---------------------------------------------------------------------------

def update_notification(notification_id: int, **fields) -> GCNotification:
    """Update a notification locally + push to server."""
    n = GCNotification.objects.get(id=notification_id)
    for k, v in fields.items():
        setattr(n, k, v)
    n.save()
    if n.server_id:
        _push_update(n)
    return n


def delete_notification(notification_id: int) -> None:
    n = GCNotification.objects.get(id=notification_id)
    if n.server_id:
        _notify_web().delete_notification(n.server_id)
        logger.info("GC notification deleted: NID=%s %r (%s)",
                    n.server_id, n.name, _type_label(n.cache_type_id))
    n.delete()


def toggle_enabled(notification_id: int) -> bool:
    """Flip enabled state — uses the cheap `?did=` toggle if we have a NID."""
    n = GCNotification.objects.get(id=notification_id)
    new_state = not n.enabled
    if n.server_id:
        _notify_web().toggle_notification(n.server_id)
        logger.info("GC notification %s: NID=%s %r (%s)",
                    "enabled" if new_state else "disabled",
                    n.server_id, n.name, _type_label(n.cache_type_id))
    n.enabled = new_state
    n.last_synced_at = datetime.now(timezone.utc)
    n.save(update_fields=["enabled", "last_synced_at"])
    return new_state


# ---------------------------------------------------------------------------
# Per-row / per-group pull (local <- server) and push (local -> server)
# ---------------------------------------------------------------------------

def pull_from_server(notification_id: int) -> GCNotification:
    """Overwrite the local row with whatever the server currently has."""
    n = GCNotification.objects.get(id=notification_id, source="gc")
    if not n.server_id:
        raise RuntimeError(
            f"Cannot pull notification id={notification_id}: no server_id."
        )
    detail = _notify_web().fetch_notification_detail(n.server_id)
    _update_local_from_detail(n, detail)
    logger.info("GC notification pulled from server: NID=%s %r (%s)",
                n.server_id, n.name, _type_label(n.cache_type_id))
    return n


def push_to_server(notification_id: int) -> GCNotification:
    """Send the local row's state up to the server (create if not yet pushed)."""
    n = GCNotification.objects.get(id=notification_id, source="gc")
    if n.server_id:
        _push_update(n)
    else:
        _push_create(n)
    return n


def pull_by_location(location_id: int | None, task_info=None) -> dict:
    """Refresh every notification in a location group from the server."""
    qs = _location_filter(location_id).exclude(server_id="")
    targets = list(qs)
    if task_info:
        task_info.phase = f"Pulling {len(targets)} notification(s)"
        task_info.total = len(targets)
        task_info.completed = 0

    pulled = 0
    errors = []
    for i, n in enumerate(targets):
        try:
            detail = _notify_web().fetch_notification_detail(n.server_id)
            _update_local_from_detail(n, detail)
            pulled += 1
            logger.info("GC notification pulled from server: NID=%s %r (%s)",
                        n.server_id, n.name, _type_label(n.cache_type_id))
        except Exception as exc:
            logger.warning("Pull failed for NID=%s: %s", n.server_id, exc)
            errors.append({"nid": n.server_id, "name": n.name, "error": str(exc)})
        if task_info:
            task_info.completed = i + 1
    return {"pulled": pulled, "errors": errors}


def push_by_location(location_id: int | None, task_info=None) -> dict:
    """Push every notification in a location group up to the server."""
    qs = _location_filter(location_id)
    targets = list(qs)
    if task_info:
        task_info.phase = f"Pushing {len(targets)} notification(s)"
        task_info.total = len(targets)
        task_info.completed = 0

    pushed = 0
    errors = []
    for i, n in enumerate(targets):
        try:
            if n.server_id:
                _push_update(n)
            else:
                _push_create(n)
            pushed += 1
        except Exception as exc:
            logger.warning("Push failed for id=%s: %s", n.id, exc)
            errors.append({"id": n.id, "name": n.name, "error": str(exc)})
        if task_info:
            task_info.completed = i + 1
    return {"pushed": pushed, "errors": errors}


# ---------------------------------------------------------------------------
# Bulk create
# ---------------------------------------------------------------------------

def bulk_create(
    *,
    location_id: int | None,
    latitude: float,
    longitude: float,
    radius_km: int,
    type_ids: list[int],
    log_event_ids: list[int],
    recipient_email: str,
    name_template: str = "{location} – {type}",
    enabled: bool = True,
    task_info=None,
) -> dict:
    """Create one notification per type and push each to server.

    Returns ``{'created': [{type_id, type_name, nid}], 'errors': [...]}``.
    """
    from preferences.models import ReferencePoint

    location_name = ""
    location = None
    if location_id:
        location = ReferencePoint.objects.filter(id=location_id).first()
        if location:
            location_name = location.name

    if task_info:
        task_info.phase = f"Creating {len(type_ids)} notification(s)"
        task_info.total = len(type_ids)
        task_info.completed = 0

    created: list[dict] = []
    errors: list[dict] = []

    for i, type_id in enumerate(type_ids):
        type_name = notify_constants.CACHE_TYPES.get(type_id, str(type_id))
        if task_info:
            task_info.phase = f"Creating {type_name} ({i + 1}/{len(type_ids)})"

        try:
            name = name_template.format(location=location_name or "Notification",
                                        type=type_name)
        except (KeyError, IndexError):
            name = f"{location_name or 'Notification'} - {type_name}"

        # Push to server first — only persist locally if the create succeeded.
        # Avoids leaving orphan ``server_id=""`` rows behind on failed pushes
        # (the user would then see them as "local only" on the next sync).
        try:
            nid = _notify_web().create_notification(
                name=name,
                latitude=latitude,
                longitude=longitude,
                radius_km=radius_km,
                cache_type_id=type_id,
                log_event_ids=list(log_event_ids),
                recipient_email=recipient_email,
                enabled=enabled,
            )
        except Exception as exc:
            logger.warning("Bulk-create push failed for type %s: %s", type_id, exc)
            errors.append({"type_id": type_id, "type_name": type_name, "error": str(exc)})
            if task_info:
                task_info.completed = i + 1
            continue

        payload = {
            "name": name,
            "latitude": latitude,
            "longitude": longitude,
            "radius_km": radius_km,
            "cache_type_id": type_id,
            "log_event_ids": list(log_event_ids),
            "recipient_email": recipient_email,
            "enabled": enabled,
        }
        from geocaches.db_lock import db_write_atomic
        with db_write_atomic():
            GCNotification.objects.create(
                source="gc",
                server_id=nid,
                last_synced_at=datetime.now(timezone.utc),
                server_hash=_compute_server_hash(payload),
                location=location,
                **payload,
            )
        created.append({"type_id": type_id, "type_name": type_name, "nid": nid})

        if task_info:
            task_info.completed = i + 1

    if task_info:
        task_info.phase = "Done"

    return {"created": created, "errors": errors}


# ---------------------------------------------------------------------------
# Region bulk operations
# ---------------------------------------------------------------------------

def _location_filter(location_id: int | None):
    qs = GCNotification.objects.filter(source="gc")
    if location_id is None:
        return qs.filter(location__isnull=True)
    return qs.filter(location_id=location_id)


def set_enabled_by_location(location_id: int | None, enabled: bool, task_info=None) -> int:
    """Enable or disable every notification in a location group."""
    qs = _location_filter(location_id).exclude(enabled=enabled)
    targets = list(qs)
    if task_info:
        task_info.phase = f"Setting {len(targets)} notification(s) {'enabled' if enabled else 'disabled'}"
        task_info.total = len(targets)
        task_info.completed = 0

    for i, n in enumerate(targets):
        if n.server_id:
            try:
                _notify_web().toggle_notification(n.server_id)
            except Exception as exc:
                logger.warning("Toggle failed for NID=%s: %s", n.server_id, exc)
                continue
            logger.info("GC notification %s: NID=%s %r (%s)",
                        "enabled" if enabled else "disabled",
                        n.server_id, n.name, _type_label(n.cache_type_id))
        n.enabled = enabled
        n.last_synced_at = datetime.now(timezone.utc)
        from geocaches.db_lock import db_write
        with db_write():
            n.save(update_fields=["enabled", "last_synced_at"])
        if task_info:
            task_info.completed = i + 1
    return len(targets)


def delete_by_location(location_id: int | None, task_info=None) -> int:
    qs = _location_filter(location_id)
    targets = list(qs)
    if task_info:
        task_info.phase = f"Deleting {len(targets)} notification(s)"
        task_info.total = len(targets)
        task_info.completed = 0

    for i, n in enumerate(targets):
        if n.server_id:
            try:
                _notify_web().delete_notification(n.server_id)
            except Exception as exc:
                logger.warning("Delete failed for NID=%s: %s", n.server_id, exc)
                continue
            logger.info("GC notification deleted: NID=%s %r (%s)",
                        n.server_id, n.name, _type_label(n.cache_type_id))
        from geocaches.db_lock import db_write
        with db_write():
            n.delete()
        if task_info:
            task_info.completed = i + 1
    return len(targets)
