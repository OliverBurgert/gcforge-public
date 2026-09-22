"""Profile management views.

Renaming an *inactive* profile (P1) and the in-session switch-with-restart
flow (P3) — see docs/multi-profile-plan.md. Creating a profile reuses
database.create_database() with set_active omitted; the low-level
database.switch_database() (backups included) stays manual-restart-only.
"""

from pathlib import Path

from django.conf import settings as django_settings
from django.contrib.staticfiles import finders
from django.http import HttpResponseNotAllowed
from django.shortcuts import render
from django.utils.translation import gettext as _

from gcforge import profiles as gcforge_profiles
from gcforge import restart as gcforge_restart
from geocaches.tasks import active_tasks, cancel_task
from preferences.context_processors import invalidate_profile_cache

from ._helpers import _redirect_tab


def rename_profile(request):
    """Rename an inactive profile's .sqlite3 file. POST: path, new_name."""
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    raw_path = request.POST.get("path", "").strip()
    new_name = request.POST.get("new_name", "").strip()
    if not raw_path or not new_name:
        request.session["db_switch_msg"] = {"ok": False, "text": _("Missing profile or new name.")}
        return _redirect_tab("database")

    try:
        renamed = gcforge_profiles.rename_profile(
            Path(raw_path), new_name, django_settings.BASE_DIR, django_settings.DATA_DIR,
        )
    except gcforge_profiles.UnknownProfileError:
        text = _("Not a known profile.")
        request.session["db_switch_msg"] = {"ok": False, "text": text}
        return _redirect_tab("database")
    except gcforge_profiles.ActiveProfileError:
        text = _("Cannot rename the profile that is currently active. Switch away from it first.")
        request.session["db_switch_msg"] = {"ok": False, "text": text}
        return _redirect_tab("database")
    except gcforge_profiles.InvalidNameError:
        text = _("Invalid profile name.")
        request.session["db_switch_msg"] = {"ok": False, "text": text}
        return _redirect_tab("database")
    except gcforge_profiles.NameCollisionError as exc:
        text = _("A profile named %(name)s already exists.") % {"name": exc.name}
        request.session["db_switch_msg"] = {"ok": False, "text": text}
        return _redirect_tab("database")

    invalidate_profile_cache()
    request.session["db_switch_msg"] = {
        "ok": True,
        "text": _("Profile renamed to %(name)s.") % {"name": gcforge_profiles.profile_name(renamed)},
    }
    return _redirect_tab("database")


def profile_picker(request):
    """In-session picker — the Profiles card already lives on the Database
    tab, so this named route (linked from the navbar badge) simply lands
    there. Kept as its own URL name for a stable link target independent of
    where the Profiles card ends up living.
    """
    return _redirect_tab("database")


def switch_profile(request):
    """Switch the active profile, auto-restarting when possible (P3).

    POST: db_path (required). confirm=1 proceeds even with active background
    tasks, cancelling them first — see the warn-and-confirm design in
    docs/multi-profile-plan.md §6. Falls back to the P1 manual-restart
    message when no automatic restart mechanism is available for this
    process (gcforge.restart.can_auto_restart()).
    """
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    db_path = request.POST.get("db_path", "").strip()
    if not db_path:
        request.session["db_switch_msg"] = {"ok": False, "text": _("No database path provided.")}
        return _redirect_tab("database")

    target = Path(db_path)
    known = {p.path.resolve() for p in gcforge_profiles.list_profiles(django_settings.DATA_DIR)}
    if target.resolve() not in known:
        request.session["db_switch_msg"] = {
            "ok": False,
            "text": _("Database file not found: %(path)s") % {"path": db_path},
        }
        return _redirect_tab("database")

    confirmed = request.POST.get("confirm") == "1"
    tasks = active_tasks()
    if tasks and not confirmed:
        return render(request, "preferences/switch_confirm.html", {"tasks": tasks, "db_path": db_path})

    if confirmed:
        for t in tasks:
            cancel_task(t["id"])

    default_path = gcforge_profiles.default_profile_path(django_settings.DATA_DIR)
    if target.resolve() == default_path.resolve():
        gcforge_profiles.clear_active_db(django_settings.BASE_DIR, django_settings.DATA_DIR)
    else:
        gcforge_profiles.write_active_db(target, django_settings.BASE_DIR, django_settings.DATA_DIR)

    can_restart, _mode = gcforge_restart.can_auto_restart()
    if can_restart:
        gcforge_restart.request_restart()
        js_path = finders.find("js/profile-switch.js")
        js_source = Path(js_path).read_text(encoding="utf-8") if js_path else ""
        return render(request, "preferences/switching_profile.html", {
            "profile_name": gcforge_profiles.active_profile_display_name(target, django_settings.DATA_DIR),
            "profile_switch_js": js_source,
        })

    request.session["db_switch_msg"] = {
        "ok": True,
        "text": _("Database switched to %(name)s. Please restart the server for the change to take effect.")
        % {"name": target.name},
    }
    return _redirect_tab("database")
