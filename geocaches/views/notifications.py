"""Views for GC Instant Notification management."""
from __future__ import annotations

import logging

from django.contrib import messages
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from geocaches.feature_flags import gc_api_available
from geocaches.models import CacheType, GCNotification
from geocaches.services import notifications as svc
from geocaches.sync import notify_constants

logger = logging.getLogger("geocaches.notify")


# ---------------------------------------------------------------------------
# Main page
# ---------------------------------------------------------------------------

def _annotate(n) -> None:
    """Attach display-only attributes used by the template."""
    n.type_name = notify_constants.CACHE_TYPES.get(n.cache_type_id, str(n.cache_type_id))
    event_names = [notify_constants.LOG_EVENT_NAMES.get(eid, str(eid)) for eid in (n.log_event_ids or [])]
    n.log_event_names = ", ".join(event_names)
    n.log_event_ids_csv = ",".join(str(e) for e in (n.log_event_ids or []))


def _group_by_location(qs):
    """Return [{'location': RP|None, 'notifications': [...]}] in location order."""
    grouped: dict[int | None, list] = {}
    locations: dict[int | None, object] = {None: None}
    for n in qs.select_related("location"):
        _annotate(n)
        key = n.location_id if n.location_id else None
        grouped.setdefault(key, []).append(n)
        if key is not None and key not in locations:
            locations[key] = n.location

    def sort_key(item):
        key, _rows = item
        loc = locations.get(key)
        return (0 if loc is None else 1, (loc.name if loc else "").lower(), key or 0)

    out = []
    for key, rows in sorted(grouped.items(), key=sort_key):
        rows.sort(key=lambda n: (n.name.lower(), n.cache_type_id))
        out.append({
            "location": locations.get(key),
            "location_id": key,
            "notifications": rows,
            "enabled_count": sum(1 for n in rows if n.enabled),
            "total_count": len(rows),
        })
    return out


def notifications_page(request):
    from preferences.models import ReferencePoint
    from accounts.models import UserAccount
    from geocaches.models import OCNotification
    from geocaches.sync import oc_profile

    qs = GCNotification.objects.filter(source="gc")
    groups = _group_by_location(qs)

    # OC tab: one card per active OC UserAccount on a supported platform.
    # Group OC rows by platform; .de family has at most one row, .us can have many.
    # Hide legacy oc_us rows whose server_id is still empty (pre-multi-nbh) so the
    # user is prompted to Pull instead of seeing a broken form.
    oc_rows_by_platform: dict[str, list[OCNotification]] = {}
    for n in OCNotification.objects.select_related("location"):
        if n.platform == "oc_us" and not n.server_id:
            continue
        oc_rows_by_platform.setdefault(n.platform, []).append(n)
    for rows in oc_rows_by_platform.values():
        rows.sort(key=lambda r: (
            0 if r.server_id == "0" else 1,                     # default first
            int(r.server_id) if r.server_id.isdigit() else 999,  # then 1..N
        ))

    oc_cards = []
    for acct in UserAccount.objects.exclude(platform="gc").order_by("platform"):
        rows = oc_rows_by_platform.get(acct.platform, [])
        oc_cards.append({
            "platform": acct.platform,
            "platform_label": acct.get_platform_display(),
            "username": acct.username,
            "supported": acct.platform in oc_profile.SUPPORTED_PLATFORMS,
            "is_us": acct.platform == "oc_us",
            "is_de_family": acct.platform in oc_profile.DE_FAMILY,
            "rows": rows,
            # Backwards-compat single-row convenience for .de family (one row).
            "notification": rows[0] if rows else None,
            # Default-nbh row for .us (server_id="0"), used to display globals.
            "default_row": next((r for r in rows if r.server_id == "0"), None),
        })

    return render(request, "geocaches/notifications.html", {
        "groups": groups,
        "total": qs.count(),
        "locations": list(ReferencePoint.objects.order_by("name")),
        # `cache_types` (list of choices) is consumed by the shared map fetch
        # dialog rendered inside the notifications map; the notification type
        # dropdowns use `notify_cache_types` (id→name dict) to avoid clobbering it.
        "cache_types": CacheType.choices,
        "notify_cache_types": notify_constants.CACHE_TYPES,
        "log_events": notify_constants.LOG_EVENTS,
        "default_log_event_ids": GCNotification.DEFAULT_LOG_EVENT_IDS,
        "active_tab": request.GET.get("tab", "gc"),
        "oc_cards": oc_cards,
        "oc_max_radius": OCNotification.MAX_RADIUS_KM,
    })


# ---------------------------------------------------------------------------
# Sync
# ---------------------------------------------------------------------------

@require_POST
def notifications_sync(request):
    """Pull from server, classify, and route to the diff dialog if needed.

    Runs synchronously — each fetch is sub-second and full syncs typically
    touch <50 server rows.  If that grows, switch to ``submit_task``.
    """
    try:
        result = svc.sync_with_server()
    except Exception as exc:
        logger.exception("Notify sync failed")
        messages.error(request, _("Sync failed: %s") % exc)
        return redirect(reverse("geocaches:notifications"))

    if not result["local_only_ids"] and not result["server_deleted_ids"]:
        parts = []
        if result["auto_created"]:
            parts.append(_("%d created locally") % result["auto_created"])
        if result["auto_updated"]:
            parts.append(_("%d updated locally") % result["auto_updated"])
        if result["matched"]:
            parts.append(_("%d already in sync") % result["matched"])
        messages.success(request, _("Sync complete — %s.") % (", ".join(parts) or _("nothing changed")))
        return redirect(reverse("geocaches:notifications"))

    # Render the diff dialog page so the user can pick per-row.
    local_only = list(GCNotification.objects.filter(id__in=result["local_only_ids"]).select_related("location"))
    server_deleted = list(GCNotification.objects.filter(id__in=result["server_deleted_ids"]).select_related("location"))

    return render(request, "geocaches/notifications_diff.html", {
        "summary": result,
        "local_only": local_only,
        "server_deleted": server_deleted,
        "notify_cache_types": notify_constants.CACHE_TYPES,
    })


@require_POST
def notifications_apply_diff(request):
    """Apply the per-row choices submitted from the diff dialog."""
    applied = {"local_only": 0, "server_deleted": 0}
    errors = []

    # Local-only rows
    for key, value in request.POST.items():
        if key.startswith("local_only_"):
            try:
                nid = int(key[len("local_only_"):])
            except ValueError:
                continue
            try:
                svc.apply_local_only_action(nid, value)
                applied["local_only"] += 1
            except Exception as exc:
                logger.warning("apply_local_only_action(%s, %s) failed: %s", nid, value, exc)
                errors.append(str(exc))
        elif key.startswith("server_deleted_"):
            try:
                nid = int(key[len("server_deleted_"):])
            except ValueError:
                continue
            try:
                svc.apply_server_deleted_action(nid, value)
                applied["server_deleted"] += 1
            except Exception as exc:
                logger.warning("apply_server_deleted_action(%s, %s) failed: %s", nid, value, exc)
                errors.append(str(exc))

    if errors:
        messages.warning(request, _("Applied %d local-only and %d server-deleted choices; %d error(s): %s") % (
            applied["local_only"], applied["server_deleted"], len(errors), "; ".join(errors[:3])))
    else:
        messages.success(request, _("Applied %d local-only and %d server-deleted choices.") % (
            applied["local_only"], applied["server_deleted"]))

    return redirect(reverse("geocaches:notifications"))


# ---------------------------------------------------------------------------
# Bulk create
# ---------------------------------------------------------------------------

@require_POST
def notifications_bulk_create(request):
    from preferences.models import ReferencePoint

    location_id_raw = request.POST.get("location_id", "")
    location_id = int(location_id_raw) if location_id_raw else None

    if location_id:
        location = ReferencePoint.objects.filter(id=location_id).first()
        if not location:
            messages.error(request, _("Location not found."))
            return redirect(reverse("geocaches:notifications"))
        latitude = location.latitude
        longitude = location.longitude
    else:
        try:
            latitude = float(request.POST.get("latitude", ""))
            longitude = float(request.POST.get("longitude", ""))
        except ValueError:
            messages.error(request, _("Please pick a location or enter valid coordinates."))
            return redirect(reverse("geocaches:notifications"))

    try:
        radius_km = int(request.POST.get("radius_km", "20"))
    except ValueError:
        radius_km = 20
    radius_km = max(1, min(radius_km, 500))

    type_ids = [int(t) for t in request.POST.getlist("type_ids") if t.isdigit()]
    if not type_ids:
        messages.error(request, _("Select at least one cache type."))
        return redirect(reverse("geocaches:notifications"))

    log_event_ids = [int(e) for e in request.POST.getlist("log_event_ids") if e.lstrip("-").isdigit()]
    if not log_event_ids:
        log_event_ids = list(GCNotification.DEFAULT_LOG_EVENT_IDS)

    recipient_email = request.POST.get("recipient_email", "").strip()
    name_template = request.POST.get("name_template", "{location} – {type}").strip() or "{location} – {type}"
    enabled = request.POST.get("enabled") == "on"

    from geocaches.tasks import submit_task
    submit_task(
        _("Bulk-create %d notification(s)") % len(type_ids),
        svc.bulk_create,
        location_id=location_id,
        latitude=latitude,
        longitude=longitude,
        radius_km=radius_km,
        type_ids=type_ids,
        log_event_ids=log_event_ids,
        recipient_email=recipient_email,
        name_template=name_template,
        enabled=enabled,
    )
    return redirect(reverse("geocaches:notifications"))


# ---------------------------------------------------------------------------
# Per-notification actions
# ---------------------------------------------------------------------------

@require_POST
def notification_toggle(request, pk):
    n = get_object_or_404(GCNotification, pk=pk)
    try:
        new_state = svc.toggle_enabled(n.id)
    except Exception as exc:
        logger.exception("Toggle failed")
        messages.error(request, _("Toggle failed: %s") % exc)
        return redirect(reverse("geocaches:notifications"))
    messages.success(request,
                     _("Notification '%(name)s' %(state)s.") %
                     {"name": n.name, "state": _("enabled") if new_state else _("disabled")})
    return redirect(reverse("geocaches:notifications"))


@require_POST
def notification_delete(request, pk):
    n = get_object_or_404(GCNotification, pk=pk)
    name = n.name
    try:
        svc.delete_notification(n.id)
    except Exception as exc:
        logger.exception("Delete failed")
        messages.error(request, _("Delete failed: %s") % exc)
        return redirect(reverse("geocaches:notifications"))
    messages.success(request, _("Deleted notification '%s'.") % name)
    return redirect(reverse("geocaches:notifications"))


@require_POST
def notification_set_location(request, pk):
    """Inline-set the location FK from the per-row dropdown. Local-only — the
    location grouping is a GCForge concept and doesn't exist on the server.
    """
    n = get_object_or_404(GCNotification, pk=pk)
    raw = request.POST.get("location_id", "")
    if raw == "":
        n.location_id = None
    else:
        try:
            n.location_id = int(raw)
        except ValueError:
            messages.error(request, _("Invalid location."))
            return redirect(reverse("geocaches:notifications"))
    n.save(update_fields=["location_id", "updated_at"])
    return redirect(reverse("geocaches:notifications"))


@require_POST
def notification_edit(request, pk):
    """Update a notification's editable fields from the inline edit form."""
    n = get_object_or_404(GCNotification, pk=pk)
    fields = {
        "name": request.POST.get("name", n.name).strip() or n.name,
        "radius_km": max(1, min(int(request.POST.get("radius_km", n.radius_km) or n.radius_km), 500)),
        "log_event_ids": [int(e) for e in request.POST.getlist("log_event_ids") if e.lstrip("-").isdigit()],
        "recipient_email": request.POST.get("recipient_email", n.recipient_email).strip(),
    }
    fields["log_event_ids"] = fields["log_event_ids"] or list(GCNotification.DEFAULT_LOG_EVENT_IDS)

    location_id_raw = request.POST.get("location_id", "")
    if location_id_raw == "":
        fields["location_id"] = None
    else:
        try:
            fields["location_id"] = int(location_id_raw)
        except ValueError:
            pass

    try:
        latitude = request.POST.get("latitude")
        longitude = request.POST.get("longitude")
        if latitude:
            fields["latitude"] = float(latitude)
        if longitude:
            fields["longitude"] = float(longitude)
    except ValueError:
        pass

    try:
        svc.update_notification(n.id, **fields)
    except Exception as exc:
        logger.exception("Edit failed")
        messages.error(request, _("Edit failed: %s") % exc)
        return redirect(reverse("geocaches:notifications"))

    messages.success(request, _("Updated notification '%s'.") % fields["name"])
    return redirect(reverse("geocaches:notifications"))


# ---------------------------------------------------------------------------
# Per-row pull / push (local <-> server)
# ---------------------------------------------------------------------------

@require_POST
def notification_pull(request, pk):
    n = get_object_or_404(GCNotification, pk=pk)
    try:
        svc.pull_from_server(n.id)
    except Exception as exc:
        logger.exception("Pull failed")
        messages.error(request, _("Pull failed: %s") % exc)
        return redirect(reverse("geocaches:notifications"))
    messages.success(request, _("Pulled notification '%s' from server.") % n.name)
    return redirect(reverse("geocaches:notifications"))


@require_POST
def notification_push(request, pk):
    n = get_object_or_404(GCNotification, pk=pk)
    try:
        svc.push_to_server(n.id)
    except Exception as exc:
        logger.exception("Push failed")
        messages.error(request, _("Push failed: %s") % exc)
        return redirect(reverse("geocaches:notifications"))
    messages.success(request, _("Pushed notification '%s' to server.") % n.name)
    return redirect(reverse("geocaches:notifications"))


# ---------------------------------------------------------------------------
# Region bulk actions
# ---------------------------------------------------------------------------

def _parse_location_id(value: str) -> int | None:
    if value in ("", "none", "None"):
        return None
    try:
        return int(value)
    except ValueError:
        return None


@require_POST
def notifications_region_set_enabled(request):
    location_id = _parse_location_id(request.POST.get("location_id", ""))
    enabled = request.POST.get("enabled") == "1"
    try:
        n = svc.set_enabled_by_location(location_id, enabled)
    except Exception as exc:
        logger.exception("Region set_enabled failed")
        messages.error(request, _("Bulk update failed: %s") % exc)
        return redirect(reverse("geocaches:notifications"))
    messages.success(request,
                     _("%(n)d notification(s) %(state)s.") % {
                         "n": n, "state": _("enabled") if enabled else _("disabled"),
                     })
    return redirect(reverse("geocaches:notifications"))


@require_POST
def notifications_region_delete(request):
    location_id = _parse_location_id(request.POST.get("location_id", ""))
    try:
        n = svc.delete_by_location(location_id)
    except Exception as exc:
        logger.exception("Region delete failed")
        messages.error(request, _("Bulk delete failed: %s") % exc)
        return redirect(reverse("geocaches:notifications"))
    messages.success(request, _("Deleted %d notification(s).") % n)
    return redirect(reverse("geocaches:notifications"))


@require_POST
def notifications_region_pull(request):
    from geocaches.tasks import submit_task
    location_id = _parse_location_id(request.POST.get("location_id", ""))
    submit_task(
        _("Pull notifications from server"),
        svc.pull_by_location,
        location_id,
    )
    return redirect(reverse("geocaches:notifications"))


@require_POST
def notifications_region_push(request):
    from geocaches.tasks import submit_task
    location_id = _parse_location_id(request.POST.get("location_id", ""))
    submit_task(
        _("Push notifications to server"),
        svc.push_by_location,
        location_id,
    )
    return redirect(reverse("geocaches:notifications"))


# ---------------------------------------------------------------------------
# JSON: list alt emails (for the bulk-create form initial population)
# ---------------------------------------------------------------------------

def notifications_alt_emails(request):
    if not gc_api_available():
        return JsonResponse({"emails": []})
    from gcprivate import notify_web
    try:
        emails = notify_web.get_alt_emails()
    except Exception as exc:
        return JsonResponse({"error": str(exc)}, status=500)
    return JsonResponse({"emails": emails})


# ---------------------------------------------------------------------------
# OC notifications
# ---------------------------------------------------------------------------

@require_POST
def oc_notification_pull(request, platform):
    from geocaches.services import oc_notifications as oc_svc
    try:
        oc_svc.pull(platform)
    except Exception as exc:
        logger.exception("OC pull failed")
        messages.error(request, _("OC pull failed (%(p)s): %(e)s") % {"p": platform, "e": exc})
        return redirect(reverse("geocaches:notifications") + "?tab=oc")
    messages.success(request, _("Pulled %s notification settings from server.") % platform)
    return redirect(reverse("geocaches:notifications") + "?tab=oc")


@require_POST
def oc_notification_push(request, pk):
    from geocaches.services import oc_notifications as oc_svc
    try:
        n = oc_svc.push(pk)
    except Exception as exc:
        logger.exception("OC push failed")
        messages.error(request, _("OC push failed: %s") % exc)
        return redirect(reverse("geocaches:notifications") + "?tab=oc")
    messages.success(request, _("Pushed %s notification settings to server.") % n.platform)
    return redirect(reverse("geocaches:notifications") + "?tab=oc")


def _oc_resolve_coords(request):
    """Parse location_id / latitude / longitude POST inputs for OC save endpoints.

    Returns ``(location_id, latitude, longitude, error)`` — ``error`` is a
    user-facing message if parsing failed (then the other three are None).
    """
    from preferences.models import ReferencePoint

    location_id_raw = request.POST.get("location_id", "")
    location_id = int(location_id_raw) if location_id_raw else None
    if location_id:
        loc = ReferencePoint.objects.filter(id=location_id).first()
        if not loc:
            return None, None, None, _("Location not found.")
        return location_id, loc.latitude, loc.longitude, None
    try:
        latitude = float(request.POST.get("latitude", ""))
        longitude = float(request.POST.get("longitude", ""))
    except ValueError:
        return None, None, None, _("Please pick a location or enter valid coordinates.")
    return None, latitude, longitude, None


@require_POST
def oc_notification_save(request, pk):
    """Save changes to an existing OC notification row + push to server."""
    from geocaches.services import oc_notifications as oc_svc
    from geocaches.models import OCNotification

    n = get_object_or_404(OCNotification, pk=pk)

    location_id, latitude, longitude, err = _oc_resolve_coords(request)
    if err:
        messages.error(request, err)
        return redirect(reverse("geocaches:notifications") + "?tab=oc")

    try:
        radius_km = int(request.POST.get("radius_km", "20"))
    except ValueError:
        radius_km = 20

    # On .us, additional neighbourhoods don't carry their own enable state —
    # the master on the default row (server_id="0") is the only switch.
    if n.platform == "oc_us" and n.server_id != "0":
        # Inherit from the default row (or default True if no default exists).
        default = OCNotification.objects.filter(platform=n.platform, server_id="0").first()
        enabled = default.enabled if default else True
    else:
        enabled = request.POST.get("enabled") == "on"
    notify_oconly = request.POST.get("notify_oconly") == "on"
    notify_logs = request.POST.get("notify_logs") == "on"
    frequency = request.POST.get("frequency", "daily")
    if frequency not in {"hourly", "daily", "weekly"}:
        frequency = "daily"
    name = request.POST.get("name", n.name).strip()

    try:
        oc_svc.save_local(
            n.id,
            platform=n.platform,
            server_id=n.server_id,
            name=name,
            latitude=latitude,
            longitude=longitude,
            radius_km=radius_km,
            enabled=enabled,
            notify_oconly=notify_oconly,
            notify_logs=notify_logs,
            frequency=frequency,
            location_id=location_id,
            push_to_server=True,
        )
    except ValueError as exc:
        messages.error(request, str(exc))
        return redirect(reverse("geocaches:notifications") + "?tab=oc")
    except Exception as exc:
        logger.exception("OC save failed")
        messages.error(request, _("OC save failed: %s") % exc)
        return redirect(reverse("geocaches:notifications") + "?tab=oc")

    messages.success(request, _("Saved %s notification settings.") % n.platform)
    return redirect(reverse("geocaches:notifications") + "?tab=oc")


@require_POST
def oc_notification_save_globals(request, platform):
    """Save the platform-wide settings (master enable, notify_logs, frequency).

    Operates on the default neighbourhood row (server_id="0") since that's
    where we store the platform-global fields locally.
    """
    from geocaches.services import oc_notifications as oc_svc
    from geocaches.models import OCNotification

    n = OCNotification.objects.filter(platform=platform, server_id="0").first()
    if not n:
        messages.error(request, _("No default neighbourhood found — pull first."))
        return redirect(reverse("geocaches:notifications") + "?tab=oc")

    n.enabled = request.POST.get("enabled") == "on"
    n.notify_logs = request.POST.get("notify_logs") == "on"
    freq = request.POST.get("frequency", "daily")
    n.frequency = freq if freq in {"hourly", "daily", "weekly"} else "daily"
    n.save(update_fields=["enabled", "notify_logs", "frequency", "updated_at"])

    # Mirror the master state onto local additional rows so the UI stays
    # consistent (the server-side per-nbh toggles are normalised on push).
    OCNotification.objects.filter(platform=platform).exclude(server_id="0").update(
        enabled=n.enabled,
        notify_logs=n.notify_logs,
        frequency=n.frequency,
    )

    try:
        oc_svc.push(n.id)
    except Exception as exc:
        logger.exception("OC globals push failed")
        messages.error(request, _("Save failed: %s") % exc)
        return redirect(reverse("geocaches:notifications") + "?tab=oc")

    messages.success(request, _("Saved %s site-wide settings.") % platform)
    return redirect(reverse("geocaches:notifications") + "?tab=oc")


@require_POST
def oc_neighbourhood_create(request, platform):
    """Create a new additional neighbourhood on .us."""
    from geocaches.services import oc_notifications as oc_svc

    location_id, latitude, longitude, err = _oc_resolve_coords(request)
    if err:
        messages.error(request, err)
        return redirect(reverse("geocaches:notifications") + "?tab=oc")

    try:
        radius_km = int(request.POST.get("radius_km", "20"))
    except ValueError:
        radius_km = 20

    name = request.POST.get("name", "").strip()
    if not name:
        messages.error(request, _("A new neighbourhood needs a name."))
        return redirect(reverse("geocaches:notifications") + "?tab=oc")

    try:
        oc_svc.create_nbh(
            platform,
            name=name,
            latitude=latitude,
            longitude=longitude,
            radius_km=radius_km,
            location_id=location_id,
        )
    except Exception as exc:
        logger.exception("OC create neighbourhood failed")
        messages.error(request, _("Create failed: %s") % exc)
        return redirect(reverse("geocaches:notifications") + "?tab=oc")

    messages.success(request, _("Created neighbourhood '%s'.") % name)
    return redirect(reverse("geocaches:notifications") + "?tab=oc")


@require_POST
def oc_neighbourhood_delete(request, pk):
    """Delete an additional neighbourhood (server + local)."""
    from geocaches.services import oc_notifications as oc_svc
    from geocaches.models import OCNotification

    n = get_object_or_404(OCNotification, pk=pk)
    name = n.name
    try:
        oc_svc.delete_nbh(n.id)
    except Exception as exc:
        logger.exception("OC delete neighbourhood failed")
        messages.error(request, _("Delete failed: %s") % exc)
        return redirect(reverse("geocaches:notifications") + "?tab=oc")
    messages.success(request, _("Deleted neighbourhood '%s'.") % name)
    return redirect(reverse("geocaches:notifications") + "?tab=oc")


def notifications_map_circles(request):
    """JSON feed of all notification circles for the map overlay.

    Returns ``{"circles": [{id, name, lat, lon, radius_km, enabled, type, location}, …]}``.
    The map layer toggles client-side; we just dump everything once.
    """
    qs = GCNotification.objects.filter(source="gc").select_related("location")
    out = []
    for n in qs:
        out.append({
            "id": n.id,
            "name": n.name,
            "lat": n.latitude,
            "lon": n.longitude,
            "radius_km": n.radius_km,
            "enabled": n.enabled,
            "type": notify_constants.CACHE_TYPES.get(n.cache_type_id, str(n.cache_type_id)),
            "location": n.location.name if n.location else "",
        })
    return JsonResponse({"circles": out})
