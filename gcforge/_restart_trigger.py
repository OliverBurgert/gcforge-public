"""Docstring-only module — its mtime is the dev-autoreloader restart trigger.

Imported at the bottom of gcforge/settings.py (and scripts/settings_public.py)
so it's guaranteed to be in sys.modules and therefore watched by Django's
StatReloader (iter_modules_and_files()). gcforge.restart.trigger_autoreload()
touches this file's mtime — content never changes — to make the reloader
re-exec the server child on its own, without a second process fighting the
watcher parent for the same port. See docs/multi-profile-plan.md §6.

If this mechanism ever misbehaves, deleting the import in settings.py
degrades gracefully back to the manual "please restart the server" flow.
"""
