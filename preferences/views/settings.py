import json

from django.conf import settings as django_settings
from django.http import HttpResponseNotAllowed
from django.shortcuts import render

from preferences.models import (
    ColumnPreset, GpxExportPreset, LOG_TEMPLATE_SCOPES,
    LogTemplate, ReferencePoint, UserPreference,
)
from preferences.columns import AVAILABLE_COLUMNS, BUILTIN_PRESETS, DEFAULT_PRESET
from ._helpers import _redirect_tab
from .image_cache import _build_image_cache_context
from .gpx_export import _get_gpx_export_settings
from .dashboard import _build_country_finds
from .database import _list_available_databases


def _compose_smileys():
    """Lazy import to avoid pulling geocaches at module import time."""
    from geocaches.log_format import COMPOSE_SMILEYS
    return COMPOSE_SMILEYS


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
    from preferences import dashboard_maps as dashboard_maps_mod
    from preferences import backup as _backup
    import sqlite3 as _sqlite3
    from geocaches.models import Geocache, Log, Tag, Waypoint
    from accounts.views import _build_user_accounts_context, _build_platform_keys_context
    from accounts.models import UserAccount
    from preferences.models import OfflineMapArea
    from preferences.services.offline_maps import _get_offline_maps_dir, get_latest_protomaps_url
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
    dashboard_maps_cfg = dashboard_maps_mod.get_config()
    country_cfg = next((m for m in dashboard_maps_cfg if m["type"] == "country"), None)
    county_cfg = next((m for m in dashboard_maps_cfg if m["type"] == "county"), None)
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
        "cache_type_display": UserPreference.get("cache_type_display", "icon"),
        "enrich_auto":          UserPreference.get("enrich_auto",          True),
        "enrich_elevation":     UserPreference.get("enrich_elevation",     True),
        "enrich_location":      UserPreference.get("enrich_location",      True),
        "drop_zero_waypoints":  UserPreference.get("drop_zero_waypoints",  True),
        "presets": ColumnPreset.objects.all(),
        "active_preset_name": UserPreference.get("active_column_preset", DEFAULT_PRESET),
        "available_columns": AVAILABLE_COLUMNS,
        "log_templates": LogTemplate.objects.all(),
        "log_template_scopes": LOG_TEMPLATE_SCOPES,
        "compose_smileys": _compose_smileys(),
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
        "map_layer_alc_circles":  UserPreference.get("map_layer_alc_circles", False),
        "map_layer_corrected":    UserPreference.get("map_layer_corrected", False),
        "map_layer_waypoints":    UserPreference.get("map_layer_waypoints", True),
        "map_layer_labels":       UserPreference.get("map_layer_labels", "name"),
        "map_layer_lod":          UserPreference.get("map_layer_lod", True),
        # Adventure Lab preferences
        "gc_public_guid":          UserPreference.get("gc_public_guid", ""),
        # Import preferences
        "delete_after_import":    UserPreference.get("delete_after_import", False),
        # Logging / image preferences
        "log_image_strip_exif":   UserPreference.get("log_image_strip_exif", True),
        "log_image_max_px":       UserPreference.get("log_image_max_px", 1024),
        "auto_fetch_tb_on_log":   UserPreference.get("auto_fetch_tb_on_log", False),
        # GPX export
        "gpx_export": _get_gpx_export_settings(),
        "gpx_export_presets": GpxExportPreset.objects.all(),
        # Offline Maps
        "offline_areas": OfflineMapArea.objects.all().order_by('-created_at'),
        "offline_maps_dir": UserPreference.get('offline_maps_dir', ''),
        "offline_source_url": UserPreference.get('offline_source_url', '') or get_latest_protomaps_url() or '',
        "offline_maps_disk_path": str(_get_offline_maps_dir()),
        # Image cache
        "image_cache": _build_image_cache_context(),
        # Dashboard maps
        "dashboard_maps": dashboard_maps_cfg,
        "dashboard_map_bundled": list(dashboard_maps_mod.BUNDLED_LEVELS),
        "dashboard_msg": request.session.pop("dashboard_msg", None),
        "dashboard_stats_msg": request.session.pop("dashboard_stats_msg", None),
        "enrich_offline_msg": request.session.pop("enrich_offline_msg", None),
        "dashboard_country_finds": _build_country_finds(country_cfg, county_cfg),
        # Dashboard statistics platform filter
        "stats_include_oc":       UserPreference.get("stats_include_oc", True),
        "stats_include_al":       UserPreference.get("stats_include_al", False),
        # Server-side tab selection — read by the template's active-class flags.
        "active_tab": (request.GET.get("tab") or "general").strip(),
    }
    return render(request, "preferences/settings.html", context)


def save_prefs(request):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    UserPreference.set("coord_format", request.POST.get("coord_format", "dd"))
    UserPreference.set("distance_unit", request.POST.get("distance_unit", "km"))
    # Map preferences
    map_layout = request.POST.get("map_layout", "")
    if map_layout in ("list", "split", "split-detail", "map"):
        UserPreference.set("map_layout", map_layout)
    try:
        map_split_pct = max(20, min(80, int(request.POST.get("map_split_pct", 40))))
    except (ValueError, TypeError):
        map_split_pct = 40
    UserPreference.set("map_split_pct", map_split_pct)
    # Import preferences
    UserPreference.set("delete_after_import", "delete_after_import" in request.POST)
    # Icon set
    icon_set = request.POST.get("icon_set", "text")
    if icon_set in ("text", "cgeo"):
        UserPreference.set("icon_set", icon_set)
    return _redirect_tab("general")


def save_list_view(request):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    cache_type_display = request.POST.get("cache_type_display", "icon")
    if cache_type_display in ("icon", "icon_text", "text"):
        UserPreference.set("cache_type_display", cache_type_display)
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
    try:
        page_size = max(10, min(500, int(request.POST.get("page_size", 50))))
    except (ValueError, TypeError):
        page_size = 50
    UserPreference.set("page_size", page_size)
    return _redirect_tab("list-view")


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


def save_enrich(request):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    UserPreference.set("enrich_auto",          request.POST.get("enrich_auto")          == "1")
    UserPreference.set("enrich_elevation",      request.POST.get("enrich_elevation")      == "1")
    UserPreference.set("enrich_location",       request.POST.get("enrich_location")       == "1")
    UserPreference.set("drop_zero_waypoints",   request.POST.get("drop_zero_waypoints")   == "1")
    return _redirect_tab("enrichment")


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
    return _redirect_tab("list-view")


def delete_preset(request):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    ColumnPreset.objects.filter(id=request.POST.get("preset_id"), is_builtin=False).delete()
    return _redirect_tab("list-view")


def set_scope(request):
    """Save the 'Now Forging' scope checkboxes and redirect back."""
    from django.shortcuts import redirect as _redirect
    if request.method == "POST":
        UserPreference.set("scope_found",          request.POST.get("scope_found")          == "1")
        UserPreference.set("scope_my_caches",      request.POST.get("scope_my_caches")      == "1")
        UserPreference.set("scope_unfound",        request.POST.get("scope_unfound")        == "1")
        UserPreference.set("scope_platform_gc",    request.POST.get("scope_platform_gc")    == "1")
        UserPreference.set("scope_platform_lc",    request.POST.get("scope_platform_lc")    == "1")
        UserPreference.set("scope_platform_oc",    request.POST.get("scope_platform_oc")    == "1")
        UserPreference.set("scope_platform_other", request.POST.get("scope_platform_other") == "1")
    return _redirect(request.POST.get("next", "/"))
