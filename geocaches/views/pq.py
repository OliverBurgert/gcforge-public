import logging
from datetime import datetime, timezone

from django.http import JsonResponse
from django.shortcuts import redirect, render

logger = logging.getLogger(__name__)


def _annotate_pqs(pqs, web_rows, imported_pqs, pq_tags):
    """Attach display fields to each API PQ from the website status + tags.

    ``web_rows`` is the cached website scrape, or ``None`` if it hasn't loaded
    yet (web-derived fields stay blank and ``is_deleted`` is left False until
    the background check delivers data).
    """
    from zoneinfo import ZoneInfo
    gc_tz = ZoneInfo("America/Los_Angeles")
    local_tz = datetime.now().astimezone().tzinfo

    web_known = web_rows is not None
    web_map = {wr["name"]: wr for wr in (web_rows or []) if wr.get("name")}

    for pq in pqs:
        ref = pq.get("referenceCode", "")
        name = pq.get("name", "")
        ws = web_map.get(name, {})
        pq["saved_tags"] = ", ".join(pq_tags.get(ref, []))
        pq["guid"] = ws.get("guid", "")
        pq["delete_id"] = ws.get("delete_id", "")
        pq["can_trigger"] = bool(ws.get("trigger_url"))
        pq["already_ran"] = ws.get("already_ran", False)
        pq["already_sched"] = ws.get("already_sched", False)
        pq["imported"] = ref in imported_pqs
        # Deleted = the website no longer lists it as an active PQ (gone) or
        # shows it struck through.  Only meaningful once web data has loaded.
        pq["is_deleted"] = web_known and (name not in web_map or ws.get("is_deleted", False))

        raw_utc = pq.get("lastUpdatedDateUtc", "")
        pq["local_time"] = ""
        pq["server_time"] = ""
        if raw_utc:
            try:
                dt = datetime.fromisoformat(raw_utc.rstrip("Z")).replace(tzinfo=timezone.utc)
                pq["local_time"] = dt.astimezone(local_tz).strftime("%Y-%m-%d %H:%M")
                pq["server_time"] = dt.astimezone(gc_tz).strftime("%Y-%m-%d %H:%M")
            except (ValueError, TypeError):
                pass
    return pqs


def pq_management(request):
    from preferences.models import UserPreference
    from geocaches.tasks import submit_task, get_task

    error = None
    pqs = []
    task_id = None
    task_result = None

    # Load saved PQ tag mappings
    pq_tags = UserPreference.get("pq_tag_map", {})

    # Check for completed task result
    result_task_id = request.GET.get("task_id")
    if result_task_id:
        task_data = get_task(result_task_id)
        if task_data:
            task_result = task_data
            # Trigger/download "parent" tasks hand the actual download off to the
            # sequential queue worker and return its id.  Once the parent has
            # finished, follow the queue task so the page shows live download
            # progress and the real created/updated counts instead of zeros.
            res = task_data.get("result") or {}
            queue_id = res.get("queue_task_id")
            if task_data.get("state") == "completed" and queue_id and queue_id != result_task_id:
                queue_data = get_task(queue_id)
                if queue_data:
                    task_result = queue_data

    if request.method == "POST":
        action = request.POST.get("action", "")

        # Save tag mappings from form (only if changed)
        old_pq_tags = dict(pq_tags)
        for key, value in request.POST.items():
            if key.startswith("tags_"):
                ref = key[5:]
                tags = [t.strip() for t in value.split(",") if t.strip()]
                if tags:
                    pq_tags[ref] = tags
                elif ref in pq_tags:
                    del pq_tags[ref]
        if pq_tags != old_pq_tags:
            UserPreference.set("pq_tag_map", pq_tags)

        # Selection-based bulk actions operate on the checked rows.  Each row
        # submits its own identifiers (name_/guid_/del_ keyed by referenceCode),
        # so there's no website↔API name matching to go wrong.
        selected = request.POST.getlist("selected")

        def _row_name(ref):
            return request.POST.get(f"name_{ref}", ref)

        def _row_guid(ref):
            return request.POST.get(f"guid_{ref}", "")

        def _row_delid(ref):
            return request.POST.get(f"del_{ref}", "")

        if action == "download":
            if not selected:
                error = "No pocket queries selected."
            else:
                from geocaches.pq_service import enqueue_pq_download
                queue_task_id = None
                for ref in selected:
                    queue_task_id = enqueue_pq_download(
                        ref, _row_name(ref), tag_names=pq_tags.get(ref),
                    )
                if queue_task_id:
                    return redirect(f"{request.path}?task_id={queue_task_id}")

        elif action == "trigger":
            items = [(_row_guid(ref), _row_name(ref)) for ref in selected if _row_guid(ref)]
            if not items:
                error = "No triggerable pocket queries selected."
            else:
                from geocaches.pq_service import bulk_trigger
                task_id = submit_task("PQ: Trigger selected", bulk_trigger, items)
                return redirect(f"{request.path}?task_id={task_id}")

        elif action == "trigger_download":
            items = [
                {"ref": ref, "guid": _row_guid(ref), "name": _row_name(ref)}
                for ref in selected if _row_guid(ref)
            ]
            if not items:
                error = "No triggerable pocket queries selected."
            else:
                from geocaches.pq_service import bulk_trigger_and_download
                task_id = submit_task(
                    "PQ: Trigger + download selected",
                    bulk_trigger_and_download, items, tag_map=pq_tags,
                )
                return redirect(f"{request.path}?task_id={task_id}")

        elif action == "delete":
            items = [(_row_delid(ref), _row_name(ref)) for ref in selected if _row_delid(ref)]
            if not items:
                error = "No deletable pocket queries selected."
            else:
                from geocaches.pq_service import bulk_delete
                task_id = submit_task("PQ: Delete selected", bulk_delete, items)
                return redirect(f"{request.path}?task_id={task_id}")

        elif action == "download_all":
            try:
                from geocaches.pq_service import list_pocket_queries, download_all_fresh, get_imported_pqs
                pq_list = list_pocket_queries()

                # Annotate with already_ran (from web session) and imported flags so
                # download_all_fresh can filter to "Ready (fresh)" + not yet imported.
                imported_pqs = get_imported_pqs()
                ws_map = {}
                try:
                    from geocaches.pq_trigger import get_pq_web_status
                    web_rows, _ = get_pq_web_status()
                    for wr in web_rows:
                        if wr["name"]:
                            ws_map[wr["name"]] = wr
                except Exception:
                    pass

                for pq in pq_list:
                    ws = ws_map.get(pq.get("name", ""), {})
                    pq["already_ran"] = ws.get("already_ran", False)
                    pq["imported"] = pq.get("referenceCode", "") in imported_pqs

                task_id = submit_task(
                    "PQ: Download all new",
                    download_all_fresh,
                    pq_list, tag_map=pq_tags,
                )
                return redirect(f"{request.path}?task_id={task_id}")
            except Exception as exc:
                error = str(exc)

        elif action == "trigger_pattern":
            pattern = request.POST.get("trigger_pattern", "").strip()
            if pattern:
                from geocaches.pq_service import trigger_pqs_by_pattern
                task_id = submit_task(
                    f"PQ trigger: *{pattern}*",
                    trigger_pqs_by_pattern, pattern,
                )
                return redirect(f"{request.path}?task_id={task_id}")

        elif action == "trigger_download_pattern":
            pattern = request.POST.get("trigger_pattern", "").strip()
            if pattern:
                from geocaches.pq_service import trigger_and_download_by_pattern
                task_id = submit_task(
                    f"PQ: Trigger + download *{pattern}*",
                    trigger_and_download_by_pattern, pattern, tag_map=pq_tags,
                )
                return redirect(f"{request.path}?task_id={task_id}")

    # GET: fetch PQ list
    try:
        from geocaches.pq_service import list_pocket_queries
        pqs = list_pocket_queries()
    except Exception as exc:
        error = str(exc)

    # Website status (GUIDs, trigger availability, deleted state) is fetched in
    # the background — read the cache and kick a refresh, never block here.
    from geocaches.pq_service import (
        ensure_web_status_fresh, get_web_status_snapshot, get_imported_pqs,
    )
    ensure_web_status_fresh()
    snap = get_web_status_snapshot()
    web_rows = snap["rows"]
    pq_summary = snap["summary"] or {}
    web_ready = web_rows is not None and not snap["refreshing"]

    imported_pqs = get_imported_pqs()
    _annotate_pqs(pqs, web_rows, imported_pqs, pq_tags)

    # Whether the GC website can be driven (trigger/delete) — based on having
    # credentials, not on the (possibly still-loading) scrape.
    from accounts.models import UserAccount
    has_web_session = UserAccount.objects.filter(platform="gc").exists()

    # Existing tags for quick-pick
    from geocaches.models import Tag
    all_tags = list(Tag.objects.order_by("name").values_list("name", flat=True))

    return render(request, "geocaches/pq_management.html", {
        "pqs": pqs,
        "error": error,
        "task_result": task_result,
        "all_tags": all_tags,
        "has_web_session": has_web_session,
        "pq_summary": pq_summary,
        "web_ready": web_ready,
    })


def pq_rows_json(request):
    """Return the table rows rendered with website status, once it's ready.

    The PQ page polls this after load: while the background scrape is still
    running it replies ``{"ready": false}``; once the cache is populated it
    returns the server-rendered ``<tbody>`` HTML (with the right chips +
    per-row guid/delete-id) for the page to swap in.
    """
    from django.template.loader import render_to_string
    from preferences.models import UserPreference
    from geocaches.pq_service import (
        ensure_web_status_fresh, get_web_status_snapshot, get_imported_pqs,
        list_pocket_queries,
    )

    ensure_web_status_fresh()
    snap = get_web_status_snapshot()
    if snap["rows"] is None or snap["refreshing"]:
        return JsonResponse({"ready": False})

    try:
        pqs = list_pocket_queries()
    except Exception as exc:
        return JsonResponse({"ready": False, "error": str(exc)})

    pq_tags = UserPreference.get("pq_tag_map", {})
    _annotate_pqs(pqs, snap["rows"], get_imported_pqs(), pq_tags)
    html = render_to_string("geocaches/_pq_rows.html", {"pqs": pqs}, request=request)
    return JsonResponse({"ready": True, "html": html})


def pq_list_json(request):
    """JSON endpoint returning the current PQ list (for polling refresh)."""
    try:
        from geocaches.pq_service import list_pocket_queries
        pqs = list_pocket_queries()
    except Exception as exc:
        return JsonResponse({"error": str(exc)}, status=500)

    result = []
    for pq in pqs:
        result.append({
            "referenceCode": pq.get("referenceCode", ""),
            "name": pq.get("name", ""),
            "count": pq.get("count"),
            "lastUpdatedDateUtc": pq.get("lastUpdatedDateUtc", ""),
        })
    return JsonResponse({"pqs": result})


def pq_match_preview(request):
    """JSON endpoint: preview which PQs match a name pattern."""
    pattern = request.GET.get("pattern", "").strip()
    if not pattern:
        return JsonResponse({"error": "No pattern specified."}, status=400)

    try:
        from geocaches.pq_trigger import match_pqs_by_pattern
        matching, summary = match_pqs_by_pattern(pattern)
    except Exception as exc:
        return JsonResponse({"error": str(exc)}, status=500)

    return JsonResponse({
        "matching": [
            {
                "name": r["name"],
                "already_ran": r["already_ran"],
                "already_sched": r["already_sched"],
                "has_trigger_url": bool(r["trigger_url"]),
            }
            for r in matching
        ],
        "summary": summary,
    })
