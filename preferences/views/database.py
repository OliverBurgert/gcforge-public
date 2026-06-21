import configparser
import os
import subprocess
import sys
from pathlib import Path

from django.conf import settings as django_settings
from django.http import FileResponse, Http404, HttpResponseNotAllowed
from django.shortcuts import redirect

from ._helpers import _redirect_tab


def _location_label(file_path: Path) -> str:
    """Return the parent directory of *file_path* relative to BASE_DIR (with trailing /).

    Falls back to the absolute parent path for files outside the project tree
    (e.g. a user-customised backup directory).
    """
    parent = file_path.parent
    try:
        rel = parent.resolve().relative_to(django_settings.BASE_DIR.resolve())
        rel_str = str(rel).replace("\\", "/")
        return f"{rel_str}/" if rel_str != "." else "(project root)/"
    except ValueError:
        return str(parent).replace("\\", "/") + "/"


def _list_available_databases(active_db_path, backup_dir):
    """Build the list of available databases from the default, DATABASES_DIR, and backup_dir."""
    default_db_path = django_settings.DATA_DIR / "db.sqlite3"
    databases_dir = django_settings.DATABASES_DIR
    active_resolved = active_db_path.resolve()
    result = []

    # 1. Default database
    result.append({
        "name": default_db_path.name,
        "location": _location_label(default_db_path),
        "path": str(default_db_path),
        "size": default_db_path.stat().st_size if default_db_path.exists() else 0,
        "exists": default_db_path.exists(),
        "active": default_db_path.resolve() == active_resolved,
        "is_backup": False,
    })

    # 2. databases/ folder
    if databases_dir.exists():
        for f in sorted(databases_dir.glob("*.sqlite3")):
            result.append({
                "name": f.name,
                "location": _location_label(f),
                "path": str(f),
                "size": f.stat().st_size,
                "exists": True,
                "active": f.resolve() == active_resolved,
                "is_backup": False,
            })

    # 3. backups/ folder
    if backup_dir.exists():
        for f in sorted(backup_dir.glob("*.sqlite3"), key=lambda p: p.stat().st_mtime, reverse=True):
            if f.resolve() == active_resolved and any(d["active"] for d in result):
                continue
            result.append({
                "name": f.name,
                "location": _location_label(f),
                "path": str(f),
                "size": f.stat().st_size,
                "exists": True,
                "active": f.resolve() == active_resolved,
                "is_backup": True,
            })

    return result


def _write_conf(db_path_str):
    """Write gcforge.conf with the given database path."""
    conf_path = django_settings.BASE_DIR / "gcforge.conf"
    cfg = configparser.ConfigParser()
    cfg["database"] = {"path": db_path_str}
    with open(conf_path, "w") as f:
        cfg.write(f)


def save_backup_prefs(request):
    from preferences.models import UserPreference
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    UserPreference.set("backup_auto_enabled", request.POST.get("backup_auto_enabled") == "1")
    raw_dir = request.POST.get("backup_dir", "").strip()
    UserPreference.set("backup_dir", raw_dir)
    try:
        keep = max(1, min(50, int(request.POST.get("backup_rotate_count", 5))))
    except (ValueError, TypeError):
        keep = 5
    UserPreference.set("backup_rotate_count", keep)
    return _redirect_tab("database")


def vacuum_now(request):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    from preferences import backup as _backup
    try:
        result = _backup.do_vacuum(reason="manual")
        freed_mb = result["freed"] / 1024 / 1024
        after_mb = result["size_after"] / 1024 / 1024
        request.session["backup_msg"] = {
            "ok": True,
            "text": (
                f"Vacuum complete in {result['elapsed_s']:.1f} s — "
                f"freed {freed_mb:.1f} MB, database now {after_mb:.1f} MB."
            ),
        }
    except Exception as exc:
        request.session["backup_msg"] = {"ok": False, "text": f"Vacuum failed: {exc}"}
    return _redirect_tab("database")


def backup_now(request):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    import logging as _logging
    from preferences import backup as _backup
    from datetime import datetime as _dt
    _bklog = _logging.getLogger("geocaches.backup")
    raw_dir  = request.POST.get("manual_backup_dir",  "").strip()
    raw_name = request.POST.get("manual_backup_name", "").strip()
    dest_dir = Path(raw_dir) if raw_dir else _backup.get_backup_dir()
    if not raw_name:
        raw_name = "gcforge_backup_" + _dt.now().strftime("%Y-%m-%d_%H%M%S")
    if not raw_name.endswith(".sqlite3"):
        raw_name += ".sqlite3"
    dest = dest_dir / raw_name
    try:
        _bklog.info("--- Manual backup start: %s", dest)
        _backup.create_backup(dest)
        size_mb = dest.stat().st_size / 1024 / 1024
        _bklog.info("--- Manual backup done: %s (%.1f MB)", dest.name, size_mb)
        request.session["backup_msg"] = {"ok": True, "text": f"Backup saved: {dest}"}
    except Exception as exc:
        _bklog.error("Manual backup failed: %s", exc)
        request.session["backup_msg"] = {"ok": False, "text": f"Backup failed: {exc}"}
    return _redirect_tab("database")


def backup_download(request, filename):
    """Serve a backup file from the backup directory as a download."""
    from preferences import backup as _backup
    backup_dir = _backup.get_backup_dir()
    safe_name = Path(filename).name  # strip any path components
    path = backup_dir / safe_name
    if not path.exists() or not path.is_file():
        raise Http404
    return FileResponse(open(path, "rb"), as_attachment=True, filename=safe_name)


def backup_delete(request):
    """Delete a backup file from the backup directory."""
    if request.method != "POST":
        return _redirect_tab("database")

    from preferences import backup as _backup
    backup_name = request.POST.get("backup_name", "").strip()
    if not backup_name:
        return _redirect_tab("database")

    backup_dir = _backup.get_backup_dir()
    safe_name = Path(backup_name).name
    path = backup_dir / safe_name
    try:
        if not path.exists():
            raise FileNotFoundError(f"File not found: {safe_name}")
        path.unlink()
        request.session["backup_msg"] = {"ok": True, "text": f"Deleted: {safe_name}"}
    except Exception as exc:
        request.session["backup_msg"] = {"ok": False, "text": f"Delete failed: {exc}"}

    return _redirect_tab("database")


def backup_restore(request):
    """Restore database from an existing backup or an uploaded file."""
    import tempfile
    if request.method != "POST":
        return redirect("preferences:settings")

    from preferences import backup as _backup

    try:
        if "restore_file" in request.FILES:
            # Restore from uploaded file — write to a temp file first
            upload = request.FILES["restore_file"]
            with tempfile.NamedTemporaryFile(suffix=".sqlite3", delete=False) as tmp:
                for chunk in upload.chunks():
                    tmp.write(chunk)
                tmp_path = Path(tmp.name)
            try:
                _backup.restore_from_path(tmp_path)
            finally:
                tmp_path.unlink(missing_ok=True)
        else:
            backup_name = request.POST.get("backup_name", "").strip()
            if not backup_name:
                return redirect("preferences:settings")
            backup_dir = _backup.get_backup_dir()
            safe_name = Path(backup_name).name
            path = backup_dir / safe_name
            if not path.exists():
                raise FileNotFoundError(f"Backup not found: {safe_name}")
            _backup.restore_from_path(path)

        request.session["backup_msg"] = {
            "ok": True,
            "text": "Database restored successfully. The page has been reloaded from the restored database.",
        }
    except Exception as exc:
        request.session["backup_msg"] = {"ok": False, "text": f"Restore failed: {exc}"}

    return _redirect_tab("database")


def switch_database(request):
    """Switch the active database by writing gcforge.conf."""
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    db_path = request.POST.get("db_path", "").strip()
    if not db_path:
        request.session["db_switch_msg"] = {"ok": False, "text": "No database path provided."}
        return _redirect_tab("database")

    target = Path(db_path)
    if not target.exists():
        request.session["db_switch_msg"] = {"ok": False, "text": f"Database file not found: {db_path}"}
        return _redirect_tab("database")

    # Check if it's the default — if so, remove the conf file to revert to default
    default_path = django_settings.DATA_DIR / "db.sqlite3"
    conf_path = django_settings.BASE_DIR / "gcforge.conf"
    if target.resolve() == default_path.resolve():
        if conf_path.exists():
            conf_path.unlink()
    else:
        _write_conf(str(target.resolve()))

    request.session["db_switch_msg"] = {
        "ok": True,
        "text": f"Database switched to {target.name}. Please restart the server for the change to take effect.",
    }
    return _redirect_tab("database")


def create_database(request):
    """Create a new empty database in databases/, run migrations, and switch to it."""
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    raw_name = request.POST.get("db_name", "").strip()
    if not raw_name:
        request.session["db_switch_msg"] = {"ok": False, "text": "Please enter a database name."}
        return _redirect_tab("database")

    # Sanitize: allow only alphanumeric, dash, underscore
    safe_name = "".join(c for c in raw_name if c.isalnum() or c in "-_")
    if not safe_name:
        request.session["db_switch_msg"] = {"ok": False, "text": "Invalid database name."}
        return _redirect_tab("database")

    if not safe_name.endswith(".sqlite3"):
        safe_name += ".sqlite3"

    databases_dir = django_settings.DATABASES_DIR
    databases_dir.mkdir(parents=True, exist_ok=True)
    new_path = databases_dir / safe_name

    if new_path.exists():
        request.session["db_switch_msg"] = {"ok": False, "text": f"Database {safe_name} already exists."}
        return _redirect_tab("database")

    try:
        # Write conf pointing to new database
        _write_conf(str(new_path.resolve()))

        # Run migrations via subprocess with the env var override
        env = os.environ.copy()
        env["GCFORGE_DATABASE"] = str(new_path.resolve())
        result = subprocess.run(
            [sys.executable, "manage.py", "migrate", "--run-syncdb"],
            cwd=str(django_settings.BASE_DIR),
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode != 0:
            # Clean up on failure
            if new_path.exists():
                new_path.unlink()
            conf_path = django_settings.BASE_DIR / "gcforge.conf"
            if conf_path.exists():
                conf_path.unlink()
            request.session["db_switch_msg"] = {
                "ok": False,
                "text": f"Migration failed: {result.stderr[:500]}",
            }
            return _redirect_tab("database")

        request.session["db_switch_msg"] = {
            "ok": True,
            "text": f"Database {safe_name} created and set as active. Please restart the server.",
        }
    except Exception as exc:
        # Clean up on failure
        if new_path.exists():
            new_path.unlink()
        conf_path = django_settings.BASE_DIR / "gcforge.conf"
        if conf_path.exists():
            conf_path.unlink()
        request.session["db_switch_msg"] = {"ok": False, "text": f"Failed to create database: {exc}"}

    return _redirect_tab("database")
