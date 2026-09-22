import json
import logging
from datetime import datetime, timezone

from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.utils.translation import gettext as _

logger = logging.getLogger(__name__)


def _pq_generation_pst_date(pq):
    """Parse a PQ dict's lastUpdatedDateUtc into a PST date, or None.

    A referenceCode is stable across regenerations (it names the saved query
    slot, not a particular run) — the generation date is what actually
    distinguishes "this run" for the ``pq_imported`` tracking key, see
    ``geocaches.pq.service.pq_import_key()``.
    """
    from zoneinfo import ZoneInfo
    raw_utc = pq.get("lastUpdatedDateUtc", "")
    if not raw_utc:
        return None
    try:
        dt = datetime.fromisoformat(raw_utc.rstrip("Z")).replace(tzinfo=timezone.utc)
        return dt.astimezone(ZoneInfo("America/Los_Angeles")).date()
    except (ValueError, TypeError):
        return None


def _pq_is_imported(pq, imported_pqs) -> bool:
    """Was *this* generation of ``pq`` already downloaded — see
    ``pq_import_key()`` for why this can't just check the bare referenceCode."""
    from geocaches.pq.service import pq_import_key
    ref = pq.get("referenceCode", "")
    gen_date = _pq_generation_pst_date(pq)
    if not ref or gen_date is None:
        return False
    return pq_import_key(ref, gen_date) in imported_pqs


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
        pq["imported"] = _pq_is_imported(pq, imported_pqs)
        # Passed back as a hidden field so a manual single/multi "Download"
        # action can mark *this* generation imported, not "today" — see
        # views/pq.py's download action and pq.service.pq_import_key().
        gen_date = _pq_generation_pst_date(pq)
        pq["import_date"] = gen_date.isoformat() if gen_date else ""
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

        def _row_generation_date(ref):
            # The row's own "Last Generated" PST date (see
            # _annotate_pqs/_pq_generation_pst_date) — not necessarily today,
            # e.g. a still-downloadable PQ that last ran a few days ago.
            # Threaded into pq_import_key() so a manual download of a
            # non-fresh row doesn't get marked imported under today's date.
            from datetime import date
            raw = request.POST.get(f"date_{ref}", "")
            try:
                return date.fromisoformat(raw) if raw else None
            except ValueError:
                return None

        if action == "download":
            if not selected:
                error = "No pocket queries selected."
            else:
                from geocaches.pq.service import enqueue_pq_download
                queue_task_id = None
                for ref in selected:
                    queue_task_id = enqueue_pq_download(
                        ref, _row_name(ref), tag_names=pq_tags.get(ref),
                        generation_date=_row_generation_date(ref),
                    )
                if queue_task_id:
                    return redirect(f"{request.path}?task_id={queue_task_id}")

        elif action == "trigger":
            items = [(_row_guid(ref), _row_name(ref)) for ref in selected if _row_guid(ref)]
            if not items:
                error = "No triggerable pocket queries selected."
            else:
                from geocaches.pq.service import bulk_trigger
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
                from geocaches.pq.service import bulk_trigger_and_download
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
                from geocaches.pq.service import bulk_delete
                task_id = submit_task("PQ: Delete selected", bulk_delete, items)
                return redirect(f"{request.path}?task_id={task_id}")

        elif action == "download_all":
            try:
                from geocaches.pq.service import list_pocket_queries, download_all_fresh, get_imported_pqs
                pq_list = list_pocket_queries()

                # Annotate with already_ran (from web session) and imported flags so
                # download_all_fresh can filter to "Ready (fresh)" + not yet imported.
                imported_pqs = get_imported_pqs()
                ws_map = {}
                try:
                    from geocaches.pq.trigger import get_pq_web_status
                    web_rows, _ = get_pq_web_status()
                    for wr in web_rows:
                        if wr["name"]:
                            ws_map[wr["name"]] = wr
                except Exception:
                    pass

                for pq in pq_list:
                    ws = ws_map.get(pq.get("name", ""), {})
                    pq["already_ran"] = ws.get("already_ran", False)
                    pq["imported"] = _pq_is_imported(pq, imported_pqs)

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
                from geocaches.pq.service import trigger_pqs_by_pattern
                task_id = submit_task(
                    f"PQ trigger: *{pattern}*",
                    trigger_pqs_by_pattern, pattern,
                )
                return redirect(f"{request.path}?task_id={task_id}")

        elif action == "trigger_download_pattern":
            pattern = request.POST.get("trigger_pattern", "").strip()
            if pattern:
                from geocaches.pq.service import trigger_and_download_by_pattern
                task_id = submit_task(
                    f"PQ: Trigger + download *{pattern}*",
                    trigger_and_download_by_pattern, pattern, tag_map=pq_tags,
                )
                return redirect(f"{request.path}?task_id={task_id}")

    # GET: fetch PQ list
    try:
        from geocaches.pq.service import list_pocket_queries
        pqs = list_pocket_queries()
    except Exception as exc:
        error = str(exc)

    # Website status (GUIDs, trigger availability, deleted state) is fetched in
    # the background — read the cache and kick a refresh, never block here.
    from geocaches.pq.service import (
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
    from geocaches.pq.service import (
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
        from geocaches.pq.service import list_pocket_queries
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
        from geocaches.pq.trigger import match_pqs_by_pattern
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


def pq_split_preview(request):
    """JSON endpoint: propose hidden_date split boundaries for the checked PQs.

    POSTs the whole ``#pq-form`` (same ``selected``/``name_<ref>``/``guid_<ref>``
    fields the bulk-action submit uses), so no new per-row markup is needed.
    """
    from preferences.models import UserPreference

    selected = request.POST.getlist("selected")
    if not selected:
        return JsonResponse({"error": "No PQs selected."}, status=400)

    def _row_name(ref):
        return request.POST.get(f"name_{ref}", ref)

    def _row_guid(ref):
        return request.POST.get(f"guid_{ref}", "")

    items = [
        {"reference_code": ref, "name": _row_name(ref), "guid": _row_guid(ref)}
        for ref in selected
    ]
    missing_guid = [i["name"] for i in items if not i["guid"]]
    if missing_guid:
        return JsonResponse({
            "error": (
                "Missing website GUID for: " + ", ".join(missing_guid) +
                " — reload the page and try again once website status has loaded."
            ),
        }, status=400)

    pq_tags = UserPreference.get("pq_tag_map", {})

    try:
        from geocaches.pq.split import compute_split_proposal
        result = compute_split_proposal(items, pq_tags)
    except Exception as exc:
        return JsonResponse({"error": str(exc)}, status=500)

    return JsonResponse(result)


def pq_apply_split(request):
    """JSON endpoint: write a confirmed split proposal back into each PQ on
    geocaching.com.

    Takes the JSON ``buckets`` list the split-preview response already
    produced (round-tripped by the client as-is, not re-form-encoded through
    ``#pq-form`` — the modal already holds it from the preview call).
    """
    try:
        payload = json.loads(request.body)
        items = payload.get("items", [])
    except (json.JSONDecodeError, AttributeError):
        return JsonResponse({"error": "Invalid request."}, status=400)
    if not items:
        return JsonResponse({"error": "No items to apply."}, status=400)

    from geocaches.pq.split import apply_split
    results = apply_split(items)
    return JsonResponse({"results": results})


# ---------------------------------------------------------------------------
# Website-only Pocket Query download (no partner-API token needed) — added
# 2026-08-15. Deliberately a separate, simpler view rather than merged into
# pq_management above: the two backends' data shapes don't unify cleanly
# (referenceCode + full lastUpdatedDateUtc timestamp vs. guid + date-only
# "last generated"), same reasoning as cache_fetch.py's standalone treatment
# rather than forcing it into sync_caches()'s BasePlatformClient shape.
#
# Also covers triggering new runs (added 2026-08-15) — pq/trigger.py's
# Active-tab trigger functions were already web-only before this effort,
# but the "wait until ready" + download steps weren't, so this page now
# offers both: download already-ready PQs immediately, or trigger +
# wait + download Active ones via pq.service.trigger_and_download_web().
# ---------------------------------------------------------------------------

def pq_management_web(request):
    """Pocket Query listing, triggering, and download via the website — no API token needed."""
    from geocaches.pq.service import get_imported_pqs, pq_import_key
    from geocaches.pq.trigger import get_pq_web_status, list_downloadable_pqs_web
    from geocaches.tasks import get_task, submit_task

    error = None
    task_id = request.GET.get("task_id", "")

    if request.method == "POST":
        action = request.POST.get("action", "download")
        selected_guids = request.POST.getlist("selected")
        tag_names = [t.strip() for t in request.POST.get("tags", "").split(",") if t.strip()]
        names = {g: request.POST.get(f"name_{g}", g) for g in selected_guids}

        if not selected_guids:
            error = _("Select at least one Pocket Query.")
        elif action == "trigger":
            def _run_trigger(guids, names_map, tag_list, task_info=None):
                from geocaches.pq.service import trigger_and_download_web
                return trigger_and_download_web(
                    guids, names=names_map, tag_names=tag_list, task_info=task_info,
                )

            task_id = submit_task(
                f"Trigger & download {len(selected_guids)} Pocket "
                f"Quer{'y' if len(selected_guids) == 1 else 'ies'} via website",
                _run_trigger, selected_guids, names, tag_names,
            )
        else:
            def _run_download(guids, names_map, tag_list, task_info=None):
                from geocaches.pq.service import download_and_import_pq_web

                if task_info:
                    task_info.total = len(guids)
                created = updated = locked = 0
                errors = []
                for i, guid in enumerate(guids):
                    if task_info and task_info.cancel_event.is_set():
                        break
                    name = names_map.get(guid, guid)
                    try:
                        result = download_and_import_pq_web(guid, name, tag_names=tag_list)
                        created += result.get("created", 0)
                        updated += result.get("updated", 0)
                        locked += result.get("locked", 0)
                        errors.extend(result.get("errors", []))
                    except Exception as exc:
                        errors.append(f"{name}: {exc}")
                    if task_info:
                        task_info.completed = i + 1

                summary = {"created": created, "updated": updated, "locked": locked, "errors": errors[:20]}
                if task_info:
                    task_info.result = summary
                return summary

            task_id = submit_task(
                f"Download {len(selected_guids)} Pocket Quer{'y' if len(selected_guids) == 1 else 'ies'} via website",
                _run_download, selected_guids, names, tag_names,
            )

    task_result = get_task(task_id) if task_id else None

    rows = []
    active_rows = []
    try:
        imported_pqs = get_imported_pqs()
        rows = list_downloadable_pqs_web()
        for row in rows:
            # Keyed by (guid, generation date) — see pq_import_key() — not
            # bare guid, so a recurring PQ's badge reflects *this*
            # generation, not "was ever imported at some point in the past".
            gen_date = row.get("last_generated_date")
            row["imported"] = bool(gen_date) and pq_import_key(row["guid"], gen_date) in imported_pqs
        active_rows, _summary = get_pq_web_status()
        active_rows = [r for r in active_rows if r.get("guid") and not r.get("is_deleted")]
    except Exception as exc:
        error = error or str(exc)

    return render(request, "geocaches/pq_management_web.html", {
        "rows": rows,
        "active_rows": active_rows,
        "error": error,
        "task_id": task_id,
        "task": task_result,
    })


def pq_web_status(request, task_id):
    """HTMX polling endpoint for the website-PQ download task."""
    from geocaches.tasks import get_task
    info = get_task(task_id)
    return render(request, "geocaches/partials/_pq_web_status.html", {
        "task_id": task_id, "task": info,
    })
