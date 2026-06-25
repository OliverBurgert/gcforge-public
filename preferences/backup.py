"""
Database backup / restore utilities.

Uses SQLite's built-in backup API for safe hot backups — no lock contention
with a running Django server.  Restore uses file copy after closing all
Django DB connections.
"""
import logging
import shutil
import sqlite3
from datetime import date, datetime
from pathlib import Path

_log = logging.getLogger("geocaches.backup")


def get_db_path() -> Path:
    from django.conf import settings
    return Path(settings.DATABASES["default"]["NAME"])


def get_backup_dir() -> Path:
    from django.conf import settings
    from preferences.models import UserPreference
    custom = UserPreference.get("backup_dir", "")
    if custom:
        p = Path(custom)
        p.mkdir(parents=True, exist_ok=True)
        return p
    return Path(settings.BACKUP_DIR)


def get_rotate_count() -> int:
    from django.conf import settings
    from preferences.models import UserPreference
    try:
        return int(UserPreference.get("backup_rotate_count", settings.BACKUP_ROTATE_COUNT))
    except (ValueError, TypeError):
        return settings.BACKUP_ROTATE_COUNT


def create_backup(dest_path: Path) -> None:
    """Hot backup using SQLite's built-in backup API."""
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    src = sqlite3.connect(str(get_db_path()))
    try:
        dst = sqlite3.connect(str(dest_path))
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()


AUTO_ROTATE_PREFIX = "auto_rotate_"


def is_auto_rotate(filename: str) -> bool:
    return Path(filename).name.startswith(AUTO_ROTATE_PREFIX)


def list_backups(backup_dir: Path) -> list:
    """Return all *.sqlite3 files in the backup directory, newest first."""
    if not backup_dir.exists():
        return []
    files = sorted(
        backup_dir.glob("*.sqlite3"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    result = []
    for f in files:
        st = f.stat()
        result.append({
            "path":        f,
            "name":        f.name,
            "size":        st.st_size,
            "mtime":       datetime.fromtimestamp(st.st_mtime),
            "auto_rotate": is_auto_rotate(f.name),
        })
    return result


def do_daily_backup() -> Path | None:
    """Create today's auto-backup if not already done. Rotate old auto backups."""
    from preferences.models import UserPreference
    if not UserPreference.get("backup_auto_enabled", True):
        return None

    backup_dir = get_backup_dir()
    backup_dir.mkdir(parents=True, exist_ok=True)

    today = date.today().isoformat()
    dest = backup_dir / f"{AUTO_ROTATE_PREFIX}gcforge_backup_{today}.sqlite3"
    if dest.exists():
        return dest

    _log.info("--- Auto backup start: %s", dest)
    create_backup(dest)
    size_mb = dest.stat().st_size / 1024 / 1024
    _log.info("--- Auto backup done: %s (%.1f MB)", dest.name, size_mb)
    _rotate(backup_dir, get_rotate_count())
    return dest


def _rotate(backup_dir: Path, keep: int) -> None:
    """Delete oldest auto-rotate backups beyond keep count. Manual backups are never touched."""
    files = sorted(
        backup_dir.glob(f"{AUTO_ROTATE_PREFIX}*.sqlite3"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for old in files[keep:]:
        try:
            old.unlink()
        except OSError:
            pass


def fragmentation_info() -> dict:
    """
    Return fragmentation statistics for the live database.

    Returns a dict with:
      page_count, freelist_count, page_size,
      total_bytes, free_bytes, fragmentation_pct
    """
    db_path = get_db_path()
    conn = sqlite3.connect(str(db_path))
    try:
        cur = conn.cursor()
        page_count   = cur.execute("PRAGMA page_count").fetchone()[0]
        freelist     = cur.execute("PRAGMA freelist_count").fetchone()[0]
        page_size    = cur.execute("PRAGMA page_size").fetchone()[0]
    finally:
        conn.close()

    total_bytes = page_count * page_size
    free_bytes  = freelist * page_size
    pct = (freelist / page_count * 100) if page_count else 0.0
    return {
        "page_count":        page_count,
        "freelist_count":    freelist,
        "page_size":         page_size,
        "total_bytes":       total_bytes,
        "free_bytes":        free_bytes,
        "fragmentation_pct": pct,
    }


def do_vacuum(reason: str = "manual") -> dict:
    """
    Run VACUUM on the live database.

    Needs an exclusive lock for the full duration — other DB operations will
    queue behind it.  Atomic: safe to interrupt (original intact until rename).

    Returns a dict with before/after sizes and freed space.
    """
    import time
    from django.db import connection

    info_before = fragmentation_info()
    size_before = info_before["total_bytes"]
    free_before = info_before["free_bytes"]
    pct_before  = info_before["fragmentation_pct"]

    _log.info(
        "--- Vacuum start (%s): DB %.1f MB, %.1f MB free (%.0f%% fragmented)",
        reason,
        size_before / 1024 / 1024,
        free_before / 1024 / 1024,
        pct_before,
    )

    t0 = time.monotonic()
    connection.cursor().execute("VACUUM")
    elapsed = time.monotonic() - t0

    info_after = fragmentation_info()
    size_after = info_after["total_bytes"]
    freed      = size_before - size_after

    _log.info(
        "--- Vacuum done (%s): %.1f MB -> %.1f MB, freed %.1f MB in %.1f s",
        reason,
        size_before / 1024 / 1024,
        size_after  / 1024 / 1024,
        freed       / 1024 / 1024,
        elapsed,
    )

    return {
        "size_before": size_before,
        "size_after":  size_after,
        "freed":       freed,
        "elapsed_s":   elapsed,
    }


def should_vacuum(min_free_mb: float = 50.0, min_pct: float = 10.0) -> tuple[bool, dict]:
    """
    Return (should_run, info) based on fragmentation thresholds.

    Triggers if free space exceeds both min_free_mb AND min_pct of total.
    """
    info = fragmentation_info()
    free_mb = info["free_bytes"] / 1024 / 1024
    run = info["fragmentation_pct"] >= min_pct and free_mb >= min_free_mb
    return run, info


def restore_from_path(backup_path: Path) -> None:
    """
    Replace the live database with a backup file.
    Closes all Django DB connections first so the copy succeeds on Windows.
    Creates a pre-restore safety backup before overwriting.
    """
    from django.db import connections
    from django.conf import settings

    db_path = get_db_path()
    backup_dir = Path(settings.BACKUP_DIR)
    backup_dir.mkdir(parents=True, exist_ok=True)

    # Safety backup of current state
    ts = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    safety = backup_dir / f"gcforge_backup_pre_restore_{ts}.sqlite3"
    _log.info("--- Restore start: from %s — creating safety backup %s", backup_path.name, safety.name)
    create_backup(safety)

    # Close all connections before overwriting the file
    connections.close_all()

    # Remove WAL / SHM side-cars if present
    for suf in ("-wal", "-shm"):
        side = Path(str(db_path) + suf)
        if side.exists():
            side.unlink(missing_ok=True)

    shutil.copy2(str(backup_path), str(db_path))
    _log.info("--- Restore done: database replaced with %s", backup_path.name)
