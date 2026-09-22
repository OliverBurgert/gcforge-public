import os
import sys

from django.apps import AppConfig


class PreferencesConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "preferences"

    def ready(self):
        from django.db.models.signals import post_migrate
        post_migrate.connect(_seed_builtin_presets, sender=self)

        from django.conf import settings
        if getattr(settings, "TASKS_RUN_SYNC", False):
            _patch_test_preference_cache_reset()

        # Trigger daily auto-backup on server startup only.
        # RUN_MAIN='true' identifies the actual worker under the autoreloader.
        # Without autoreload (--noreload) RUN_MAIN is unset.
        if "runserver" in sys.argv and (
            os.environ.get("RUN_MAIN") == "true" or "--noreload" in sys.argv
        ):
            import threading

            def _startup_maintenance():
                import time
                time.sleep(3)  # let the server finish starting up
                try:
                    from preferences.backup import do_daily_backup, should_vacuum, do_vacuum
                    do_daily_backup()
                except Exception:
                    pass
                try:
                    run, info = should_vacuum()
                    if run:
                        do_vacuum(reason="auto")
                    else:
                        import logging
                        _log = logging.getLogger("geocaches.backup")
                        _log.info(
                            "Vacuum skipped: %.1f MB free (%.0f%%) — below threshold",
                            info["free_bytes"] / 1024 / 1024,
                            info["fragmentation_pct"],
                        )
                except Exception:
                    pass

            threading.Thread(target=_startup_maintenance, daemon=True).start()


def _patch_test_preference_cache_reset():
    """Clear this package's process-level caches after every test.

    Each cache (UserPreference's preference dict, geocaches.runtime_cache's
    platform-availability/country-list/attribute-group/my-usernames caches,
    and context_processors' connected-platforms and profile-info caches) is
    invalidated on every write that goes through
    the ORM/views, so a value written and read back within one test is always
    correct. But TestCase wraps each test in a transaction that gets rolled
    back — which fires no ORM signal — and some tests write rows directly
    (e.g. Geocache.objects.create(), bypassing save_geocache's invalidation
    hook), so a value cached during one test could otherwise leak into the
    next. SimpleTestCase's _post_teardown() runs after that rollback (or
    after TransactionTestCase's flush) for every Django test case, so
    patching it once here resets all of them for every test file without
    editing any of them.  The distance cache's "recompute in flight" set rides
    along for the same reason — it is process state, not database state.
    """
    from django.test import SimpleTestCase

    if getattr(SimpleTestCase, "_gcf_pref_cache_patched", False):
        return

    original_post_teardown = SimpleTestCase._post_teardown

    def _post_teardown(self):
        original_post_teardown(self)
        from preferences.models import UserPreference
        from preferences.context_processors import (
            invalidate_connected_platforms_cache, invalidate_profile_cache,
        )
        from geocaches.geo.distance_cache import reset_in_flight
        from geocaches.runtime_cache import (
            invalidate_filter_dialog_options, invalidate_my_usernames_cache, invalidate_platform_availability,
        )
        UserPreference.invalidate_cache()
        invalidate_platform_availability()
        invalidate_filter_dialog_options()
        invalidate_connected_platforms_cache()
        invalidate_my_usernames_cache()
        invalidate_profile_cache()
        reset_in_flight()

    SimpleTestCase._post_teardown = _post_teardown
    SimpleTestCase._gcf_pref_cache_patched = True


_EDITABLE_PRESETS = ("Standard", "Personal", "Compact", "ALC")
_AUTO_UPDATED_PRESETS = ("Full",)


def _seed_builtin_presets(sender, **kwargs):
    """Seed built-in presets after migrations.

    Editable presets (Standard, Personal, Compact): get_or_create — user changes preserved.
    Auto-updated presets (Full): update_or_create — always reflects current AVAILABLE_COLUMNS.
    """
    from preferences.columns import BUILTIN_PRESETS
    from preferences.models import ColumnPreset
    for name in _EDITABLE_PRESETS:
        if name in BUILTIN_PRESETS:
            ColumnPreset.objects.get_or_create(
                name=name,
                defaults={"columns": BUILTIN_PRESETS[name], "is_builtin": True},
            )
    for name in _AUTO_UPDATED_PRESETS:
        if name in BUILTIN_PRESETS:
            ColumnPreset.objects.update_or_create(
                name=name,
                defaults={"columns": BUILTIN_PRESETS[name], "is_builtin": True},
            )

    # Ensure all built-in presets include mandatory columns (e.g. tags).
    # Editable presets use get_or_create, so existing rows won't pick up
    # new columns added to BUILTIN_PRESETS — patch them here.
    _MANDATORY = ["tags"]
    for preset in ColumnPreset.objects.filter(is_builtin=True):
        cols = list(preset.columns)
        changed = False
        for col in _MANDATORY:
            if col not in cols:
                # Insert before "flags" if present, else append
                try:
                    idx = cols.index("flags")
                except ValueError:
                    idx = len(cols)
                cols.insert(idx, col)
                changed = True
        if changed:
            preset.columns = cols
            preset.save(update_fields=["columns"])
