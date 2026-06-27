# Re-export all public views so that `from . import views` in urls.py and
# `from preferences.views import X` in other modules work without changes.

from .settings import (  # noqa: F401
    settings_view,
    save_prefs,
    save_list_view,
    save_cache_detail,
    save_enrich,
    add_preset,
    delete_preset,
    set_scope,
    get_active_columns,
    get_active_preset_name,
)

from .image_cache import (  # noqa: F401
    _build_image_cache_context,
    save_image_cache_prefs,
    clear_image_cache,
)

from .accounts import (  # noqa: F401
    save_gc_username,
    fetch_gc_public_guid,
    save_al_prefs,
    refresh_total_finds,
    _fetch_total_platform_finds,
    add_log_template,
    delete_log_template,
    save_logging_prefs,
    log_view,
)

from .map_display import (  # noqa: F401
    save_map_state,
    save_map_display,
    locations_json,
    save_location_json,
    save_offline_prefs,
)

from .dashboard import (  # noqa: F401
    save_dashboard_maps,
    save_dashboard_stats,
    _build_country_finds,
    download_boundary,
    update_all_boundaries,
    enrich_locations_offline,
)

from .refpoints import (  # noqa: F401
    add_refpoint,
    edit_refpoint,
    delete_refpoint,
    set_default_refpoint,
    set_current_location,
)

from .database import (  # noqa: F401
    save_backup_prefs,
    vacuum_now,
    backup_now,
    backup_download,
    backup_delete,
    backup_restore,
    switch_database,
    create_database,
    _list_available_databases,
    _write_conf,
    _location_label,
)

from .profile import user_profile  # noqa: F401

from .gpx_export import (  # noqa: F401
    _get_gpx_export_settings,
    save_gpx_export,
    add_gpx_export_preset,
    delete_gpx_export_preset,
    load_gpx_export_preset,
    reset_gpx_export,
)

from .about import about_view  # noqa: F401

from ._helpers import _redirect_tab, _pop_msg  # noqa: F401
