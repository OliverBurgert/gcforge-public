from django.contrib import messages
from django.core.paginator import Paginator
from django.db import models as db_models
from django.http import HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext as _, ngettext

from geocaches.models import CacheStatus, IgnoreListEntry, IgnoreSource
from geocaches.services.ignore_list import (
    add_internal,
    add_gc,
    add_oc,
    remove_gc,
    remove_oc,
    sync_gc_ignore_list,
    sync_oc_ignore_list,
    remove_archived as svc_remove_archived,
)

_PAGE_SIZE = 50

_SORT_FIELDS = {
    "code": "code",
    "source": "source",
    "name": "name",
    "status": "status",
    "last_status_refresh": "last_status_refresh",
    "added": "added",
}


def _apply_filters(qs, params):
    source_f = params.get("source", "")
    platform_f = params.get("oc_platform", "")
    status_f = params.get("status", "")
    db_f = params.get("db", "")
    q = (params.get("q", "") or "").strip()

    if source_f:
        qs = qs.filter(source=source_f)
    if platform_f:
        qs = qs.filter(oc_platform=platform_f)
    if status_f:
        qs = qs.filter(status=status_f)
    if q:
        qs = qs.filter(
            db_models.Q(code__icontains=q)
            | db_models.Q(name__icontains=q)
            | db_models.Q(notes__icontains=q)
        )
    if db_f in ("in_db", "found", "in_db_found"):
        from geocaches.models import Geocache
        from django.db.models import Exists, OuterRef
        if db_f == "in_db":
            qs = qs.annotate(
                _in_db_gc=Exists(Geocache.objects.filter(gc_code=OuterRef("code"))),
                _in_db_oc=Exists(Geocache.objects.filter(oc_code=OuterRef("code"))),
            ).filter(db_models.Q(_in_db_gc=True) | db_models.Q(_in_db_oc=True))
        else:
            qs = qs.annotate(
                _found_gc=Exists(Geocache.objects.filter(gc_code=OuterRef("code"), found=True)),
                _found_oc=Exists(Geocache.objects.filter(oc_code=OuterRef("code"), found=True)),
            ).filter(db_models.Q(_found_gc=True) | db_models.Q(_found_oc=True))
    return qs


def page(request):
    source_f = request.GET.get("source", "")
    platform_f = request.GET.get("oc_platform", "")
    status_f = request.GET.get("status", "")
    db_f = request.GET.get("db", "")
    q = request.GET.get("q", "").strip()
    sort = request.GET.get("sort", "added")
    direction = request.GET.get("dir", "desc")

    qs = _apply_filters(IgnoreListEntry.objects.all(), request.GET)

    if sort not in _SORT_FIELDS:
        sort = "added"
    if direction not in ("asc", "desc"):
        direction = "desc"
    order_expr = _SORT_FIELDS[sort]
    if direction == "desc":
        order_expr = f"-{order_expr}"
    qs = qs.order_by(order_expr)

    paginator = Paginator(qs, _PAGE_SIZE)
    page_obj = paginator.get_page(request.GET.get("page", 1))

    fp = request.GET.copy()
    fp.pop("page", None)

    from accounts.models import UserAccount
    oc_platforms = list(
        UserAccount.objects.filter(platform__startswith="oc_")
        .values_list("platform", flat=True)
        .distinct()
        .order_by("platform")
    )
    has_gc = UserAccount.objects.filter(platform="gc").exists()

    archived_count = IgnoreListEntry.objects.filter(status=CacheStatus.ARCHIVED).count()

    from geocaches.models import Geocache
    page_codes = [e.code for e in page_obj]
    db_lookup: dict = {}
    if page_codes:
        for g in Geocache.objects.filter(gc_code__in=page_codes).only("gc_code", "found", "found_date"):
            db_lookup[g.gc_code] = g
        for g in Geocache.objects.filter(oc_code__in=page_codes).only("oc_code", "found", "found_date"):
            if g.oc_code:
                db_lookup.setdefault(g.oc_code, g)
    page_entries = [(e, db_lookup.get(e.code)) for e in page_obj]

    return render(request, "geocaches/tools/ignore_lists.html", {
        "page_obj": page_obj,
        "page_entries": page_entries,
        "filter_params": fp.urlencode(),
        "source_f": source_f,
        "platform_f": platform_f,
        "status_f": status_f,
        "db_f": db_f,
        "q": q,
        "sort": sort,
        "dir": direction,
        "total": paginator.count,
        "oc_platforms": oc_platforms,
        "has_gc": has_gc,
        "status_choices": CacheStatus.choices,
        "archived_count": archived_count,
    })


def add(request):
    if request.method != "POST":
        return HttpResponseBadRequest("POST required")

    code = request.POST.get("code", "").strip().upper()
    targets = request.POST.getlist("targets")
    notes = request.POST.get("notes", "").strip()

    if not code:
        messages.error(request, _("Cache code is required."))
        return redirect("geocaches:tools_ignore_lists")

    if not targets:
        messages.error(request, _("Select at least one target list."))
        return redirect("geocaches:tools_ignore_lists")

    for target in targets:
        if target == "internal":
            add_internal(code, notes=notes)
        elif target == "gc":
            try:
                add_gc(code)
            except Exception as exc:
                messages.error(request, _("GC.com: %(error)s") % {"error": exc})
        elif target.startswith("oc:"):
            platform = target[3:]
            try:
                add_oc(code, platform)
            except Exception as exc:
                messages.error(request, _("%(platform)s: %(error)s") % {"platform": platform, "error": exc})

    added_to = []
    if "internal" in targets:
        added_to.append(_("internal ignore list"))
    if "gc" in targets:
        added_to.append(_("GC.com ignore list"))
    for t in targets:
        if t.startswith("oc:"):
            added_to.append(_("%(platform)s ignore list") % {"platform": t[3:]})
    if added_to:
        messages.success(request, _("%(code)s added to %(lists)s.") % {
            "code": code,
            "lists": _(" and ").join(added_to),
        })

    return redirect("geocaches:tools_ignore_lists")


def remove(request):
    if request.method != "POST":
        return HttpResponseBadRequest("POST required")

    pks = request.POST.getlist("pks")
    if not pks:
        pk = request.POST.get("pk", "")
        if pk:
            pks = [pk]

    if not pks:
        return redirect("geocaches:tools_ignore_lists")

    entries = list(IgnoreListEntry.objects.filter(pk__in=pks))
    internal_deleted = 0
    gc_removed = 0
    oc_removed = 0

    for entry in entries:
        if entry.source in (IgnoreSource.INTERNAL, IgnoreSource.GSAK):
            entry.delete()
            internal_deleted += 1
        elif entry.source == IgnoreSource.GC:
            try:
                remove_gc(entry.code)
                gc_removed += 1
            except Exception as exc:
                messages.error(request, _("GC.com remove %(code)s: %(error)s") % {"code": entry.code, "error": exc})
        elif entry.source == IgnoreSource.OC:
            try:
                remove_oc(entry.code, entry.oc_platform)
                oc_removed += 1
            except Exception as exc:
                messages.error(request, _("%(platform)s remove %(code)s: %(error)s") % {"platform": entry.oc_platform, "code": entry.code, "error": exc})

    if internal_deleted:
        messages.success(request, ngettext(
            "Removed %(n)d local entry from ignore list.",
            "Removed %(n)d local entries from ignore list.",
            internal_deleted,
        ) % {"n": internal_deleted})
    if gc_removed:
        messages.success(request, ngettext(
            "Removed %(n)d GC.com entry from GC.com ignore list.",
            "Removed %(n)d GC.com entries from GC.com ignore list.",
            gc_removed,
        ) % {"n": gc_removed})
    if oc_removed:
        messages.success(request, ngettext(
            "Removed %(n)d OC entry from OC ignore list.",
            "Removed %(n)d OC entries from OC ignore list.",
            oc_removed,
        ) % {"n": oc_removed})

    return redirect("geocaches:tools_ignore_lists")


def _local_cache_for(code: str):
    from geocaches.models import Geocache
    return (
        Geocache.objects.filter(gc_code=code).only("gc_code", "found", "found_date").first()
        or Geocache.objects.filter(oc_code=code).only("oc_code", "found", "found_date").first()
    )


def edit_notes(request, pk):
    entry = get_object_or_404(IgnoreListEntry, pk=pk, source__in=[IgnoreSource.INTERNAL, IgnoreSource.GSAK])
    local_cache = _local_cache_for(entry.code)

    if request.method == "POST":
        entry.notes = request.POST.get("notes", "").strip()
        entry.save(update_fields=["notes", "updated"])
        return render(request, "geocaches/partials/_ignore_row.html", {"entry": entry, "local_cache": local_cache})

    editing = request.GET.get("mode", "edit") == "edit"
    return render(request, "geocaches/partials/_ignore_row.html", {"entry": entry, "editing": editing, "local_cache": local_cache})


def import_gsak(request):
    from pathlib import Path

    GSAK_DATA_DIR = Path.home() / "AppData/Roaming/gsak/data"
    gsak_dbs = []
    if GSAK_DATA_DIR.exists():
        gsak_dbs = sorted(
            p for p in GSAK_DATA_DIR.iterdir()
            if p.is_dir() and (p / "sqlite.db3").exists()
        )

    results = []
    errors = []

    if request.method == "POST":
        from geocaches.importers.gsak import import_gsak_ignore_list
        paths = request.POST.getlist("gsak_paths")
        custom = request.POST.get("gsak_custom_path", "").strip()
        if custom:
            paths.append(custom)

        if not paths:
            errors.append(_("Select at least one database or enter a custom path."))
        else:
            for db_path_str in paths:
                db_path = Path(db_path_str)
                if not db_path.exists():
                    errors.append(_("File not found: %(path)s") % {"path": db_path_str})
                    continue
                try:
                    results.append(import_gsak_ignore_list(db_path))
                except Exception as exc:
                    errors.append(_("%(name)s: %(error)s") % {"name": db_path.name, "error": exc})

        if results and not errors:
            total_added = sum(r["added"] for r in results)
            total_skipped = sum(r["skipped_existing"] for r in results)
            db_names = ", ".join(r["db_name"] for r in results)
            messages.success(request, ngettext(
                "Imported %(n)d entry from %(dbs)s; %(skipped)d already existed.",
                "Imported %(n)d entries from %(dbs)s; %(skipped)d already existed.",
                total_added,
            ) % {"n": total_added, "dbs": db_names, "skipped": total_skipped})
            return redirect("geocaches:tools_ignore_lists")

    return render(request, "geocaches/tools/ignore_lists_import_gsak.html", {
        "gsak_dbs": gsak_dbs,
        "errors": errors,
    })


def import_gsak_preview(request):
    if request.method != "POST":
        return HttpResponseBadRequest("POST required")

    from pathlib import Path
    from geocaches.models import IgnoreListEntry, IgnoreSource

    paths = request.POST.getlist("gsak_paths")
    custom = request.POST.get("gsak_custom_path", "").strip()
    if custom:
        paths.append(custom)

    existing_codes = set(
        IgnoreListEntry.objects.filter(source=IgnoreSource.INTERNAL).values_list("code", flat=True)
    )

    dbs = []
    for db_path_str in paths:
        db_path = Path(db_path_str)
        db_label = db_path.parent.name
        if not db_path.exists():
            dbs.append({"name": db_label, "error": f"File not found: {db_path_str}", "rows": []})
            continue
        try:
            import sqlite3
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            try:
                rows = conn.execute("SELECT iCode, iName FROM Ignore").fetchall()
            finally:
                conn.close()
            entries = []
            for icode, iname in rows:
                code = (icode or "").strip().upper()
                if not code:
                    continue
                entries.append({"code": code, "name": iname or "", "exists": code in existing_codes})
            dbs.append({"name": db_label, "error": None, "rows": entries})
        except Exception as exc:
            dbs.append({"name": db_path.name, "error": str(exc), "rows": []})

    return render(request, "geocaches/partials/_ignore_gsak_preview.html", {"dbs": dbs})


def refresh(request):
    if request.method != "POST":
        return HttpResponseBadRequest("POST required")

    from geocaches.tasks.ignore_list import start_refresh

    source = request.POST.get("source", "").strip() or None
    oc_platform = request.POST.get("oc_platform", "").strip() or None
    status = request.POST.get("status", "").strip() or None
    q = request.POST.get("q", "").strip() or None

    if source:
        scope = {"source": source}
        if oc_platform:
            scope["oc_platform"] = oc_platform
    else:
        scope = {"all": True}

    if status:
        scope["status"] = status
    if q:
        scope["q"] = q

    started = start_refresh(scope)
    if started:
        messages.info(request, _("Ignore list refresh started — check the task dock for progress."))
    else:
        messages.warning(request, _("A refresh is already running."))

    return redirect("geocaches:tools_ignore_lists")


def sync_oc(request):
    if request.method != "POST":
        return HttpResponseBadRequest("POST required")

    platform = request.POST.get("oc_platform", "").strip()
    if not platform:
        messages.error(request, _("No OC platform specified."))
        return redirect("geocaches:tools_ignore_lists")

    try:
        count = sync_oc_ignore_list(platform)
        messages.success(request, ngettext(
            "Synced %(platform)s ignore list: %(n)d entry fetched.",
            "Synced %(platform)s ignore list: %(n)d entries fetched.",
            count,
        ) % {"platform": platform, "n": count})
    except Exception as exc:
        messages.error(request, _("%(platform)s sync failed: %(error)s") % {"platform": platform, "error": exc})

    return redirect("geocaches:tools_ignore_lists")


def sync_gc(request):
    if request.method != "POST":
        return HttpResponseBadRequest("POST required")

    try:
        count = sync_gc_ignore_list()
        messages.success(request, ngettext(
            "Synced GC.com ignore list: %(n)d entry fetched.",
            "Synced GC.com ignore list: %(n)d entries fetched.",
            count,
        ) % {"n": count})
    except Exception as exc:
        messages.error(request, _("GC.com sync failed: %(error)s") % {"error": exc})

    return redirect("geocaches:tools_ignore_lists")


def remove_archived(request):
    if request.method != "POST":
        return HttpResponseBadRequest("POST required")

    source = request.POST.get("source") or None
    oc_platform = request.POST.get("oc_platform") or None
    count = svc_remove_archived(source=source, oc_platform=oc_platform)

    if count:
        messages.success(request, ngettext(
            "Removed %(n)d archived entry from ignore list.",
            "Removed %(n)d archived entries from ignore list.",
            count,
        ) % {"n": count})
    else:
        messages.info(request, _("No archived entries found to remove."))

    return redirect("geocaches:tools_ignore_lists")


def cache_ignore(request, gc_code):
    if request.method != "POST":
        return HttpResponseBadRequest("POST required")
    code = gc_code.upper()
    target = request.POST.get("target", "local")

    if target in ("local", "both"):
        add_internal(code)

    if target in ("remote", "both"):
        if code[:2] == "GC":
            try:
                add_gc(code)
            except Exception as exc:
                messages.error(request, _("GC.com: %(error)s") % {"error": exc})
        else:
            from geocaches.oc_platforms import platform_for_code
            platform = platform_for_code(code)
            if platform:
                try:
                    add_oc(code, platform)
                except Exception as exc:
                    messages.error(request, _("%(platform)s: %(error)s") % {"platform": platform, "error": exc})

    messages.success(request, _("%(code)s added to ignore list.") % {"code": code})
    return redirect("geocaches:detail", gc_code=gc_code)


def cache_unignore(request, gc_code):
    if request.method != "POST":
        return HttpResponseBadRequest("POST required")
    from geocaches.services.ignore_list import _LOCAL_SOURCES, remove_internal
    code = gc_code.upper()
    target = request.POST.get("target", "local")
    removed_any = False

    if target in ("local", "all"):
        if remove_internal(code):
            removed_any = True

    if target == "all":
        for entry in IgnoreListEntry.objects.filter(code=code).exclude(source__in=_LOCAL_SOURCES):
            if entry.source == IgnoreSource.GC:
                try:
                    remove_gc(entry.code)
                    removed_any = True
                except Exception as exc:
                    messages.error(request, _("GC.com: %(error)s") % {"error": exc})
            elif entry.source == IgnoreSource.OC:
                try:
                    remove_oc(entry.code, entry.oc_platform)
                    removed_any = True
                except Exception as exc:
                    messages.error(request, _("%(platform)s: %(error)s") % {"platform": entry.oc_platform, "error": exc})
    elif target == "gc":
        try:
            remove_gc(code)
            removed_any = True
        except Exception as exc:
            messages.error(request, _("GC.com: %(error)s") % {"error": exc})
    elif target == "oc":
        entry = IgnoreListEntry.objects.filter(source=IgnoreSource.OC, code=code).first()
        if entry:
            try:
                remove_oc(entry.code, entry.oc_platform)
                removed_any = True
            except Exception as exc:
                messages.error(request, _("%(platform)s: %(error)s") % {"platform": entry.oc_platform, "error": exc})

    if removed_any:
        messages.success(request, _("%(code)s removed from ignore list.") % {"code": code})
    return redirect("geocaches:detail", gc_code=gc_code)


def remove_filtered(request):
    if request.method != "POST":
        return HttpResponseBadRequest("POST required")
    qs = _apply_filters(IgnoreListEntry.objects.all(), request.POST)
    count, __ = qs.delete()
    if count:
        messages.success(request, ngettext(
            "Removed %(n)d entry from ignore list.",
            "Removed %(n)d entries from ignore list.",
            count,
        ) % {"n": count})
    else:
        messages.info(request, _("No entries matched the current filter."))
    return redirect("geocaches:tools_ignore_lists")


def bulk_transfer(request):
    """Move or copy entries in the filtered subset to a target ignore list.

    POST params: op (move|copy), target (internal|gc|oc:<platform>), plus filter params.
    GSAK is not a valid target (import-only). Entries whose code is incompatible with
    the target list (e.g. an OC code targeted at GC.com) are skipped.
    """
    from geocaches.oc_platforms import platform_for_code
    if request.method != "POST":
        return HttpResponseBadRequest("POST required")
    # Accept either separate op/target params, or a combined "action" param ("move:gc", "copy:oc:oc_de", etc.)
    action = request.POST.get("action", "")
    if action:
        op, __, target = action.partition(":")
    else:
        op = request.POST.get("op", "")
        target = request.POST.get("target", "")
    if op not in ("move", "copy"):
        return HttpResponseBadRequest("invalid op")

    if target == "internal":
        target_source = IgnoreSource.INTERNAL
        target_platform = ""
        target_label = _("Internal")
    elif target == "gc":
        target_source = IgnoreSource.GC
        target_platform = ""
        target_label = "GC.com"
    elif target.startswith("oc:"):
        target_source = IgnoreSource.OC
        target_platform = target[3:]
        target_label = target_platform
    else:
        return HttpResponseBadRequest("invalid target")

    qs = _apply_filters(IgnoreListEntry.objects.all(), request.POST)

    processed = 0
    skipped_incompatible = 0
    skipped_already_target = 0
    errors: list[str] = []

    for entry in qs:
        code = entry.code
        if target_source == IgnoreSource.GC and not code.upper().startswith("GC"):
            skipped_incompatible += 1
            continue
        if target_source == IgnoreSource.OC and platform_for_code(code) != target_platform:
            skipped_incompatible += 1
            continue
        # Already on target list?
        already_on_target = (
            entry.source == target_source and entry.oc_platform == target_platform
        )
        if already_on_target:
            skipped_already_target += 1
            if op == "move":
                # nothing to do — entry is already there
                pass
            continue
        try:
            if target_source == IgnoreSource.INTERNAL:
                add_internal(code)
            elif target_source == IgnoreSource.GC:
                add_gc(code)
            elif target_source == IgnoreSource.OC:
                add_oc(code, target_platform)
        except Exception as exc:
            errors.append(f"{code}: {exc}")
            continue
        if op == "move":
            entry.delete()
        processed += 1

    if processed:
        if op == "move":
            msg = ngettext(
                "Moved %(n)d entry to %(target)s.",
                "Moved %(n)d entries to %(target)s.",
                processed,
            )
        else:
            msg = ngettext(
                "Copied %(n)d entry to %(target)s.",
                "Copied %(n)d entries to %(target)s.",
                processed,
            )
        messages.success(request, msg % {"n": processed, "target": target_label})
    if skipped_incompatible:
        messages.info(request, ngettext(
            "Skipped %(n)d entry (code incompatible with %(target)s).",
            "Skipped %(n)d entries (code incompatible with %(target)s).",
            skipped_incompatible,
        ) % {"n": skipped_incompatible, "target": target_label})
    if skipped_already_target:
        messages.info(request, ngettext(
            "Skipped %(n)d entry (already on %(target)s).",
            "Skipped %(n)d entries (already on %(target)s).",
            skipped_already_target,
        ) % {"n": skipped_already_target, "target": target_label})
    if errors:
        for err in errors[:5]:
            messages.error(request, err)
        if len(errors) > 5:
            messages.error(request, _("… and %(n)d more errors.") % {"n": len(errors) - 5})
    if not (processed or skipped_incompatible or skipped_already_target or errors):
        messages.info(request, _("No entries matched the current filter."))
    return redirect("geocaches:tools_ignore_lists")
