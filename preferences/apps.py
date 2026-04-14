import os
import sys

from django.apps import AppConfig


class PreferencesConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "preferences"

    def ready(self):
        from django.db.models.signals import post_migrate
        post_migrate.connect(_seed_builtin_presets, sender=self)

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


_EDITABLE_PRESETS = ("Standard", "Personal", "Compact")
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
