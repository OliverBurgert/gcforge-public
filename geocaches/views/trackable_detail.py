"""Trackable detail page views — Phase 2.

Four endpoints:
  trackable_detail        GET  trackables/<ref>/
  trackable_refresh       POST trackables/<ref>/refresh/
  trackable_action        POST trackables/<ref>/action/
  trackable_coords_save   POST trackables/<ref>/coords/
"""
from __future__ import annotations

import logging
import re
from datetime import UTC, datetime

from django.core.paginator import Paginator
from django.http import Http404, JsonResponse
from django.shortcuts import redirect, render
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from geocaches.models import Trackable, TrackableHolderState, TrackableKind

logger = logging.getLogger(__name__)

LOG_TYPE_FOR_ACTION: dict[str, str] = {
    "move_to_inventory":  "Move To Inventory",
    "move_to_collection": "Move To Collection",
    "mark_missing":       "Mark Missing",
}


def _get_tb(ref: str) -> Trackable:
    ref = (ref or "").strip().upper()
    tb = Trackable.objects.filter(reference_code=ref).first()
    if tb is None:
        raise Http404(f"No trackable with reference code {ref!r}")
    return tb


def trackable_detail(request, ref):
    tb = _get_tb(ref)

    logs_qs = tb.logs.all().select_related("geocache").order_by("-logged_date", "-logged_at")
    paginator = Paginator(logs_qs, 25)
    page_obj = paginator.get_page(request.GET.get("page", 1))

    movement_logs = (
        tb.logs.filter(geocache_lat__isnull=False)
        .order_by("logged_date", "logged_at")
        .values("id", "logged_date", "log_type", "geocache_ref_code", "geocache_lat", "geocache_lon", "text")
    )
    movements = []
    for lg in movement_logs:
        text = (lg["text"] or "").strip()
        movements.append({
            "id":           lg["id"],
            "date":         str(lg["logged_date"]),
            "type":         lg["log_type"],
            "code":         lg["geocache_ref_code"],
            "lat":          lg["geocache_lat"],
            "lon":          lg["geocache_lon"],
            "text_snippet": text[:120] + ("…" if len(text) > 120 else ""),
        })

    from preferences.models import ReferencePoint

    home_ref = ReferencePoint.objects.filter(is_home=True).first()
    holder_state_label = dict(TrackableHolderState.choices).get(tb.holder_state, tb.holder_state)
    kind_label = dict(TrackableKind.choices).get(tb.kind, tb.kind)
    images = list(tb.images.filter(log__isnull=True).order_by("uploaded_at", "id"))
    from geocaches.models import Tag
    tags = list(tb.tags.order_by("name"))
    all_tags = list(Tag.objects.order_by("name").values_list("name", flat=True))

    return render(request, "geocaches/trackable_detail.html", {
        "tb":                 tb,
        "page_obj":           page_obj,
        "movements":          movements,
        "home_ref":           home_ref,
        "holder_state_label": holder_state_label,
        "kind_label":         kind_label,
        "images":             images,
        "tags":               tags,
        "all_tags":           all_tags,
        "external_url":       f"https://coord.info/{tb.reference_code}",
        "edit_url":           f"https://coord.info/{tb.reference_code}/edit",
        "embed":              request.GET.get("embed") == "1",
    })


@require_POST
def trackable_refresh(request, ref):
    tb = _get_tb(ref)
    is_htmx = bool(request.headers.get("HX-Request"))

    try:
        from geocaches.services.trackable_sync import sync_trackable, sync_trackable_logs
        tb = sync_trackable(ref)
        new_logs = sync_trackable_logs(ref, full=False)
        tb.refresh_from_db()
    except Exception as exc:  # noqa: BLE001
        logger.warning("trackable_refresh failed for %s: %s", ref, exc)
        if is_htmx:
            return JsonResponse({"ok": False, "error": str(exc)})
        from django.contrib import messages
        messages.error(request, _("Refresh failed: %(error)s") % {"error": exc})
        return redirect("geocaches:trackable_detail", ref=ref)

    if is_htmx:
        return JsonResponse({
            "ok":            True,
            "new_logs":      new_logs,
            "holder_state":  tb.holder_state,
            "last_log_date": str(tb.last_log_date) if tb.last_log_date else None,
        })
    return redirect("geocaches:trackable_detail", ref=ref)


@require_POST
def trackable_action(request, ref):
    action = (request.POST.get("action") or "").strip()
    log_type = LOG_TYPE_FOR_ACTION.get(action)
    if not log_type:
        return JsonResponse({"ok": False, "error": f"Unknown action: {action!r}"}, status=400)

    tb = _get_tb(ref)
    text = request.POST.get("text", "")
    iso = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S")

    try:
        from gcprivate.trackable_client import TrackableClient
        TrackableClient().submit_trackable_log(ref, log_type, iso, text)
    except Exception as exc:  # noqa: BLE001
        err_str = str(exc)
        logger.warning("trackable_action %s on %s failed: %s", action, ref, err_str)
        hint = None
        if "not collectible" in err_str.lower():
            hint = f"https://coord.info/{tb.reference_code}/edit"
        return JsonResponse({"ok": False, "error": err_str, "hint": hint})

    try:
        from geocaches.services.trackable_sync import sync_trackable, sync_trackable_logs
        tb = sync_trackable(ref)
        sync_trackable_logs(ref, full=False)
        tb.refresh_from_db()
    except Exception as exc:  # noqa: BLE001
        logger.warning("trackable_action post-sync failed for %s: %s", ref, exc)

    return JsonResponse({"ok": True, "holder_state": tb.holder_state})


@require_POST
def trackable_tags_save(request, ref):
    """Replace a trackable's tag set from a comma-separated POST body.

    Creates tags on demand (matching cache_tag_edit behaviour) so the user
    can type a new tag name and have it auto-create.
    """
    from geocaches.models import Tag
    tb = _get_tb(ref)
    raw = (request.POST.get("tags", "") or "").strip()
    names = [n.strip() for n in raw.split(",") if n.strip()]
    tag_objs = []
    for name in names:
        tag, __ = Tag.objects.get_or_create(name=name)
        tag_objs.append(tag)
    tb.tags.set(tag_objs)
    return redirect("geocaches:trackable_detail", ref=tb.reference_code)


@require_POST
def trackable_tracking_code_save(request, ref):
    """Manually set/clear a TB's private tracking code (for found/discovered
    TBs we don't own — owners use the website-scrape sync instead)."""
    tb = _get_tb(ref)
    code = (request.POST.get("tracking_code") or "").strip().upper()
    if not re.fullmatch(r"[A-Z0-9]{0,20}", code):
        return JsonResponse({"ok": False, "error": "invalid characters"}, status=400)
    tb.tracking_code = code
    tb.save(update_fields=["tracking_code", "updated_at"])
    return redirect("geocaches:trackable_detail", ref=tb.reference_code)


@require_POST
def trackable_delete(request, ref):
    """Delete a Trackable locally. Cascades to TrackableLog / TrackableImage.

    Does not touch the GC.com side — the TB still exists upstream and can be
    re-synced by visiting the page again (which 404s) or by `refresh_trackable`
    from the CLI.
    """
    tb = _get_tb(ref)
    tb.delete()
    return redirect("geocaches:list")


@require_POST
def trackable_coords_save(request, ref):
    tb = _get_tb(ref)
    lat_str = (request.POST.get("lat") or "").strip()
    lon_str = (request.POST.get("lon") or "").strip()

    if not lat_str and not lon_str:
        tb.current_lat = None
        tb.current_lon = None
        tb.coords_user_override = False
        tb.save(update_fields=["current_lat", "current_lon", "coords_user_override", "updated_at"])
        try:
            from geocaches.services.trackable_sync import recompute_trackable_denorms
            recompute_trackable_denorms(tb)
        except Exception as exc:  # noqa: BLE001
            logger.warning("recompute after coords clear failed: %s", exc)
        return JsonResponse({"ok": True, "lat": None, "lon": None})

    try:
        lat = float(lat_str)
        lon = float(lon_str)
    except (ValueError, TypeError):
        return JsonResponse({"ok": False, "error": "Invalid lat/lon"}, status=400)

    tb.current_lat = lat
    tb.current_lon = lon
    tb.coords_user_override = True
    tb.save(update_fields=["current_lat", "current_lon", "coords_user_override", "updated_at"])
    return JsonResponse({"ok": True, "lat": lat, "lon": lon})
