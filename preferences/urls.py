from django.urls import path
from . import views

app_name = "preferences"

urlpatterns = [
    path("settings/",                         views.settings_view,          name="settings"),
    path("settings/save-prefs/",              views.save_prefs,             name="save_prefs"),
    path("settings/save-map-display/",        views.save_map_display,       name="save_map_display"),
    path("settings/save-enrich/",             views.save_enrich,            name="save_enrich"),
    path("settings/save-cache-detail/",       views.save_cache_detail,      name="save_cache_detail"),
    path("settings/save-map-state/",          views.save_map_state,         name="save_map_state"),
    path("settings/locations-json/",          views.locations_json,         name="locations_json"),
    path("settings/add-preset/",              views.add_preset,             name="add_preset"),
    path("settings/delete-preset/",          views.delete_preset,          name="delete_preset"),
    path("settings/add-refpoint/",            views.add_refpoint,           name="add_refpoint"),
    path("settings/edit-refpoint/",           views.edit_refpoint,          name="edit_refpoint"),
    path("settings/delete-refpoint/",         views.delete_refpoint,        name="delete_refpoint"),
    path("settings/set-default-refpoint/",    views.set_default_refpoint,   name="set_default_refpoint"),
    path("settings/save-backup-prefs/",       views.save_backup_prefs,      name="save_backup_prefs"),
    path("settings/vacuum-now/",              views.vacuum_now,             name="vacuum_now"),
    path("settings/backup-now/",              views.backup_now,             name="backup_now"),
    path("location/current/",                  views.set_current_location,   name="set_current_location"),
    path("location/save/",                     views.save_location_json,     name="save_location_json"),
    path("scope/",                            views.set_scope,              name="set_scope"),
    path("log/",                               views.log_view,               name="log"),
    path("backup/download/<str:filename>/",   views.backup_download,        name="backup_download"),
    path("backup/restore/",                   views.backup_restore,         name="backup_restore"),
    path("backup/delete/",                    views.backup_delete,          name="backup_delete"),
    path("settings/save-gpx-export/",          views.save_gpx_export,        name="save_gpx_export"),
    path("settings/gpx-export-preset/add/",   views.add_gpx_export_preset,  name="add_gpx_export_preset"),
    path("settings/gpx-export-preset/delete/", views.delete_gpx_export_preset, name="delete_gpx_export_preset"),
    path("settings/gpx-export-preset/load/",  views.load_gpx_export_preset, name="load_gpx_export_preset"),
    path("settings/reset-gpx-export/",        views.reset_gpx_export,       name="reset_gpx_export"),
    path("settings/switch-database/",         views.switch_database,        name="switch_database"),
    path("settings/create-database/",         views.create_database,        name="create_database"),
    path("profile/",                          views.user_profile,           name="user_profile"),
    path("about/",                            views.about_view,             name="about"),
]
