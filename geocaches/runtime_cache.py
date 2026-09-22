"""Process-level caches shared across the app.

Not a distributed cache: state lives only in this process, which is correct
for a single-user, one-process-per-profile desktop app — profile switches
restart the process (see gcforge/profiles.py). ``TTLCache`` is the generic
primitive (reused by preferences/context_processors.py for the
connected-platforms and profile-info caches); platform availability, the
country list and the attribute groups are the concrete instances this
module owns, since they depend on the Geocache/Attribute models.

See docs/architecture-review-2026-09.md §4.1 item 1 and
docs/architecture-review-2026-09-workplan.md WP-01/WP-04.
"""
import threading
import time


class TTLCache:
    """A single cached value behind a lock, with an optional TTL.

    get_or_compute(fn) returns the cached value if present and not expired,
    else calls fn() and caches the result. invalidate() forces the next
    get_or_compute() call to recompute. ttl_seconds=None means "process
    lifetime" — the value never expires on its own; invalidate() is the only
    way to force a refresh.
    """

    def __init__(self, ttl_seconds=None):
        self._ttl = ttl_seconds
        self._lock = threading.Lock()
        self._value = None
        self._set_at = None

    def get_or_compute(self, fn):
        with self._lock:
            if self._set_at is not None:
                if self._ttl is None or (time.monotonic() - self._set_at) < self._ttl:
                    return self._value
        value = fn()
        with self._lock:
            self._value = value
            self._set_at = time.monotonic()
        return value

    def invalidate(self):
        with self._lock:
            self._value = None
            self._set_at = None


# Platform-availability cache — which of gc/lc/oc/other have at least one
# live cache. Backs the navbar's "Now Forging" platform checkboxes
# (preferences/context_processors.py:forging_scope). A 5-minute TTL is a
# safety net; explicit invalidation happens wherever caches are created or
# removed (import completion, save_geocache's create path, the delete task,
# trash/restore/purge, backup restore).
_platform_availability = TTLCache(ttl_seconds=300)


def _compute_available_platforms():
    from geocaches.models import Geocache

    has_gc = Geocache.objects.filter(gc_code__startswith="GC").exists()
    has_lc = Geocache.objects.filter(al_code__gt="").exists()
    has_oc = Geocache.objects.filter(oc_code__gt="").exists()
    has_other = Geocache.objects.exclude(gc_code="").exclude(gc_code__startswith="GC").exists()
    platforms = []
    if has_gc:
        platforms.append("gc")
    if has_lc:
        platforms.append("lc")
    if has_oc:
        platforms.append("oc")
    if has_other:
        platforms.append("other")
    return platforms


def get_available_platforms():
    """Cached list of platform codes ("gc"/"lc"/"oc"/"other") with at least
    one live cache — see invalidate_platform_availability()."""
    return _platform_availability.get_or_compute(_compute_available_platforms)


def invalidate_platform_availability():
    _platform_availability.invalidate()


# Filter-dialog option lists — the country list (DISTINCT iso_country_code
# over every live cache, 207-246 ms on 68k rows) and the attributes grouped
# by source, both embedded in the "Custom filter" dialogs (WP-04). Same
# 5-minute TTL safety net; explicit invalidation happens wherever
# invalidate_platform_availability() is called (import/delete/trash/restore/
# purge/backup restore) plus after enrichment completes (enrichment can set
# iso_country_code).
_country_list = TTLCache(ttl_seconds=300)
_attribute_groups = TTLCache(ttl_seconds=300)


def _compute_country_list():
    from geocaches.geo.countries import iso_to_name
    from geocaches.models import Geocache

    iso_codes = (
        Geocache.objects.exclude(iso_country_code="")
        .values_list("iso_country_code", flat=True)
        .distinct()
        .order_by("iso_country_code")
    )
    countries = [{"code": code, "name": iso_to_name(code)} for code in iso_codes]
    countries.sort(key=lambda c: c["name"])
    has_no_country = Geocache.objects.filter(iso_country_code="").exists()
    return {"countries": countries, "has_no_country": has_no_country}


def get_country_list():
    """Cached ``{"countries": [{"code", "name"}, …], "has_no_country": bool}``
    — see invalidate_filter_dialog_options()."""
    return _country_list.get_or_compute(_compute_country_list)


def _compute_attribute_groups():
    from itertools import groupby

    from geocaches.models import Attribute

    all_attributes = list(Attribute.objects.order_by("source", "name", "-is_positive"))
    attrs_by_source = {}
    for src, grp in groupby(all_attributes, key=lambda a: a.source):
        attrs_by_source[src] = list(grp)
    return attrs_by_source


def get_attribute_groups():
    """Cached ``{source: [Attribute, …]}`` — see invalidate_filter_dialog_options()."""
    return _attribute_groups.get_or_compute(_compute_attribute_groups)


def invalidate_filter_dialog_options():
    _country_list.invalidate()
    _attribute_groups.invalidate()


# My-usernames cache — the case-folded set of the user's own GC/OC usernames
# (every ``UserAccount.username``, plus the legacy ``gc_username`` preference
# as a fallback). Backs ``image_cache.state_for_cache()``, which is called
# once per image — 22 times on a cache-detail render, once per cache inside
# the image-cache bulk-fill task's per-cache loop (tens of thousands of times
# on a large collection). Same 5-minute TTL safety net as the caches above;
# explicit invalidation happens wherever the account set or the
# ``gc_username`` preference can change: accounts/views.py's
# invalidate_connected_platforms_cache() call sites (add/edit/delete/login/
# OAuth-callback/platform-keys), preferences/views/accounts.py:
# save_gc_username, and backup restore.
_my_usernames = TTLCache(ttl_seconds=300)


def _compute_my_usernames():
    from accounts.models import UserAccount
    from preferences.models import UserPreference

    names = {u.lower() for u in UserAccount.objects.values_list("username", flat=True) if u}
    fallback = (UserPreference.get("gc_username", "") or "").strip().lower()
    if fallback:
        names.add(fallback)
    return names


def get_my_usernames() -> set[str]:
    """Cached lower-cased set of the user's own GC/OC usernames — see
    invalidate_my_usernames_cache()."""
    return _my_usernames.get_or_compute(_compute_my_usernames)


def invalidate_my_usernames_cache():
    _my_usernames.invalidate()
