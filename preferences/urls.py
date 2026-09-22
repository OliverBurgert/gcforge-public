from django.urls import path, re_path
from .views import about as views_about
from .views import accounts as views_accounts
from .views import dashboard as views_dashboard
from .views import database as views_database
from .views import fonts as views_fonts
from .views import gpx_export as views_gpx_export
from .views import image_cache as views_image_cache
from .views import map_display as views_map_display
from .views import offline_maps as views_offline_maps
from .views import profile as views_profile
from .views import profiles as views_profiles
from .views import refpoints as views_refpoints
from .views import settings as views_settings

app_name = "preferences"

urlpatterns = [
    path("settings/",                         views_settings.settings_view,          name="settings"),
    path("settings/save-prefs/",              views_settings.save_prefs,             name="save_prefs"),
    path("settings/save-logging-prefs/",      views_accounts.save_logging_prefs,     name="save_logging_prefs"),
    path("settings/save-gc-username/",        views_accounts.save_gc_username,       name="save_gc_username"),
    path("settings/fetch-gc-public-guid/",    views_accounts.fetch_gc_public_guid,   name="fetch_gc_public_guid"),
    path("settings/save-al-prefs/",           views_accounts.save_al_prefs,          name="save_al_prefs"),
    path("settings/save-map-display/",        views_map_display.save_map_display,       name="save_map_display"),
    path("settings/save-dashboard-maps/",     views_dashboard.save_dashboard_maps,    name="save_dashboard_maps"),
    path("settings/save-dashboard-stats/",    views_dashboard.save_dashboard_stats,   name="save_dashboard_stats"),
    path("settings/download-boundary/",       views_dashboard.download_boundary,       name="download_boundary"),
    path("settings/update-all-boundaries/",   views_dashboard.update_all_boundaries,   name="update_all_boundaries"),
    path("settings/save-enrich/",             views_settings.save_enrich,            name="save_enrich"),
    path("settings/enrich-offline/",          views_dashboard.enrich_locations_offline, name="enrich_locations_offline"),
    path("settings/save-cache-detail/",       views_settings.save_cache_detail,      name="save_cache_detail"),
    path("settings/save-list-view/",          views_settings.save_list_view,         name="save_list_view"),
    path("settings/save-map-state/",          views_map_display.save_map_state,         name="save_map_state"),
    path("settings/locations-json/",          views_map_display.locations_json,         name="locations_json"),
    path("settings/add-preset/",              views_settings.add_preset,             name="add_preset"),
    path("settings/delete-preset/",          views_settings.delete_preset,          name="delete_preset"),
    path("settings/add-log-template/",        views_accounts.add_log_template,       name="add_log_template"),
    path("settings/delete-log-template/",     views_accounts.delete_log_template,    name="delete_log_template"),
    path("settings/refresh-total-finds/",     views_accounts.refresh_total_finds,    name="refresh_total_finds"),
    path("settings/add-refpoint/",            views_refpoints.add_refpoint,           name="add_refpoint"),
    path("settings/edit-refpoint/",           views_refpoints.edit_refpoint,          name="edit_refpoint"),
    path("settings/delete-refpoint/",         views_refpoints.delete_refpoint,        name="delete_refpoint"),
    path("settings/set-default-refpoint/",    views_refpoints.set_default_refpoint,   name="set_default_refpoint"),
    path("settings/save-backup-prefs/",       views_database.save_backup_prefs,      name="save_backup_prefs"),
    path("settings/vacuum-now/",              views_database.vacuum_now,             name="vacuum_now"),
    path("settings/backup-now/",              views_database.backup_now,             name="backup_now"),
    path("location/current/",                  views_refpoints.set_current_location,   name="set_current_location"),
    path("location/save/",                     views_map_display.save_location_json,     name="save_location_json"),
    path("scope/",                            views_settings.set_scope,              name="set_scope"),
    path("log/",                               views_accounts.log_view,               name="log"),
    path("backup/download/<str:filename>/",   views_database.backup_download,        name="backup_download"),
    path("backup/restore/",                   views_database.backup_restore,         name="backup_restore"),
    path("backup/delete/",                    views_database.backup_delete,          name="backup_delete"),
    path("settings/save-gpx-export/",          views_gpx_export.save_gpx_export,        name="save_gpx_export"),
    path("settings/gpx-export-preset/add/",   views_gpx_export.add_gpx_export_preset,  name="add_gpx_export_preset"),
    path("settings/gpx-export-preset/delete/", views_gpx_export.delete_gpx_export_preset, name="delete_gpx_export_preset"),
    path("settings/gpx-export-preset/load/",  views_gpx_export.load_gpx_export_preset, name="load_gpx_export_preset"),
    path("settings/reset-gpx-export/",        views_gpx_export.reset_gpx_export,       name="reset_gpx_export"),
    path("settings/switch-database/",         views_database.switch_database,        name="switch_database"),
    path("settings/create-database/",         views_database.create_database,        name="create_database"),
    path("profiles/",                         views_profiles.profile_picker,         name="profile_picker"),
    path("profiles/switch/",                  views_profiles.switch_profile,         name="switch_profile"),
    path("profiles/rename/",                  views_profiles.rename_profile,         name="rename_profile"),
    path("profile/",                          views_profile.user_profile,           name="user_profile"),
    path("about/",                            views_about.about_view,             name="about"),
    path("settings/save-offline-prefs/",     views_map_display.save_offline_prefs,     name="save_offline_prefs"),
    path("settings/save-image-cache/",       views_image_cache.save_image_cache_prefs, name="save_image_cache_prefs"),
    path("settings/clear-image-cache/",      views_image_cache.clear_image_cache,      name="clear_image_cache"),
    path("offline-maps/areas.json",              views_offline_maps.areas_json,      name="offline_areas_json"),
    path("offline-maps/areas-partial",           views_offline_maps.areas_partial,   name="offline_areas_partial"),
    path("offline-maps/<int:pk>/tiles.pmtiles",  views_offline_maps.serve_pmtiles,   name="serve_pmtiles"),
    path("offline-maps/<int:pk>/download",       views_offline_maps.start_download,  name="offline_start_download"),
    path("offline-maps/<int:pk>/delete",         views_offline_maps.delete_area,     name="offline_delete_area"),
    path("offline-maps/create",                  views_offline_maps.create_area,     name="offline_create_area"),
    path("offline-maps/estimate",                views_offline_maps.estimate_tiles,  name="offline_estimate_tiles"),
    re_path(r"^fonts/(?P<fontstack>[^/]+)/(?P<range_str>\d+-\d+)\.pbf$",
            views_fonts.serve_map_glyph, name="map_font_glyph"),
]
