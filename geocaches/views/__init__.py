# Re-export all public views so that `from . import views` in urls.py and
# `from geocaches.views import X` in other modules work without changes.

from .list import cache_list, _filtered_qs, SORT_FIELDS, PAGE_SIZE  # noqa: F401

from .detail import (  # noqa: F401
    cache_detail,
    al_answer_save,
    _get_cache,
    _build_log_submit_context,
    _parse_image_attachments,
    _parse_logged_at,
)

from .notes_logs import (  # noqa: F401
    log_submit,
    log_delete,
    note_add,
    note_update,
    note_delete,
    corrected_coords_save,
    oc_passphrase_save,
)

from .imports import (  # noqa: F401
    import_gpx,
    import_gsak,
    import_lab2gpx,
    import_fieldnotes,
    import_gsak_locations,
    import_al_founds,
    import_al_founds_preview,
    import_al_founds_status,
    import_al_recover_founds,
    import_al_recover_founds_status,
    import_by_code,
    import_alc_from_url,
    sync_al_stage_dates,
    sync_al_stage_dates_status,
    detect_gpx_format_ajax,
    tools_remove_zero_waypoints,
    _derive_wpts_path,
    _is_wpts_file,
)

from .bulk import bulk_logging, bulk_map_visibility  # noqa: F401

from .tools_ftf import (  # noqa: F401
    tools_check_ftf,
    tools_ftf_markers,
    ftf_verify_row,
)

from .tools_dedup import (  # noqa: F401
    tools_duped_my_logs,
    tools_duped_cache_logs,
    tools_misplaced_codes,
    tools_duplicate_caches,
)

from .tools_fused import (  # noqa: F401
    tools_manage_fused,
    tools_unlinked_oc,
)

from .ignore_lists import (  # noqa: F401
    page as ignore_lists_page,
    add as ignore_lists_add,
    remove as ignore_lists_remove,
    edit_notes as ignore_lists_edit_notes,
    remove_archived as ignore_lists_remove_archived,
    import_gsak as ignore_lists_import_gsak,
    import_gsak_preview as ignore_lists_import_gsak_preview,
    sync_gc as ignore_lists_sync_gc,
    sync_oc as ignore_lists_sync_oc,
    refresh as ignore_lists_refresh,
    cache_ignore as ignore_lists_cache_ignore,
    cache_unignore as ignore_lists_cache_unignore,
)

from .tools_events import (  # noqa: F401
    tools_event_calendar,
    tools_event_ical,
    tools_remove_unattended_past_events,
)

from .calendar import (  # noqa: F401
    calendar_feed,
    calendar_agenda,
    calendar_sync_events,
    calendar_add_missing,
    calendar_remove_missing,
    calendar_clear,
    calendar_toggle_alarm,
    calendar_delete_entry,
    calendar_day_candidates,
)

from .pq import (  # noqa: F401
    pq_management,
    pq_list_json,
    pq_rows_json,
    pq_match_preview,
)

from .notifications import (  # noqa: F401
    notifications_page,
    notifications_sync,
    notifications_apply_diff,
    notifications_bulk_create,
    notification_toggle,
    notification_delete,
    notification_edit,
    notification_set_location,
    notification_pull,
    notification_push,
    notifications_region_set_enabled,
    notifications_region_delete,
    notifications_region_pull,
    notifications_region_push,
    notifications_alt_emails,
    notifications_map_circles,
    oc_notification_pull,
    oc_notification_push,
    oc_notification_save,
    oc_notification_save_globals,
    oc_neighbourhood_create,
    oc_neighbourhood_delete,
)

from .tags import (  # noqa: F401
    tag_management,
    tags_json,
    bulk_tag_add,
    bulk_tag_remove,
    cache_tag_edit,
)

from .cache_actions import (  # noqa: F401
    cache_toggle_lock,
    cache_fetch_logs,
    al_fetch_logs,
    cache_refresh,
    cache_defuse,
    cache_delete,
    cache_delete_filtered,
    cache_delete_progress,
    cache_enrich,
    cache_location_options,
    cache_save_location,
    enrich_status,
    enrich_cancel,
    cache_update,
    update_status,
    update_cancel,
    save_map_state,
    reset_map_state,
    set_as_reference_point,
    set_map_visibility,
)

from .export import (  # noqa: F401
    cache_export_gpx,
    cache_detail_export_gpx,
    export_recent_folders,
)

from .gps import gps_detect_devices, gps_recent_devices, send_to_gps  # noqa: F401

from .tools_image_gallery import (  # noqa: F401
    tools_image_gallery_config,
    tools_image_gallery_generate,
    tools_image_gallery_view,
    tools_image_gallery_export_html,
    tools_image_gallery_export_odf,
)

from .saved_filters import (  # noqa: F401
    saved_filter_delete,
    tree_filter_apply,
    tree_filter_save,
    where_clause_save,
    where_clause_delete,
)

from .trash import trash_list, trash_restore, trash_purge, trash_empty  # noqa: F401

from .dashboard import (  # noqa: F401
    dashboard, dashboard_latest_additions,
    dashboard_statistics, dashboard_alc, dashboard_maps_panel,
    dashboard_stat_tables, dashboard_stat_table, dashboard_missing,
    dashboard_bearing_data, dashboard_alc_theme,
    dashboard_360_data, dashboard_360_missing,
    dashboard_360_grid, dashboard_360_set_location,
    dashboard_souvenirs_list, dashboard_souvenirs_refresh,
    dashboard_souvenirs_tag_editor, dashboard_souvenirs_set_tags,
    dashboard_souvenirs_tags_manage,
    dashboard_treasures_list, dashboard_treasures_refresh,
    dashboard_treasures_candidates, dashboard_treasures_detail,
    dashboard_region_data, dashboard_county_data, dashboard_district_data,
    dashboard_region_flag,
)
