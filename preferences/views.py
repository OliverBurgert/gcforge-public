import collections
import configparser
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from django.conf import settings as django_settings
from django.http import FileResponse, Http404, HttpResponseNotAllowed
from django.shortcuts import redirect, render
from django.urls import reverse


def _redirect_tab(tab: str):
    return redirect(reverse("preferences:settings") + f"#{tab}")

from .columns import AVAILABLE_COLUMNS, BUILTIN_PRESETS, DEFAULT_PRESET
from .models import ColumnPreset, GpxExportPreset, GPX_EXPORT_DEFAULTS, ReferencePoint, UserPreference


def get_active_columns(request) -> set[str]:
    """
    Return the set of active column keys for the list view.
    GET param ?preset= takes precedence; falls back to saved preference.
    """
    preset_name = request.GET.get("preset") or UserPreference.get("active_column_preset", DEFAULT_PRESET)
    preset = ColumnPreset.objects.filter(name=preset_name).first()
    if preset:
        if request.GET.get("preset"):
            UserPreference.set("active_column_preset", preset_name)
        return set(preset.columns)
    return set(BUILTIN_PRESETS.get(DEFAULT_PRESET, []))


def get_active_preset_name(request) -> str:
    return request.GET.get("preset") or UserPreference.get("active_column_preset", DEFAULT_PRESET)


def settings_view(request):
    from . import backup as _backup
    import sqlite3 as _sqlite3
    from geocaches.models import Geocache, Log, Tag, Waypoint
    from accounts.views import _build_user_accounts_context, _build_platform_keys_context
    from accounts.models import UserAccount
    backup_dir = _backup.get_backup_dir()
    frag = _backup.fragmentation_info()
    db_path = _backup.get_db_path()
    db_stats = {
        "geocache_count":  Geocache.objects.count(),
        "found_count":     Geocache.objects.filter(found=True).count(),
        "log_count":       Log.objects.count(),
        "waypoint_count":  Waypoint.objects.count(),
        "tag_count":       Tag.objects.count(),
        "file_size":       db_path.stat().st_size if db_path.exists() else 0,
        "sqlite_version":  _sqlite3.sqlite_version,
        "db_path":         str(db_path),
    }
    available_databases = _list_available_databases(db_path, backup_dir)
    context = {
        "hint_display":         UserPreference.get("hint_display",         "hidden"),
        "log_truncate":         UserPreference.get("log_truncate",         True),
        "log_truncate_length":  UserPreference.get("log_truncate_length",  300),
        "coord_format": UserPreference.get("coord_format", "dd"),
        "distance_unit": UserPreference.get("distance_unit", "km"),
        "gc_username": UserPreference.get("gc_username", ""),
        "page_size": UserPreference.get("page_size", 50),
        "default_sort": UserPreference.get("default_sort", "gc_code"),
        "default_order": UserPreference.get("default_order", "asc"),
        "enrich_auto":          UserPreference.get("enrich_auto",          True),
        "enrich_elevation":     UserPreference.get("enrich_elevation",     True),
        "enrich_location":      UserPreference.get("enrich_location",      True),
        "drop_zero_waypoints":  UserPreference.get("drop_zero_waypoints",  True),
        "presets": ColumnPreset.objects.all(),
        "active_preset_name": UserPreference.get("active_column_preset", DEFAULT_PRESET),
        "available_columns": AVAILABLE_COLUMNS,
        "reference_points": ReferencePoint.objects.all(),
        "rp_list_json": json.dumps([
            {"id": rp.id, "name": rp.name, "lat": rp.latitude, "lon": rp.longitude, "is_home": rp.is_home}
            for rp in ReferencePoint.objects.all()
        ]),
        "user_accounts":  _build_user_accounts_context(),
        "account_msg":    request.session.pop("account_msg", None),
        "platform_keys":  _build_platform_keys_context(),
        "registered_platforms": set(UserAccount.objects.values_list("platform", flat=True)),
        # Backups
        "backup_auto_enabled":    UserPreference.get("backup_auto_enabled",    True),
        "backup_dir":             UserPreference.get("backup_dir",             ""),
        "backup_dir_effective":   str(backup_dir),
        "backup_rotate_count":    UserPreference.get("backup_rotate_count",    django_settings.BACKUP_ROTATE_COUNT),
        "backups":                _backup.list_backups(backup_dir),
        "backup_msg":             request.session.pop("backup_msg", None),
        "frag":                   frag,
        "db_stats":               db_stats,
        "available_databases":    available_databases,
        "has_backup_databases":   any(d["is_backup"] for d in available_databases),
        "db_switch_msg":          request.session.pop("db_switch_msg", None),
        # Map preferences
        "icon_set":               UserPreference.get("icon_set", "text"),
        "map_layout":             UserPreference.get("map_layout", "list"),
        "map_split_pct":          UserPreference.get("map_split_pct", 40),
        # Map display defaults
        "map_display_msg":        request.session.pop("map_display_msg", None),
        "map_style":              UserPreference.get("map_style", "outdoor"),
        "map_boundary_country":   UserPreference.get("map_boundary_country", True),
        "map_boundary_state":     UserPreference.get("map_boundary_state", True),
        "map_boundary_county":    UserPreference.get("map_boundary_county", True),
        "map_radius_circle":      UserPreference.get("map_radius_circle", True),
        "map_radius_shade":       UserPreference.get("map_radius_shade", True),
        "map_layer_sep_circles":  UserPreference.get("map_layer_sep_circles", False),
        "map_layer_corrected":    UserPreference.get("map_layer_corrected", False),
        "map_layer_waypoints":    UserPreference.get("map_layer_waypoints", True),
        "map_layer_labels":       UserPreference.get("map_layer_labels", "name"),
        "map_layer_lod":          UserPreference.get("map_layer_lod", True),
        # Import preferences
        "delete_after_import":    UserPreference.get("delete_after_import", False),
        # Logging / image preferences
        "log_image_strip_exif":   UserPreference.get("log_image_strip_exif", True),
        "log_image_max_px":       UserPreference.get("log_image_max_px", 1024),
        # GPX export
        "gpx_export": _get_gpx_export_settings(),
        "gpx_export_presets": GpxExportPreset.objects.all(),
    }
    return render(request, "preferences/settings.html", context)


def save_prefs(request):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    UserPreference.set("coord_format", request.POST.get("coord_format", "dd"))
    UserPreference.set("distance_unit", request.POST.get("distance_unit", "km"))
    gc_username = request.POST.get("gc_username", "").strip()
    UserPreference.set("gc_username", gc_username)
    try:
        page_size = max(10, min(500, int(request.POST.get("page_size", 50))))
    except (ValueError, TypeError):
        page_size = 50
    UserPreference.set("page_size", page_size)
    # Map preferences
    map_layout = request.POST.get("map_layout", "")
    if map_layout in ("list", "split", "map"):
        UserPreference.set("map_layout", map_layout)
    try:
        map_split_pct = max(20, min(80, int(request.POST.get("map_split_pct", 40))))
    except (ValueError, TypeError):
        map_split_pct = 40
    UserPreference.set("map_split_pct", map_split_pct)
    # Default sort
    default_sort = request.POST.get("default_sort", "gc_code")
    _VALID_SORTS = {
        "gc_code", "name", "cache_type", "size", "difficulty", "terrain",
        "status", "hidden_date", "last_found_date", "found_date",
        "fav_points", "distance_km", "bearing_deg",
    }
    if default_sort in _VALID_SORTS:
        UserPreference.set("default_sort", default_sort)
    default_order = request.POST.get("default_order", "asc")
    if default_order in ("asc", "desc"):
        UserPreference.set("default_order", default_order)
    # Import preferences
    UserPreference.set("delete_after_import", "delete_after_import" in request.POST)
    # Logging / image preferences
    UserPreference.set("log_image_strip_exif", "log_image_strip_exif" in request.POST)
    try:
        log_image_max_px = int(request.POST.get("log_image_max_px", 1024))
        if log_image_max_px not in (512, 1024, 1600, 2048, 0):
            log_image_max_px = 1024
    except (ValueError, TypeError):
        log_image_max_px = 1024
    UserPreference.set("log_image_max_px", log_image_max_px)
    # Icon set
    icon_set = request.POST.get("icon_set", "text")
    if icon_set in ("text", "cgeo"):
        UserPreference.set("icon_set", icon_set)
    return _redirect_tab("general")


def save_map_state(request):
    """AJAX POST: save map state preferences (layout, split %, center, zoom)."""
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    from django.http import JsonResponse
    _MAP_KEYS = {
        "map_layout", "map_split_pct", "map_center_lat", "map_center_lon", "map_zoom",
    }
    for key in _MAP_KEYS:
        val = request.POST.get(key)
        if val is not None:
            # Store numeric values as numbers
            if key in ("map_split_pct", "map_center_lat", "map_center_lon", "map_zoom"):
                try:
                    val = float(val)
                    if key == "map_split_pct":
                        val = max(20, min(80, int(val)))
                except (ValueError, TypeError):
                    continue
            UserPreference.set(key, val)
    return JsonResponse({"ok": True})


def locations_json(request):
    """GET: return reference points as JSON (for refreshing the map dropdown)."""
    from django.http import JsonResponse
    from preferences.models import ReferencePoint
    data = [
        {"id": rp.id, "name": rp.name, "lat": rp.latitude, "lon": rp.longitude, "home": rp.is_home}
        for rp in ReferencePoint.objects.all()
    ]
    return JsonResponse(data, safe=False)


def save_map_display(request):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    style = request.POST.get("map_style", "outdoor")
    if style in ("street", "outdoor", "aerial"):
        UserPreference.set("map_style", style)
    UserPreference.set("map_boundary_country", request.POST.get("map_boundary_country") == "1")
    UserPreference.set("map_boundary_state",   request.POST.get("map_boundary_state")   == "1")
    UserPreference.set("map_boundary_county",  request.POST.get("map_boundary_county")  == "1")
    UserPreference.set("map_radius_circle",    request.POST.get("map_radius_circle")    == "1")
    UserPreference.set("map_radius_shade",     request.POST.get("map_radius_shade")     == "1")
    UserPreference.set("map_layer_sep_circles", request.POST.get("map_layer_sep_circles") == "1")
    UserPreference.set("map_layer_corrected",  request.POST.get("map_layer_corrected")  == "1")
    UserPreference.set("map_layer_waypoints",  request.POST.get("map_layer_waypoints")  == "1")
    UserPreference.set("map_layer_labels",     request.POST.get("map_layer_labels", ""))
    UserPreference.set("map_layer_lod",       request.POST.get("map_layer_lod") == "1")
    request.session["map_display_msg"] = {"ok": True, "text": "Map display defaults saved."}
    return _redirect_tab("map")


def save_enrich(request):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    UserPreference.set("enrich_auto",          request.POST.get("enrich_auto")          == "1")
    UserPreference.set("enrich_elevation",      request.POST.get("enrich_elevation")      == "1")
    UserPreference.set("enrich_location",       request.POST.get("enrich_location")       == "1")
    UserPreference.set("drop_zero_waypoints",   request.POST.get("drop_zero_waypoints")   == "1")
    return _redirect_tab("enrichment")


def save_cache_detail(request):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    UserPreference.set("hint_display", request.POST.get("hint_display", "hidden"))
    UserPreference.set("log_truncate", request.POST.get("log_truncate") == "1")
    try:
        log_truncate_length = max(50, min(5000, int(request.POST.get("log_truncate_length", 300))))
    except (ValueError, TypeError):
        log_truncate_length = 300
    UserPreference.set("log_truncate_length", log_truncate_length)
    return _redirect_tab("cache-detail-view")


def add_preset(request):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    name = request.POST.get("preset_name", "").strip()
    selected = request.POST.getlist("columns")
    if name and selected:
        ColumnPreset.objects.update_or_create(
            name=name,
            defaults={"columns": selected, "is_builtin": False},
        )
    return _redirect_tab("columns")


def delete_preset(request):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    ColumnPreset.objects.filter(id=request.POST.get("preset_id"), is_builtin=False).delete()
    return _redirect_tab("columns")


def add_refpoint(request):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    from geocaches.coords import parse_lat_lon
    name = request.POST.get("rp_name", "").strip()
    lat_str = request.POST.get("rp_lat", "").strip()
    lon_str = request.POST.get("rp_lon", "").strip()
    note = request.POST.get("rp_note", "").strip()
    valid_from_str = request.POST.get("rp_valid_from", "").strip() or None
    is_default = request.POST.get("rp_default") == "1"
    is_home = request.POST.get("rp_home") == "1"
    result = parse_lat_lon(lat_str, lon_str)
    if name and result:
        lat, lon = result
        rp = ReferencePoint.objects.create(
            name=name,
            latitude=lat,
            longitude=lon,
            note=note,
            valid_from=valid_from_str,
            is_default=is_default,
            is_home=is_home,
        )
        if is_default:
            ReferencePoint.objects.exclude(pk=rp.pk).update(is_default=False)
    return _redirect_tab("reference-points")


def edit_refpoint(request):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    from geocaches.coords import parse_lat_lon
    rp_id = request.POST.get("rp_id")
    name = request.POST.get("rp_name", "").strip()
    lat_str = request.POST.get("rp_lat", "").strip()
    lon_str = request.POST.get("rp_lon", "").strip()
    note = request.POST.get("rp_note", "").strip()
    valid_from_str = request.POST.get("rp_valid_from", "").strip() or None
    is_home = request.POST.get("rp_home") == "1"
    result = parse_lat_lon(lat_str, lon_str)
    if rp_id and name and result:
        lat, lon = result
        ReferencePoint.objects.filter(id=rp_id).update(
            name=name,
            latitude=lat,
            longitude=lon,
            note=note,
            valid_from=valid_from_str,
            is_home=is_home,
        )
    return _redirect_tab("reference-points")


def delete_refpoint(request):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    ReferencePoint.objects.filter(id=request.POST.get("rp_id")).delete()
    return _redirect_tab("reference-points")


def set_default_refpoint(request):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    rp_id = request.POST.get("rp_id")
    ReferencePoint.objects.all().update(is_default=False)
    ReferencePoint.objects.filter(id=rp_id).update(is_default=True)
    return _redirect_tab("reference-points")


def set_current_location(request):
    from django.http import JsonResponse
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    try:
        data = json.loads(request.body)
        lat = float(data.get("latitude"))
        lon = float(data.get("longitude"))
    except (json.JSONDecodeError, TypeError, ValueError):
        return JsonResponse({"ok": False, "error": "Invalid coordinates"}, status=400)
    if not (-90 <= lat <= 90) or not (-180 <= lon <= 180):
        return JsonResponse({"ok": False, "error": "Coordinates out of range"}, status=400)
    rp, _created = ReferencePoint.objects.update_or_create(
        name="Current Location",
        defaults={"latitude": lat, "longitude": lon},
    )
    from geocaches.distance_cache import recompute_distances
    recompute_distances(rp)
    return JsonResponse({"ok": True, "id": rp.pk})


def save_location_json(request):
    """Create/update a named location. Used by map context menu and crosshair button."""
    from django.http import JsonResponse
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    try:
        data = json.loads(request.body)
        lat = float(data.get("latitude"))
        lon = float(data.get("longitude"))
        name = (data.get("name") or "").strip()
    except (json.JSONDecodeError, TypeError, ValueError):
        return JsonResponse({"ok": False, "error": "Invalid data"}, status=400)
    if not (-90 <= lat <= 90) or not (-180 <= lon <= 180):
        return JsonResponse({"ok": False, "error": "Coordinates out of range"}, status=400)
    if not name:
        return JsonResponse({"ok": False, "error": "Name required"}, status=400)
    note = (data.get("note") or "").strip()
    rp = ReferencePoint.objects.create(
        name=name, latitude=lat, longitude=lon, note=note,
    )
    return JsonResponse({"ok": True, "id": rp.pk, "name": rp.name})


def save_backup_prefs(request):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    UserPreference.set("backup_auto_enabled", request.POST.get("backup_auto_enabled") == "1")
    raw_dir = request.POST.get("backup_dir", "").strip()
    UserPreference.set("backup_dir", raw_dir)
    try:
        keep = max(1, min(50, int(request.POST.get("backup_rotate_count", 5))))
    except (ValueError, TypeError):
        keep = 5
    UserPreference.set("backup_rotate_count", keep)
    return _redirect_tab("database")


def vacuum_now(request):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    from . import backup as _backup
    try:
        result = _backup.do_vacuum(reason="manual")
        freed_mb = result["freed"] / 1024 / 1024
        after_mb = result["size_after"] / 1024 / 1024
        request.session["backup_msg"] = {
            "ok": True,
            "text": (
                f"Vacuum complete in {result['elapsed_s']:.1f} s — "
                f"freed {freed_mb:.1f} MB, database now {after_mb:.1f} MB."
            ),
        }
    except Exception as exc:
        request.session["backup_msg"] = {"ok": False, "text": f"Vacuum failed: {exc}"}
    return _redirect_tab("database")


def backup_now(request):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    import logging as _logging
    from . import backup as _backup
    from datetime import datetime as _dt
    _bklog = _logging.getLogger("geocaches.backup")
    raw_dir  = request.POST.get("manual_backup_dir",  "").strip()
    raw_name = request.POST.get("manual_backup_name", "").strip()
    dest_dir = Path(raw_dir) if raw_dir else _backup.get_backup_dir()
    if not raw_name:
        raw_name = "gcforge_backup_" + _dt.now().strftime("%Y-%m-%d_%H%M%S")
    if not raw_name.endswith(".sqlite3"):
        raw_name += ".sqlite3"
    dest = dest_dir / raw_name
    try:
        _bklog.info("--- Manual backup start: %s", dest)
        _backup.create_backup(dest)
        size_mb = dest.stat().st_size / 1024 / 1024
        _bklog.info("--- Manual backup done: %s (%.1f MB)", dest.name, size_mb)
        request.session["backup_msg"] = {"ok": True, "text": f"Backup saved: {dest}"}
    except Exception as exc:
        _bklog.error("Manual backup failed: %s", exc)
        request.session["backup_msg"] = {"ok": False, "text": f"Backup failed: {exc}"}
    return _redirect_tab("database")


def set_scope(request):
    """Save the 'Now Forging' scope checkboxes and redirect back."""
    if request.method == "POST":
        UserPreference.set("scope_found",          request.POST.get("scope_found")          == "1")
        UserPreference.set("scope_my_caches",      request.POST.get("scope_my_caches")      == "1")
        UserPreference.set("scope_unfound",        request.POST.get("scope_unfound")        == "1")
        UserPreference.set("scope_platform_gc",    request.POST.get("scope_platform_gc")    == "1")
        UserPreference.set("scope_platform_lc",    request.POST.get("scope_platform_lc")    == "1")
        UserPreference.set("scope_platform_oc",    request.POST.get("scope_platform_oc")    == "1")
        UserPreference.set("scope_platform_other", request.POST.get("scope_platform_other") == "1")
    return redirect(request.POST.get("next", "/"))


def log_view(request):
    """Display the last N lines from all log files (current + rotated), newest first."""
    log_dir = Path(django_settings.LOG_DIR)
    log_base = log_dir / "gcforge.log"

    # Collect all rotated files: gcforge.log, gcforge.log.1, ..., gcforge.log.4
    candidates = [log_base] + [Path(f"{log_base}.{i}") for i in range(1, 6)]
    lines = []
    for path in candidates:
        if path.exists():
            try:
                lines.extend(path.read_text(encoding="utf-8").splitlines())
            except OSError:
                pass

    lines.reverse()  # newest first (current file last written, lines within reversed)
    lines = lines[:500]

    import time as _time
    until = request.GET.get("until", "")
    auto_refresh = False
    if until:
        try:
            auto_refresh = int(until) > _time.time()
        except ValueError:
            pass

    return render(request, "preferences/log.html", {
        "lines": lines,
        "log_path": log_base,
        "auto_refresh": auto_refresh,
        "until": until,
    })


def backup_download(request, filename):
    """Serve a backup file from the backup directory as a download."""
    from . import backup as _backup
    backup_dir = _backup.get_backup_dir()
    safe_name = Path(filename).name  # strip any path components
    path = backup_dir / safe_name
    if not path.exists() or not path.is_file():
        raise Http404
    return FileResponse(open(path, "rb"), as_attachment=True, filename=safe_name)


def backup_delete(request):
    """Delete a backup file from the backup directory."""
    if request.method != "POST":
        return _redirect_tab("database")

    from . import backup as _backup
    backup_name = request.POST.get("backup_name", "").strip()
    if not backup_name:
        return _redirect_tab("database")

    backup_dir = _backup.get_backup_dir()
    safe_name = Path(backup_name).name
    path = backup_dir / safe_name
    try:
        if not path.exists():
            raise FileNotFoundError(f"File not found: {safe_name}")
        path.unlink()
        request.session["backup_msg"] = {"ok": True, "text": f"Deleted: {safe_name}"}
    except Exception as exc:
        request.session["backup_msg"] = {"ok": False, "text": f"Delete failed: {exc}"}

    return _redirect_tab("database")


def backup_restore(request):
    """Restore database from an existing backup or an uploaded file."""
    if request.method != "POST":
        return redirect("preferences:settings")

    from . import backup as _backup

    try:
        if "restore_file" in request.FILES:
            # Restore from uploaded file — write to a temp file first
            upload = request.FILES["restore_file"]
            with tempfile.NamedTemporaryFile(suffix=".sqlite3", delete=False) as tmp:
                for chunk in upload.chunks():
                    tmp.write(chunk)
                tmp_path = Path(tmp.name)
            try:
                _backup.restore_from_path(tmp_path)
            finally:
                tmp_path.unlink(missing_ok=True)
        else:
            backup_name = request.POST.get("backup_name", "").strip()
            if not backup_name:
                return redirect("preferences:settings")
            backup_dir = _backup.get_backup_dir()
            safe_name = Path(backup_name).name
            path = backup_dir / safe_name
            if not path.exists():
                raise FileNotFoundError(f"Backup not found: {safe_name}")
            _backup.restore_from_path(path)

        request.session["backup_msg"] = {
            "ok": True,
            "text": "Database restored successfully. The page has been reloaded from the restored database.",
        }
    except Exception as exc:
        request.session["backup_msg"] = {"ok": False, "text": f"Restore failed: {exc}"}

    return _redirect_tab("database")


def _list_available_databases(active_db_path, backup_dir):
    """Build the list of available databases from default, databases/, and backups/."""
    base_dir = django_settings.BASE_DIR
    databases_dir = django_settings.DATABASES_DIR
    active_resolved = active_db_path.resolve()
    result = []

    # 1. Default database
    default_path = base_dir / "db.sqlite3"
    result.append({
        "name": "db.sqlite3",
        "location": "(default)",
        "path": str(default_path),
        "size": default_path.stat().st_size if default_path.exists() else 0,
        "exists": default_path.exists(),
        "active": default_path.resolve() == active_resolved,
        "is_backup": False,
    })

    # 2. databases/ folder
    if databases_dir.exists():
        for f in sorted(databases_dir.glob("*.sqlite3")):
            result.append({
                "name": f.name,
                "location": "databases/",
                "path": str(f),
                "size": f.stat().st_size,
                "exists": True,
                "active": f.resolve() == active_resolved,
                "is_backup": False,
            })

    # 3. backups/ folder
    if backup_dir.exists():
        for f in sorted(backup_dir.glob("*.sqlite3"), key=lambda p: p.stat().st_mtime, reverse=True):
            if f.resolve() == active_resolved and any(d["active"] for d in result):
                continue
            result.append({
                "name": f.name,
                "location": "backups/",
                "path": str(f),
                "size": f.stat().st_size,
                "exists": True,
                "active": f.resolve() == active_resolved,
                "is_backup": True,
            })

    return result


def _write_conf(db_path_str):
    """Write gcforge.conf with the given database path."""
    conf_path = django_settings.BASE_DIR / "gcforge.conf"
    cfg = configparser.ConfigParser()
    cfg["database"] = {"path": db_path_str}
    with open(conf_path, "w") as f:
        cfg.write(f)


def switch_database(request):
    """Switch the active database by writing gcforge.conf."""
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    db_path = request.POST.get("db_path", "").strip()
    if not db_path:
        request.session["db_switch_msg"] = {"ok": False, "text": "No database path provided."}
        return _redirect_tab("database")

    target = Path(db_path)
    if not target.exists():
        request.session["db_switch_msg"] = {"ok": False, "text": f"Database file not found: {db_path}"}
        return _redirect_tab("database")

    # Check if it's the default — if so, remove the conf file to revert to default
    default_path = django_settings.BASE_DIR / "db.sqlite3"
    conf_path = django_settings.BASE_DIR / "gcforge.conf"
    if target.resolve() == default_path.resolve():
        if conf_path.exists():
            conf_path.unlink()
    else:
        _write_conf(str(target.resolve()))

    request.session["db_switch_msg"] = {
        "ok": True,
        "text": f"Database switched to {target.name}. Please restart the server for the change to take effect.",
    }
    return _redirect_tab("database")


def create_database(request):
    """Create a new empty database in databases/, run migrations, and switch to it."""
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    raw_name = request.POST.get("db_name", "").strip()
    if not raw_name:
        request.session["db_switch_msg"] = {"ok": False, "text": "Please enter a database name."}
        return _redirect_tab("database")

    # Sanitize: allow only alphanumeric, dash, underscore
    safe_name = "".join(c for c in raw_name if c.isalnum() or c in "-_")
    if not safe_name:
        request.session["db_switch_msg"] = {"ok": False, "text": "Invalid database name."}
        return _redirect_tab("database")

    if not safe_name.endswith(".sqlite3"):
        safe_name += ".sqlite3"

    databases_dir = django_settings.DATABASES_DIR
    databases_dir.mkdir(parents=True, exist_ok=True)
    new_path = databases_dir / safe_name

    if new_path.exists():
        request.session["db_switch_msg"] = {"ok": False, "text": f"Database {safe_name} already exists."}
        return _redirect_tab("database")

    try:
        # Write conf pointing to new database
        _write_conf(str(new_path.resolve()))

        # Run migrations via subprocess with the env var override
        env = os.environ.copy()
        env["GCFORGE_DATABASE"] = str(new_path.resolve())
        result = subprocess.run(
            [sys.executable, "manage.py", "migrate", "--run-syncdb"],
            cwd=str(django_settings.BASE_DIR),
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode != 0:
            # Clean up on failure
            if new_path.exists():
                new_path.unlink()
            conf_path = django_settings.BASE_DIR / "gcforge.conf"
            if conf_path.exists():
                conf_path.unlink()
            request.session["db_switch_msg"] = {
                "ok": False,
                "text": f"Migration failed: {result.stderr[:500]}",
            }
            return _redirect_tab("database")

        request.session["db_switch_msg"] = {
            "ok": True,
            "text": f"Database {safe_name} created and set as active. Please restart the server.",
        }
    except Exception as exc:
        # Clean up on failure
        if new_path.exists():
            new_path.unlink()
        conf_path = django_settings.BASE_DIR / "gcforge.conf"
        if conf_path.exists():
            conf_path.unlink()
        request.session["db_switch_msg"] = {"ok": False, "text": f"Failed to create database: {exc}"}

    return _redirect_tab("database")


def user_profile(request):
    from accounts.models import UserAccount
    from accounts import gc_client, keyring_util, okapi_client
    from preferences.models import UserPreference
    from datetime import date

    cards = []

    # --- GC accounts ---
    gc_accounts = UserAccount.objects.filter(platform="gc")
    if gc_accounts.exists():
        for acct in gc_accounts:
            card = {
                "platform": "geocaching.com",
                "platform_key": "gc",
                "username": acct.username,
                "profile_url": acct.profile_url,
                "error": None,
                "data": None,
            }
            if gc_client.has_api_tokens():
                try:
                    from geocaches.sync.gc_client import GCClient
                    client = GCClient()
                    raw = client._api.get(
                        "/users/me",
                        fields="referenceCode,findCount,hideCount,favoritePoints,"
                               "membershipLevelId,avatarUrl,homeCoordinates,"
                               "username,joinedDateUtc",
                    )
                    level = raw.get("membershipLevelId", 0)
                    level_names = {0: "Unknown", 1: "Basic", 2: "Charter", 3: "Premium"}
                    home = raw.get("homeCoordinates") or {}
                    card["data"] = {
                        "username": raw.get("username", ""),
                        "reference_code": raw.get("referenceCode", ""),
                        "find_count": raw.get("findCount"),
                        "hide_count": raw.get("hideCount"),
                        "favorite_points": raw.get("favoritePoints"),
                        "membership": level_names.get(level, f"Level {level}"),
                        "avatar_url": raw.get("avatarUrl", ""),
                        "join_date": (raw.get("joinedDateUtc") or "")[:10],
                        "home_lat": home.get("latitude"),
                        "home_lon": home.get("longitude"),
                    }
                    # Quota info — ensure today's records exist so we always show usage
                    from geocaches.sync.rate_limiter import QuotaTracker
                    today = date.today()
                    quotas = []
                    for mode in ("light", "full"):
                        remaining = QuotaTracker.remaining("gc", mode)
                        from geocaches.models import SyncQuota
                        sq = SyncQuota.objects.get(platform="gc", mode=mode, date=today)
                        quotas.append({
                            "mode": mode,
                            "used": sq.used,
                            "limit": sq.limit,
                            "remaining": remaining,
                        })
                    card["quotas"] = quotas
                except Exception as exc:
                    card["error"] = str(exc)
            else:
                card["error"] = "No GC API tokens available."
            cards.append(card)
    else:
        cards.append({
            "platform": "geocaching.com",
            "platform_key": "gc",
            "username": None,
            "profile_url": "",
            "error": "Not configured",
            "data": None,
        })

    # --- OC accounts ---
    oc_accounts = UserAccount.objects.filter(platform__startswith="oc_")
    if oc_accounts.exists():
        for acct in oc_accounts:
            card = {
                "platform": acct.get_platform_display(),
                "platform_key": acct.platform,
                "username": acct.username,
                "profile_url": acct.profile_url,
                "error": None,
                "data": None,
            }
            node_url = okapi_client.get_node_url(acct.platform)
            if not node_url:
                card["error"] = f"Unknown OC platform: {acct.platform}"
                cards.append(card)
                continue

            custom_key = UserPreference.get(f"okapi_consumer_key_{acct.platform}", "")
            custom_secret = UserPreference.get(f"okapi_consumer_secret_{acct.platform}", "")
            creds = okapi_client.get_consumer_credentials(acct.platform, custom_key, custom_secret)
            if not creds:
                card["error"] = "No consumer key available."
                cards.append(card)
                continue

            consumer_key, consumer_secret = creds
            oauth_creds = keyring_util.get_oauth_token(acct.platform, acct.user_id)

            try:
                fields = "uuid|username|profile_url|caches_found|caches_notfound|caches_hidden|rcmds_given|date_registered|home_location"
                if oauth_creds:
                    oauth_token, oauth_token_secret = oauth_creds
                    raw = okapi_client._get_level3(
                        f"{node_url}/okapi/services/users/user",
                        {"fields": fields},
                        consumer_key, consumer_secret,
                        oauth_token, oauth_token_secret,
                    )
                else:
                    raw = okapi_client._get_level1(
                        f"{node_url}/okapi/services/users/user",
                        {"fields": fields, "user_uuid": acct.user_id},
                        consumer_key,
                    )
                home = raw.get("home_location") or ""
                home_lat = None
                home_lon = None
                if home and "|" in home:
                    parts = home.split("|")
                    try:
                        home_lat = float(parts[0])
                        home_lon = float(parts[1])
                    except (ValueError, IndexError):
                        pass
                card["data"] = {
                    "username": raw.get("username", ""),
                    "uuid": raw.get("uuid", ""),
                    "profile_url": raw.get("profile_url", ""),
                    "caches_found": raw.get("caches_found"),
                    "caches_notfound": raw.get("caches_notfound"),
                    "caches_hidden": raw.get("caches_hidden"),
                    "rcmds_given": raw.get("rcmds_given"),
                    "date_registered": raw.get("date_registered", ""),
                    "home_lat": home_lat,
                    "home_lon": home_lon,
                }
            except Exception as exc:
                card["error"] = str(exc)
            cards.append(card)
    else:
        has_oc = any(p.startswith("oc_") for p, _ in UserAccount.PLATFORM_CHOICES)
        if has_oc:
            cards.append({
                "platform": "opencaching",
                "platform_key": "oc",
                "username": None,
                "profile_url": "",
                "error": "Not configured",
                "data": None,
            })

    return render(request, "preferences/user_profile.html", {"cards": cards})


# ---------------------------------------------------------------------------
# GPX Export settings
# ---------------------------------------------------------------------------

def _get_gpx_export_settings():
    """Return current GPX export settings, merging with defaults."""
    saved = UserPreference.get("gpx_export_settings", {}) or {}
    return {**GPX_EXPORT_DEFAULTS, **saved}


def save_gpx_export(request):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    p = request.POST
    settings = {
        "notes_gcforge":          "notes_gcforge"    in p,
        "notes_gc_user":          "notes_gc_user"    in p,
        "notes_field_notes":      "notes_field_notes" in p,
        "notes_corrected":        "notes_corrected"  in p,
        "notes_fuse":             p.get("notes_fuse", "fuse") == "fuse",
        "wp_hidden":              "wp_hidden"         in p,
        "wp_completed":           "wp_completed"      in p,
        "wp_completed_as_hidden": "wp_completed_as_hidden" in p,
        "cc_original_as_wp":      "cc_original_as_wp" in p,
        "logs_max":               p.get("logs_max", "").strip(),
        "logs_my_on_top":         "logs_my_on_top"   in p,
        "alc_stages":             p.get("alc_stages",    "child_and_export"),
        "alc_completed":          p.get("alc_completed", "found_invisible"),
        "events_exclude_past":    "events_exclude_past" in p,
        "events_days_ahead":      p.get("events_days_ahead", "").strip(),
    }
    UserPreference.set("gpx_export_settings", settings)
    return _redirect_tab("gpx-export")


def add_gpx_export_preset(request):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    name = request.POST.get("preset_name", "").strip()
    if name:
        settings = _get_gpx_export_settings()
        GpxExportPreset.objects.update_or_create(name=name, defaults={"settings": settings})
    return _redirect_tab("gpx-export")


def delete_gpx_export_preset(request):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    pk = request.POST.get("preset_id")
    GpxExportPreset.objects.filter(pk=pk).delete()
    return _redirect_tab("gpx-export")


def load_gpx_export_preset(request):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    pk = request.POST.get("preset_id")
    preset = GpxExportPreset.objects.filter(pk=pk).first()
    if preset:
        merged = {**GPX_EXPORT_DEFAULTS, **preset.settings}
        UserPreference.set("gpx_export_settings", merged)
    return _redirect_tab("gpx-export")


def reset_gpx_export(request):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    UserPreference.set("gpx_export_settings", GPX_EXPORT_DEFAULTS)
    return _redirect_tab("gpx-export")


def about_view(request):
    return render(request, "preferences/about.html")
