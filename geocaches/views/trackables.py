"""Trackable views — JSON endpoints used by the log compose dialog.

Phase 2 will add full list/split/map/detail views; these endpoints are the
Phase-1 minimum needed to wire the cache-log compose dialog.
"""
from __future__ import annotations

import logging

from django.http import JsonResponse
from django.views.decorators.http import require_GET, require_POST

logger = logging.getLogger(__name__)


@require_GET
def trackable_inventory(request):
    """Return the authenticated user's GC TB inventory as JSON.

    Each row is enriched with the local ``auto_visit_enabled`` /
    ``auto_visit_text`` from a matching ``Trackable`` row when one exists,
    so the compose dialog can pre-check visits + populate text.
    """
    try:
        from geocaches.sync.gc_access import get_trackable_api_client
        items = get_trackable_api_client().get_my_inventory()
    except Exception as exc:  # noqa: BLE001
        logger.warning("trackable_inventory failed: %s", exc)
        return JsonResponse({"ok": False, "error": str(exc)}, status=200)

    from geocaches.models import Geocache, Trackable
    from geocaches.log_format import expand_placeholders

    refs = [t.get("reference_code", "") for t in items if t.get("reference_code")]
    local = {
        tb.reference_code: tb
        for tb in Trackable.objects.filter(reference_code__in=refs)
    }

    # Optional gc_code = the cache the compose dialog is open for. Used to
    # expand [name]/[gc_code]/etc. in auto_visit_text so the textarea shows
    # the final text instead of raw placeholders.
    cache = None
    gc_code = (request.GET.get("gc_code") or "").strip()
    if gc_code:
        cache = Geocache.objects.filter(gc_code=gc_code).first()

    out = []
    for t in items:
        ref = t.get("reference_code", "")
        tb = local.get(ref)
        raw_text = tb.auto_visit_text if tb else ""
        expanded_text = raw_text
        if raw_text:
            expanded_text = expand_placeholders(
                raw_text,
                cache=cache,
                log_type="Visited",
                trackable={
                    "reference_code": ref,
                    "name":           t.get("name", ""),
                    "owner_name":     "",
                },
            )
        out.append({
            "reference_code":     ref,
            "name":               t.get("name", ""),
            "icon_url":           t.get("icon_url", ""),
            "tracking_number":    t.get("tracking_number", ""),
            "type_name":          t.get("type_name", ""),
            "auto_visit_enabled": bool(tb.auto_visit_enabled) if tb else False,
            "auto_visit_text":    expanded_text,
        })
    return JsonResponse({"ok": True, "items": out})


@require_POST
def trackable_auto_visit_save(request):
    """Save the auto-visit settings for a trackable.

    POST fields: ref_code, enabled (0/1), text. A Trackable row is created
    on demand if we haven't interacted with this TB yet.
    """
    ref = (request.POST.get("ref_code") or "").strip().upper()
    if not ref:
        return JsonResponse({"ok": False, "error": "missing ref_code"}, status=400)
    enabled = (request.POST.get("enabled") or "0").strip() in ("1", "true", "on", "yes")
    text = request.POST.get("text") or ""
    from geocaches.models import Trackable
    tb, _ = Trackable.objects.get_or_create(
        reference_code=ref, defaults={"name": ref},
    )
    tb.auto_visit_enabled = enabled
    tb.auto_visit_text = text
    tb.save(update_fields=["auto_visit_enabled", "auto_visit_text", "updated_at"])
    return JsonResponse({"ok": True, "enabled": enabled, "text": text})


@require_POST
def trackable_verify(request):
    """Verify a tracking code via the GC API.

    Returns ``{ok, ref_code, name, current_geocache_code, holder}`` on success
    (holder is ``{username, reference_code, profile_url, message_url}`` or
    null), or ``{ok: False, error}`` on failure.
    """
    code = (request.POST.get("tracking_code") or "").strip()
    if not code:
        return JsonResponse({"ok": False, "error": "empty code"}, status=200)
    try:
        from geocaches.sync.gc_access import get_trackable_client
        data = get_trackable_client().verify_tracking_code(code)
    except Exception as exc:  # noqa: BLE001
        logger.info("trackable_verify rejected %s: %s", code, exc)
        return JsonResponse({"ok": False, "error": "code not recognised"}, status=200)
    return JsonResponse({
        "ok":                    True,
        "ref_code":              data.get("reference_code", ""),
        "name":                  data.get("name", ""),
        "current_geocache_code": data.get("current_geocache_code", ""),
        "holder":                data.get("holder"),
    })


@require_POST
def trackable_log_by_code(request):
    """Submit a standalone Discover/Retrieve trackable log by tracking code.

    Used by the trackables list view's "Log TB" bulk button — no specific
    cache is involved, unlike the cache-detail log-compose dialog's TB
    sections. POST fields: ``tracking_code``, ``action`` ∈ {discover, retrieve},
    ``text`` (required — GC rejects a trackable log with empty text).

    Returns ``{ok, ref_code, name}`` on success, or ``{ok: False, error}``.
    """
    code = (request.POST.get("tracking_code") or "").strip()
    action = (request.POST.get("action") or "").strip().lower()
    text = (request.POST.get("text") or "").strip()
    if action not in ("discover", "retrieve"):
        return JsonResponse({"ok": False, "error": f"unsupported action: {action!r}"}, status=200)
    if not code:
        return JsonResponse({"ok": False, "error": "empty code"}, status=200)
    if not text:
        return JsonResponse({"ok": False, "error": "log text required"}, status=200)

    from geocaches.sync.log_submit import submit_standalone_tb_action
    result = submit_standalone_tb_action(action, code, text)
    if not result.success:
        return JsonResponse({"ok": False, "error": result.error or "unknown error"}, status=200)
    return JsonResponse({"ok": True, "ref_code": result.ref_code})
