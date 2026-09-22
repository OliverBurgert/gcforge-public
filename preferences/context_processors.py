from pathlib import Path

from django.conf import settings as django_settings

from gcforge import profiles as gcforge_profiles
from geocaches.runtime_cache import TTLCache, get_available_platforms

from .models import UserPreference

# Connected-platform indicator: hits the OS keyring per account (and may
# trigger a background GC check), so it's memoised for 60s and invalidated
# on account add/edit/delete/login/OAuth callback and platform-key save
# (accounts/views.py) via invalidate_connected_platforms_cache().
_connected_platforms_cache = TTLCache(ttl_seconds=60)

# Profile display name + count: gcforge.profiles scans the filesystem.
# Cached for the process lifetime (profile switches restart the process) and
# invalidated on profile create/rename (preferences/views/profiles.py,
# preferences/views/database.py) via invalidate_profile_cache().
_profile_info_cache = TTLCache(ttl_seconds=None)

PLATFORM_LABELS = {
    "gc":    "GC",
    "lc":    "Lab",
    "oc":    "OC",
    "other": "Other",
}

# Short display labels for individual OC nodes in the navbar
_OC_NODE_LABELS = {
    "oc_de": "OC.de",
    "oc_pl": "OC.pl",
    "oc_uk": "OC.uk",
    "oc_nl": "OC.nl",
    "oc_us": "OC.us",
}


def _connected_platforms():
    """Cached (60s) wrapper around _compute_connected_platforms() — see
    invalidate_connected_platforms_cache()."""
    return _connected_platforms_cache.get_or_compute(_compute_connected_platforms)


def invalidate_connected_platforms_cache():
    _connected_platforms_cache.invalidate()


def invalidate_profile_cache():
    _profile_info_cache.invalidate()


def _compute_profile_info():
    db_path = Path(django_settings.DATABASES["default"]["NAME"])
    return {
        "name":  gcforge_profiles.active_profile_display_name(db_path, django_settings.DATA_DIR),
        "count": len(gcforge_profiles.list_profiles(django_settings.DATA_DIR)),
    }


def _compute_connected_platforms():
    """
    Return a list of dicts for the 'connected to' navbar indicator.

    Each dict: {platform, label, level}
      level 3 = full API access (OAuth/bearer token in keyring)
      level 2 = password stored (website session access only, GC)
      level 1 = API key only (consumer key available, no user login, OC)
    """
    from accounts.models import UserAccount
    from accounts.okapi_client import _BUNDLED_KEYS
    from accounts import keyring_util

    rows = []

    # GC: level 3 = verified API access, level 1 = token file exists (unverified)
    gc_accounts = UserAccount.objects.filter(platform="gc")
    if gc_accounts.exists():
        from accounts import gc_client as _gc
        if _gc.has_api_tokens():
            _gc.ensure_gc_checked()  # trigger background check if stale/unverified
            level = 3 if _gc.is_gc_api_verified() else 1
            rows.append({"platform": "gc", "label": "GC", "level": level})
        elif any(keyring_util.get_password("gc", acct.username) for acct in gc_accounts):
            rows.append({"platform": "gc", "label": "GC", "level": 2})

    # OC nodes: check for OAuth tokens and consumer keys
    for platform, label in _OC_NODE_LABELS.items():
        has_level3 = any(
            keyring_util.has_oauth_token(acct.platform, acct.user_id)
            for acct in UserAccount.objects.filter(platform=platform)
        )
        if has_level3:
            rows.append({"platform": platform, "label": label, "level": 3})
            continue
        custom_key = UserPreference.get(f"okapi_consumer_key_{platform}", "")
        if custom_key or platform in _BUNDLED_KEYS:
            rows.append({"platform": platform, "label": label, "level": 1})
    return rows


def forging_scope(request):
    """Expose the persistent 'Now Forging' scope flags and available platforms to every template."""
    available_platforms = get_available_platforms()

    # Locations for map quick-jump
    from preferences.models import ReferencePoint
    import json as _json
    locations_json = _json.dumps([
        {"id": rp.id, "name": rp.name, "lat": rp.latitude, "lon": rp.longitude, "home": rp.is_home}
        for rp in ReferencePoint.objects.all()
    ])

    profile_info = _profile_info_cache.get_or_compute(_compute_profile_info)

    return {
        "active_profile_name":   profile_info["name"],
        "profile_count":         profile_info["count"],
        "scope_found":          UserPreference.get("scope_found",          True),
        "scope_my_caches":      UserPreference.get("scope_my_caches",      True),
        "scope_unfound":        UserPreference.get("scope_unfound",        True),
        "scope_platform_gc":    UserPreference.get("scope_platform_gc",    True),
        "scope_platform_lc":    UserPreference.get("scope_platform_lc",    True),
        "scope_platform_oc":    UserPreference.get("scope_platform_oc",    True),
        "scope_platform_other": UserPreference.get("scope_platform_other", True),
        "available_platforms":  available_platforms,
        "platform_labels":      PLATFORM_LABELS,
        "connected_platforms":  _connected_platforms(),
        "icon_set":             UserPreference.get("icon_set", "cgeo"),
        "distance_unit":        UserPreference.get("distance_unit", "km"),
        "locations_json":       locations_json,
        "map_prefs_json":       _json.dumps({
            "icon_set":         UserPreference.get("icon_set", "cgeo"),
            "distance_unit":    UserPreference.get("distance_unit", "km"),
            "layout":           UserPreference.get("map_layout",           "list"),
            "style":            UserPreference.get("map_style",            "outdoor"),
            "boundary_country": UserPreference.get("map_boundary_country", True),
            "boundary_state":   UserPreference.get("map_boundary_state",   True),
            "boundary_county":  UserPreference.get("map_boundary_county",  True),
            "radius_circle":    UserPreference.get("map_radius_circle",    True),
            "radius_shade":     UserPreference.get("map_radius_shade",     True),
            "layer_sep_circles": UserPreference.get("map_layer_sep_circles", False),
            "layer_alc_circles": UserPreference.get("map_layer_alc_circles", False),
            "layer_corrected":  UserPreference.get("map_layer_corrected",  False),
            "layer_waypoints":  UserPreference.get("map_layer_waypoints",  True),
            "layer_labels":     UserPreference.get("map_layer_labels",     "name"),
            "layer_lod":        UserPreference.get("map_layer_lod",        True),
        }),
    }
