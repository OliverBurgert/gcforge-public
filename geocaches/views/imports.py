import logging
from datetime import datetime, timezone

from django.http import HttpResponse, HttpResponseBadRequest
from django.shortcuts import render
from django.utils.translation import gettext as _, ngettext

_import_logger = logging.getLogger("geocaches.import")
_fieldnote_logger = logging.getLogger("geocaches.fieldnote")


def _import_tag_names(request):
    raw = request.POST.get("tags", "")
    return [t.strip() for t in raw.split(",") if t.strip()] or None


def _derive_wpts_path(gpx_path):
    """Derive the companion -wpts.gpx path from a main .gpx path, or None."""
    from pathlib import Path
    p = Path(gpx_path)
    if p.suffix.lower() != ".gpx":
        return None
    candidate = p.with_name(p.stem + "-wpts.gpx")
    return str(candidate) if candidate.exists() else None


def _is_wpts_file(filename):
    """Return True if the filename looks like a companion -wpts.gpx file."""
    return filename.lower().endswith("-wpts.gpx")


def _extract_alc_smart_name(url: str) -> str:
    """Extract the smart link name from a labs.geocaching.com/goto/<name> URL.

    Also accepts a bare smart name with no URL prefix.
    Raises ValueError if the URL cannot be parsed.
    """
    import re as _re
    m = _re.search(r"labs\.geocaching\.com/goto/([^/?#\s]+)", url, _re.IGNORECASE)
    if m:
        return m.group(1)
    stripped = url.strip().strip("/")
    if stripped and " " not in stripped:
        return stripped
    raise ValueError(f"Could not extract smart link name from: {url!r}")


def _save_recent_import(pref_key, path_str, result):
    """Append a successful import to the recent-files list (max 10)."""
    from preferences.models import UserPreference
    recent = UserPreference.get(pref_key, [])
    summary = f"{result.created}+ {result.updated}~"
    entry = {
        "path": path_str,
        "date": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M"),
        "summary": summary,
    }
    # Remove duplicates of the same path
    recent = [r for r in recent if r.get("path") != path_str]
    recent.insert(0, entry)
    recent = recent[:10]
    UserPreference.set(pref_key, recent)


def _get_recent_imports(pref_key):
    from preferences.models import UserPreference
    return UserPreference.get(pref_key, [])


def _save_recent_fieldnote_folder(path_str: str):
    """Save the parent folder of path_str to recent_fieldnote_folders (max 5).

    Skips the internal fieldnotes/ dir — those aren't useful as shortcuts.
    """
    from pathlib import Path as _Path
    from preferences.models import UserPreference
    try:
        folder = str(_Path(path_str).resolve().parent)
        from geocaches.importers.fieldnote import _fieldnotes_dir
        if _Path(folder).resolve() == _fieldnotes_dir().resolve():
            return
        recent = UserPreference.get("recent_fieldnote_folders", [])
        recent = [f for f in recent if f != folder]
        recent.insert(0, folder)
        UserPreference.set("recent_fieldnote_folders", recent[:5])
    except Exception:
        pass


def _resolve_gpx_paths(path_str: str) -> list:
    """Resolve a path string into a list of GPX file paths.

    Handles: single file, semicolon-separated list, or folder path.
    Excludes -wpts.gpx companion files when expanding folders.
    """
    from pathlib import Path
    paths = []
    for part in path_str.split(";"):
        part = part.strip()
        if not part:
            continue
        p = Path(part)
        if p.is_dir():
            for f in sorted(p.glob("*.gpx")):
                if not _is_wpts_file(f.name):
                    paths.append(str(f))
            for f in sorted(p.glob("*.zip")):
                paths.append(str(f))
        elif p.exists():
            paths.append(str(p))
        else:
            paths.append(str(p))  # let import_and_enrich report the error
    return paths


def _delete_imported_file(file_path: str, include_wpts: bool = True):
    """Delete a GPX file and its companion -wpts.gpx after import."""
    from pathlib import Path
    p = Path(file_path)
    try:
        if p.exists():
            p.unlink()
            _import_logger.info("Deleted imported file: %s", file_path)
        if include_wpts:
            wpts = _derive_wpts_path(file_path)
            if wpts:
                wp = Path(wpts)
                if wp.exists():
                    wp.unlink()
                    _import_logger.info("Deleted companion wpts file: %s", wpts)
    except OSError as exc:
        _import_logger.warning("Failed to delete %s: %s", file_path, exc)


def _merge_import_results(results):
    """Merge multiple ImportResult objects into a summary."""
    if len(results) == 1:
        return results[0]

    # Create a simple summary object that looks like ImportResult
    class MergedResult:
        def __init__(self):
            self.created = 0
            self.updated = 0
            self.locked = 0
            self.skipped = 0
            self.errors = []
            self.file_count = 0

    merged = MergedResult()
    for r in results:
        merged.created += getattr(r, "created", 0)
        merged.updated += getattr(r, "updated", 0)
        merged.locked += getattr(r, "locked", 0)
        merged.skipped += getattr(r, "skipped", 0)
        merged.errors.extend(getattr(r, "errors", []))
        merged.file_count += 1
    return merged


def _check_duplicates_after_import(request):
    """Quick scan for potential GC/OC duplicates; adds a Django message if found."""
    from django.contrib import messages
    from geocaches.services import find_potential_duplicates
    try:
        dupes = find_potential_duplicates()
        if dupes:
            from django.utils.safestring import mark_safe
            url = '/tools/duplicate-caches/'
            messages.info(request, mark_safe(ngettext(
                '%(n)d potential duplicate GC/OC cache detected. <a href="%(url)s">Review and merge in Tools</a>.',
                '%(n)d potential duplicate GC/OC caches detected. <a href="%(url)s">Review and merge in Tools</a>.',
                len(dupes),
            ) % {"n": len(dupes), "url": url}))
    except Exception as exc:
        _import_logger.debug("Post-import dedup scan failed: %s", exc)


def import_gpx(request):
    from geocaches.services import import_and_enrich
    from preferences.models import UserPreference

    results = []
    errors = []
    delete_after_import = UserPreference.get("delete_after_import", False)

    if request.method == "POST":
        tag_names = _import_tag_names(request)
        path_str = request.POST.get("gpx_path", "").strip()
        include_wpts = request.POST.get("include_wpts", "include") == "include"
        delete_after = "delete_after_import" in request.POST

        # Persist the delete preference
        if delete_after != delete_after_import:
            UserPreference.set("delete_after_import", delete_after)
            delete_after_import = delete_after

        if not path_str:
            errors.append("Please enter a file path.")
        else:
            file_paths = _resolve_gpx_paths(path_str)
            if not file_paths:
                errors.append("No GPX/ZIP files found.")

            from datetime import datetime, timezone
            batch_since = datetime.now(timezone.utc)
            any_imported = False

            for fp in file_paths:
                if _is_wpts_file(fp):
                    continue
                try:
                    _import_logger.info("--- GPX import start: %s", fp)
                    wpts_path = _derive_wpts_path(fp) if include_wpts else ""
                    # auto_enrich=False so we fire one enrichment pass for the
                    # whole batch instead of N (one per file) overlapping each
                    # other and the next import on the same worker pool.
                    result = import_and_enrich(
                        "unified_gpx", fp, tag_names, wpts_path=wpts_path,
                        auto_enrich=False,
                    )
                    if result:
                        any_imported = True
                        _import_logger.info("--- GPX import done: %s", result)
                        for e in result.errors:
                            _import_logger.warning("GPX import error: %s", e)
                        _save_recent_import("recent_imports_gpx", fp, result)
                        results.append(result)

                        # Delete source file after successful import
                        if delete_after:
                            _delete_imported_file(fp, include_wpts)
                except Exception as exc:
                    errors.append(f"{fp}: {exc}")
                    _import_logger.error("GPX import failed: %s: %s", fp, exc)

            if any_imported:
                from geocaches.services import _start_auto_enrich
                _start_auto_enrich(batch_since)

    # Merge results for template display
    merged_result = _merge_import_results(results) if results else None

    if results:
        _check_duplicates_after_import(request)

    recent_files = _get_recent_imports("recent_imports_gpx")
    return render(request, "geocaches/import/import_gpx.html", {
        "result": merged_result,
        "errors": errors,
        "recent_files": recent_files,
        "delete_after_import": delete_after_import,
    })


def import_alc_from_url(request):
    """Import / refresh a single Adventure Lab cache from its public labs.geocaching.com URL."""
    from django.shortcuts import redirect
    from geocaches.models import Tag

    all_tags = Tag.objects.order_by("name")

    if request.method != "POST":
        return render(request, "geocaches/import/import_alc_from_url.html", {"all_tags": all_tags})

    raw_url = request.POST.get("alc_url", "").strip()
    tag_names = _import_tag_names(request)
    tags = [Tag.objects.get_or_create(name=n)[0] for n in (tag_names or [])]

    def _err(msg):
        return render(request, "geocaches/import/import_alc_from_url.html", {
            "all_tags": all_tags, "alc_url": raw_url,
            "tags_value": request.POST.get("tags", ""), "error": msg,
        })

    try:
        smart_name = _extract_alc_smart_name(raw_url)
    except ValueError as exc:
        return _err(str(exc))

    try:
        from gcprivate.al_client import ALClient
        from geocaches.services.save_alc import save_adventure_from_api
        client = ALClient()
        adventure_guid = client.resolve_smart_link(smart_name)
        data = client.get_adventure(adventure_guid)
        adv, _stats = save_adventure_from_api(data, tags=tags)
    except Exception as exc:
        return _err(f"Import failed: {exc}")

    return redirect("geocaches:detail", gc_code=adv.code)


def import_by_code(request):
    """Import a single cache by GC or OC code via the respective API."""
    from django.shortcuts import redirect
    from geocaches.models import Tag

    all_tags = Tag.objects.order_by("name")

    if request.method != "POST":
        return render(request, "geocaches/import/import_by_code.html", {"all_tags": all_tags})

    raw_code = request.POST.get("code", "").strip()
    code = raw_code.upper()
    tag_names = _import_tag_names(request)
    tags = [Tag.objects.get_or_create(name=n)[0] for n in (tag_names or [])]

    if not code:
        return render(request, "geocaches/import/import_by_code.html", {
            "all_tags": all_tags, "error": "Please enter a cache code.",
        })

    from geocaches.services.ignore_list import is_internally_ignored, remove_internal
    confirm_ignore = request.POST.get("confirm_ignore", "")
    if is_internally_ignored(code) and confirm_ignore not in ("remove", "keep"):
        return render(request, "geocaches/import/import_by_code.html", {
            "all_tags": all_tags,
            "code": raw_code,
            "tags_value": request.POST.get("tags", ""),
            "ignored_warning": True,
        })
    if confirm_ignore == "remove":
        remove_internal(code)

    prefix = code[:2]

    def _err(msg):
        return render(request, "geocaches/import/import_by_code.html", {
            "all_tags": all_tags, "code": raw_code, "tags_value": request.POST.get("tags", ""),
            "error": msg,
        })

    if prefix == "GC":
        try:
            from gcprivate.gc_client import GCClient
            from geocaches.sync.base import SyncMode
            from geocaches.services import save_geocache
            from geocaches.sync.log_fetch import ensure_my_gc_logs
            client = GCClient()
            data = client.get_cache(code, SyncMode.FULL, log_count=5)
            kwargs = dict(data)
            kwargs["fields"] = dict(data["fields"])
            kwargs["tags"] = tags
            save_geocache(**kwargs)
            ensure_my_gc_logs(client, code)
        except Exception as exc:
            return _err(f"GC import failed: {exc}")

    elif prefix in ("OC", "OP", "OK", "OB", "ON", "OR", "OU"):
        try:
            from geocaches.sync.oc_client import OCClient
            from geocaches.sync.base import SyncMode
            from geocaches.services import save_geocache
            from geocaches.oc_platforms import platform_for_code
            from accounts.models import UserAccount
            platform = platform_for_code(code)
            acct = UserAccount.objects.filter(platform=platform).first()
            client = OCClient(platform=platform, user_id=acct.user_id if acct else "")
            data = client.get_cache(code, SyncMode.FULL)
            kwargs = dict(data)
            kwargs["fields"] = dict(data["fields"])
            kwargs["tags"] = tags
            save_geocache(**kwargs)
        except Exception as exc:
            return _err(f"OC import failed: {exc}")

    else:
        return _err(f"Unknown code prefix '{prefix}'. Supported: GC, OC, OP, OK, OB, ON, OR, OU.")

    return redirect("geocaches:detail", gc_code=code)


def detect_gpx_format_ajax(request):
    """AJAX endpoint to detect GPX file format from a path."""
    from geocaches.importers import detect_gpx_format
    import json
    path_str = request.GET.get("path", "").strip()
    if not path_str:
        return HttpResponseBadRequest(json.dumps({"error": "No path"}), content_type="application/json")
    fmt = detect_gpx_format(path_str)
    return HttpResponse(json.dumps({"format": fmt}), content_type="application/json")


def import_gsak(request):
    from pathlib import Path
    from geocaches.services import import_and_enrich

    GSAK_DATA_DIR = Path.home() / "AppData/Roaming/gsak/data"
    gsak_dbs = []
    if GSAK_DATA_DIR.exists():
        gsak_dbs = sorted(
            p for p in GSAK_DATA_DIR.iterdir()
            if p.is_dir() and (p / "sqlite.db3").exists()
        )

    result = None
    errors = []
    db_path = ""

    if request.method == "POST":
        tag_names = _import_tag_names(request)
        db_path = request.POST.get("gsak_path", "").strip()
        if not db_path:
            db_path = request.POST.get("gsak_custom_path", "").strip()
        try:
            if db_path:
                _import_logger.info("--- GSAK import start: %s", db_path)
                result = import_and_enrich("gsak", db_path, tag_names)
            else:
                errors.append("Please select or enter a database path.")
        except Exception as exc:
            errors.append(str(exc))
        if result:
            _import_logger.info("--- GSAK import done: %s", result)
            for e in result.errors:
                _import_logger.warning("GSAK import error: %s", e)
        for e in errors:
            _import_logger.error("GSAK import failed: %s", e)

    if result:
        _check_duplicates_after_import(request)

    db_name = Path(db_path).parent.name if db_path else None
    return render(request, "geocaches/import/import_gsak.html", {
        "gsak_dbs": gsak_dbs,
        "result": result,
        "errors": errors,
        "db_name": db_name,
    })


def import_lab2gpx(request):
    from geocaches.services import import_and_enrich

    result = None
    errors = []

    if request.method == "POST":
        tag_names = _import_tag_names(request)
        path_str = request.POST.get("lab2gpx_path", "").strip()
        try:
            if not path_str:
                errors.append("Please enter a file path.")
            else:
                _import_logger.info("--- lab2gpx import start: %s", path_str)
                result = import_and_enrich("lab2gpx", path_str, tag_names)
        except Exception as exc:
            errors.append(str(exc))
        if result:
            _import_logger.info("--- lab2gpx import done: %s", result)
            for e in result.errors:
                _import_logger.warning("lab2gpx import error: %s", e)
            if path_str:
                _save_recent_import("recent_imports_lab2gpx", path_str, result)
        for e in errors:
            _import_logger.error("lab2gpx import failed: %s", e)

    if result:
        _check_duplicates_after_import(request)

    recent_files = _get_recent_imports("recent_imports_lab2gpx")
    return render(request, "geocaches/import/import_lab2gpx.html", {
        "result": result, "errors": errors, "recent_files": recent_files,
    })


def import_fieldnotes(request):
    """Import field notes from a file or download from GC.com."""
    import json as _json
    from django.contrib import messages
    from geocaches.importers.fieldnote import (
        import_fieldnote_file, analyze_fieldnote_file,
        download_gc_fieldnotes, _fieldnotes_dir,
    )
    from pathlib import Path

    result = None
    errors = []
    pending_file_path = ""   # file waiting for user decision (not yet imported)
    action = request.POST.get("action", "import") if request.method == "POST" else ""

    if request.method == "POST":
        if action == "download_gc":
            try:
                saved_path = download_gc_fieldnotes()
                messages.success(request, _("Downloaded GC field notes → %(name)s") % {"name": saved_path.name})
                result = analyze_fieldnote_file(saved_path)
                if result.not_found_entries:
                    pending_file_path = str(saved_path)
                else:
                    result = import_fieldnote_file(saved_path)
            except Exception as exc:
                errors.append(f"GC download failed: {exc}")
                _fieldnote_logger.error("GC field note download failed: %s", exc)

        elif action in ("import", "reimport"):
            path_str = request.POST.get("fieldnote_path", "").strip()
            if not path_str:
                errors.append("Please enter a file path.")
            else:
                fp = Path(path_str)
                if not fp.is_file():
                    errors.append(f"File not found: {path_str}")
                else:
                    # If the file is not already in the fieldnotes folder, copy it there first
                    fn_dir = _fieldnotes_dir()
                    fn_dir.mkdir(parents=True, exist_ok=True)
                    if fp.parent.resolve() != fn_dir.resolve():
                        _save_recent_fieldnote_folder(path_str)
                        import shutil as _shutil
                        dest = fn_dir / fp.name
                        if dest.exists():
                            from datetime import datetime as _dt
                            dest = fn_dir / f"{fp.stem}_{_dt.now().strftime('%Y%m%d_%H%M%S')}{fp.suffix}"
                        _shutil.copy2(str(fp), str(dest))
                        fp = dest
                    result = analyze_fieldnote_file(fp)
                    if result.not_found_entries:
                        pending_file_path = str(fp)
                    else:
                        result = import_fieldnote_file(fp)

        elif action == "import_skip_missing":
            path_str = request.POST.get("fieldnote_path", "").strip()
            fp = Path(path_str)
            if fp.is_file():
                result = import_fieldnote_file(fp, mode="skip_missing")
            else:
                errors.append(f"File not found: {path_str}")

        elif action == "import_with_placeholders":
            path_str = request.POST.get("fieldnote_path", "").strip()
            fp = Path(path_str)
            if fp.is_file():
                result = import_fieldnote_file(fp, mode="import_all")
            else:
                errors.append(f"File not found: {path_str}")

    # List unprocessed files for the "recent" panel
    fn_dir = _fieldnotes_dir()
    pending_files = sorted(fn_dir.glob("*.txt"), reverse=True)[:10]

    # Build per-platform code grouping for the "Fetch Caches" button
    not_found_by_platform: dict = {}
    if result and result.not_found_entries:
        for entry in result.not_found_entries:
            plat = entry.platform
            not_found_by_platform.setdefault(plat, []).append(entry.cache_code)

    redirect_to_bulk = (
        result is not None
        and not errors
        and not result.not_found_entries
        and not pending_file_path
    )

    from pathlib import Path as _Path
    from preferences.models import UserPreference as _UP
    raw_folders = _UP.get("recent_fieldnote_folders", [])
    recent_folders = [(_Path(f).name, f) for f in raw_folders]

    return render(request, "geocaches/import/import_fieldnotes.html", {
        "result": result,
        "errors": errors,
        "pending_files": [str(p) for p in pending_files],
        "redirect_to_bulk": redirect_to_bulk,
        "pending_file_path": pending_file_path,
        "not_found_by_platform_json": _json.dumps(not_found_by_platform),
        "recent_folders": recent_folders,
    })


def import_gsak_locations(request):
    """Import reference points from GSAK Options > Locations and per-DB centre points."""
    from pathlib import Path
    from django.http import HttpResponseRedirect
    from django.urls import reverse
    from geocaches.services import import_gsak_location_candidates, parse_and_import_gsak_locations

    GSAK_DIR = Path.home() / "AppData/Roaming/gsak"
    unique_candidates, errors, existing = parse_and_import_gsak_locations(GSAK_DIR)

    if request.method == "POST":
        selected_indices = request.POST.getlist("loc_idx")
        selected = []
        for idx_str in selected_indices:
            try:
                selected.append(unique_candidates[int(idx_str)])
            except (ValueError, IndexError):
                continue
        imported = import_gsak_location_candidates(selected)
        request.session["gsak_locations_imported"] = imported
        return HttpResponseRedirect(reverse("geocaches:import_gsak_locations"))

    imported = request.session.pop("gsak_locations_imported", [])

    return render(request, "geocaches/import/import_gsak_locations.html", {
        "candidates": unique_candidates,
        "errors": errors,
        "imported": imported,
        "existing": existing,
    })


def import_al_founds(request):
    """Import AL adventures selected after a preview fetch."""
    from django.http import HttpResponseNotAllowed
    from geocaches.models import Tag

    all_tags = Tag.objects.order_by("name")

    if request.method == "GET":
        return render(request, "geocaches/import/import_al_founds.html", {
            "all_tags": all_tags,
        })

    if request.method != "POST":
        return HttpResponseNotAllowed(["GET", "POST"])

    selected_guids = request.POST.getlist("guids")
    if not selected_guids:
        return render(request, "geocaches/partials/_al_founds_status.html", {
            "error": "No Adventures selected."
        })

    tag_names = _import_tag_names(request) or []

    from geocaches.tasks import submit_task
    from geocaches.sync.service import sync_al_by_guids

    def _run(guids, tag_list, task_info=None):
        return sync_al_by_guids(
            guids, tags=tag_list,
            task_info=task_info,
            cancel_event=task_info.cancel_event if task_info else None,
        )

    task_id = submit_task(
        f"Import {len(selected_guids)} AL find(s)",
        _run,
        list(selected_guids),
        tag_names,
    )
    return render(request, "geocaches/partials/_al_founds_status.html", {
        "task_id": task_id,
        "count": len(selected_guids),
    })


def import_al_founds_preview(request):
    """HTMX endpoint: call the AL API and classify results against GCForge DB state."""
    from django.http import HttpResponseNotAllowed
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    from gcprivate.al_client import ALClient
    from geocaches.models import Adventure, Geocache

    try:
        client = ALClient()
        items = client.fetch_player_adventures_preview(["Completed", "InProgress"])
    except Exception as exc:
        return render(request, "geocaches/partials/_al_founds_preview.html", {
            "error": str(exc),
        })

    if not items:
        return render(request, "geocaches/partials/_al_founds_preview.html", {
            "empty": True,
        })

    all_guids = [item["adventure_guid"] for item in items]

    # Fetch which GUIDs exist in GCForge and their LC codes
    guid_to_code: dict[str, str] = {
        g.lower(): code
        for g, code in Adventure.objects.filter(adventure_guid__in=all_guids)
        .values_list("adventure_guid", "code")
    }
    existing_guids = set(guid_to_code.keys())

    # Attach lc_code to each item for template links
    for item in items:
        item["lc_code"] = guid_to_code.get(item["adventure_guid"], "")

    # Parent geocache `completed` flag: guid.lower() → bool
    parent_completed: dict[str, bool] = {}
    for g, c in (
        Geocache.objects.filter(
            adventure__adventure_guid__in=all_guids,
            al_detail__isnull=True,
        ).values_list("adventure__adventure_guid", "completed")
    ):
        parent_completed[g.lower()] = bool(c)

    # Adventures that have at least one found stage
    has_found_stage: set[str] = {
        g.lower()
        for g in Geocache.objects.filter(
            adventure__adventure_guid__in=all_guids,
            al_detail__isnull=False,
            found=True,
        ).values_list("adventure__adventure_guid", flat=True)
    }

    classified: dict[str, list] = {
        "completed_confirmed": [],
        "completed_needs_update": [],
        "inprogress_confirmed": [],
        "inprogress_needs_update": [],
    }

    for item in items:
        guid = item["adventure_guid"]
        api_status = item.get("completion_status", "")

        if api_status == "Completed":
            if guid in existing_guids and parent_completed.get(guid, False):
                classified["completed_confirmed"].append(item)
            else:
                classified["completed_needs_update"].append(item)
        elif api_status == "InProgress":
            if guid in existing_guids and guid in has_found_stage:
                classified["inprogress_confirmed"].append(item)
            else:
                classified["inprogress_needs_update"].append(item)

    total_completed = len(classified["completed_confirmed"]) + len(classified["completed_needs_update"])
    total_inprogress = len(classified["inprogress_confirmed"]) + len(classified["inprogress_needs_update"])

    return render(request, "geocaches/partials/_al_founds_preview.html", {
        "classified": classified,
        "total_completed": total_completed,
        "total_inprogress": total_inprogress,
        "total": len(items),
    })


def import_al_founds_status(request, task_id):
    """HTMX polling endpoint for AL import task status."""
    from geocaches.tasks import get_task
    info = get_task(task_id)
    return render(request, "geocaches/partials/_al_founds_status.html", {
        "task_id": task_id,
        "task": info,
    })


def sync_al_stage_dates(request):
    """Fetch per-stage completion dates from labs.geocaching.com and populate found_date.

    GET  → render the page (shows how many stages are pending a date).
    POST → start background task, return HTMX status partial.
    """
    from geocaches.models import Geocache

    pending_count = Geocache.objects.filter(
        al_detail__isnull=False,
        found=True,
        found_date__isnull=True,
    ).count()

    if request.method == "GET":
        from accounts.models import UserAccount
        gc_account = UserAccount.objects.filter(platform="gc").first()
        has_account = bool(gc_account and gc_account.user_id)
        return render(request, "geocaches/sync_al_stage_dates.html", {
            "pending_count": pending_count,
            "has_account": has_account,
        })

    if request.method != "POST":
        from django.http import HttpResponseNotAllowed
        return HttpResponseNotAllowed(["GET", "POST"])

    from accounts.models import UserAccount
    gc_account = UserAccount.objects.filter(platform="gc").first()
    if not gc_account or not gc_account.user_id:
        return render(request, "geocaches/partials/_sync_al_stage_dates_status.html", {
            "error": "No GC account with user GUID configured. "
                     "Go to Settings > Accounts and connect your GC account."
        })

    if not pending_count:
        return render(request, "geocaches/partials/_sync_al_stage_dates_status.html", {
            "done": True,
            "updated": 0,
        })

    account_guid = gc_account.user_id

    from geocaches.tasks import submit_task
    from gcprivate.labs_client import LabsClient

    def _run(guid, task_info=None):
        if task_info:
            task_info.total = pending_count
        client = LabsClient()
        updated = client.sync_stage_dates(guid)
        if task_info:
            task_info.completed = updated
        return updated

    task_id = submit_task("Sync ALC stage dates", _run, account_guid)
    return render(request, "geocaches/partials/_sync_al_stage_dates_status.html", {
        "task_id": task_id,
    })


def sync_al_stage_dates_status(request, task_id):
    """HTMX polling endpoint for ALC stage date sync task."""
    from geocaches.tasks import get_task
    info = get_task(task_id)
    return render(request, "geocaches/partials/_sync_al_stage_dates_status.html", {
        "task_id": task_id,
        "task": info,
    })


def import_al_recover_founds(request):
    """Recover AL stages found by the player that are missing from the DB,
    including retired adventures absent from the search API.

    GET  → render the page.
    POST → start the background recovery task, return the HTMX status partial.
    """
    from accounts.models import UserAccount
    gc_account = UserAccount.objects.filter(platform="gc").first()
    has_account = bool(gc_account and gc_account.user_id)

    if request.method == "GET":
        return render(request, "geocaches/import_al_recover_founds.html", {
            "has_account": has_account,
        })

    if request.method != "POST":
        from django.http import HttpResponseNotAllowed
        return HttpResponseNotAllowed(["GET", "POST"])

    if not has_account:
        return render(request, "geocaches/partials/_al_recover_founds_status.html", {
            "error": "No GC account with a user GUID configured. "
                     "Go to Settings > Accounts and connect your GC account.",
        })

    account_guid = gc_account.user_id
    tag_names = [t.strip() for t in request.POST.get("tags", "").split(",") if t.strip()]

    from geocaches.tasks import submit_task
    from geocaches.sync.service import import_al_founds_from_logs

    def _run(guid, tag_list, task_info=None):
        return import_al_founds_from_logs(
            guid, tags=tag_list,
            task_info=task_info,
            cancel_event=task_info.cancel_event if task_info else None,
        )

    task_id = submit_task("Recover AL founds from logs", _run, account_guid, tag_names)
    return render(request, "geocaches/partials/_al_recover_founds_status.html", {
        "task_id": task_id,
    })


def import_al_recover_founds_status(request, task_id):
    """HTMX polling endpoint for the AL found-recovery task."""
    from geocaches.tasks import get_task
    info = get_task(task_id)
    return render(request, "geocaches/partials/_al_recover_founds_status.html", {
        "task_id": task_id,
        "task": info,
    })


def tools_remove_zero_waypoints(request):
    """Delete all waypoints where lat=0 and lon=0."""
    from geocaches.models import Waypoint
    qs = Waypoint.objects.filter(latitude=0.0, longitude=0.0)
    count = qs.count()
    if request.method == "POST":
        qs.delete()
        return render(request, "geocaches/tools/tools_result.html", {
            "title": "Remove 0,0 waypoints",
            "message": f"Deleted {count} waypoint{'s' if count != 1 else ''} with coordinates 0°/0°.",
        })
    return render(request, "geocaches/tools/tools_confirm.html", {
        "title": "Remove 0,0 waypoints",
        "description": f"This will permanently delete {count} waypoint{'s' if count != 1 else ''} "
                       f"where latitude and longitude are both 0°. These are placeholder waypoints "
                       f"that break map display and are exported to external devices without need.",
        "action_url": request.path,
        "submit_label": "Delete waypoints",
    })
