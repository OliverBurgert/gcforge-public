from django.http import HttpResponseNotAllowed

from preferences.models import UserPreference
from ._helpers import _redirect_tab, _pop_msg


def _build_image_cache_context():
    from geocaches.services.image_cache import (
        CATEGORIES, DEFAULT_EXCLUSIONS, auto_delete_days, disk_usage_by_category,
        is_category_enabled, refresh_days,
    )
    usage = disk_usage_by_category()
    categories = []
    for key, meta in CATEGORIES.items():
        cat = {
            "key":    key,
            "label":  meta["label"],
            "splits": meta["splits"],
            "count":  usage.get(key, {}).get("count", 0),
            "bytes":  usage.get(key, {}).get("bytes", 0),
        }
        if meta["splits"]:
            cat["sub_toggles"] = [
                (s, is_category_enabled(key, state=s)) for s in meta["splits"]
            ]
        else:
            cat["value"] = is_category_enabled(key)
        categories.append(cat)
    total_bytes = sum(c["bytes"] for c in categories)
    total_count = sum(c["count"] for c in categories)
    return {
        "categories":   categories,
        "exclusions":   UserPreference.get("image_cache.exclusions", DEFAULT_EXCLUSIONS),
        "total_count":  total_count,
        "total_bytes":  total_bytes,
        "refresh_days":    refresh_days(),
        "auto_delete_days": auto_delete_days(),
        "msg":             _pop_msg(),
    }


def save_image_cache_prefs(request):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    from geocaches.services.image_cache import CATEGORIES
    for key, meta in CATEGORIES.items():
        if meta["splits"]:
            for sub in meta["splits"]:
                UserPreference.set(f"image_cache.{key}.{sub}", f"{key}.{sub}" in request.POST)
        else:
            UserPreference.set(f"image_cache.{key}", key in request.POST)
    UserPreference.set("image_cache.exclusions", request.POST.get("image_cache.exclusions", "").strip())
    try:
        days = max(0, int(request.POST.get("image_cache.refresh_days", "30")))
    except (ValueError, TypeError):
        days = 30
    UserPreference.set("image_cache.refresh_days", days)
    try:
        auto_days = max(0, int(request.POST.get("image_cache.auto_delete_days", "0")))
    except (ValueError, TypeError):
        auto_days = 0
    UserPreference.set("image_cache.auto_delete_days", auto_days)
    return _redirect_tab("images")


def clear_image_cache(request):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    from geocaches.services.image_cache import (
        CATEGORIES, clear_category, clear_excluded, clear_older_than,
    )
    if request.POST.get("clear_old_days"):
        try:
            days = int(request.POST["clear_old_days"])
        except (ValueError, TypeError):
            days = 0
        if days > 0:
            clear_older_than(days)
        return _redirect_tab("images")
    if request.POST.get("purge_excluded"):
        clear_excluded()
        return _redirect_tab("images")
    if request.POST.get("refresh_stale"):
        from geocaches.services.image_cache import refresh_stale
        refresh_stale()
        return _redirect_tab("images")
    if request.POST.get("sweep_orphans"):
        from geocaches.services.image_cache import sweep_orphan_files
        sweep_orphan_files()
        return _redirect_tab("images")
    if request.POST.get("sweep_non_images"):
        from geocaches.services.image_cache import sweep_non_image_files
        sweep_non_image_files()
        return _redirect_tab("images")
    target = (request.POST.get("category") or "").strip()
    if target == "__all__":
        for key in CATEGORIES:
            clear_category(key)
    elif target in CATEGORIES:
        clear_category(target)
    return _redirect_tab("images")
