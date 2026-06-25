#!/usr/bin/env python3
"""
GCForge launcher.

Starts the Django development server on a free local port, runs migrations
on first launch, creates a default admin account if none exists, then opens
the app in the default browser.

Works both as a plain Python script (development) and as a PyInstaller bundle.
"""

import os
import socket
import sys
import threading
import time
import traceback
import webbrowser
from pathlib import Path


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(('127.0.0.1', 0))
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        return s.getsockname()[1]


def _wait_for_server(port: int, timeout: int = 60) -> bool:
    import urllib.error
    import urllib.request
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            urllib.request.urlopen(f'http://127.0.0.1:{port}/', timeout=1)
            return True
        except (urllib.error.URLError, OSError):
            time.sleep(0.4)
    return False


def _run_server(port: int) -> None:
    from django.core.management import call_command
    call_command('runserver', f'127.0.0.1:{port}', '--noreload')


def _pending_migrations() -> list:
    """Return the list of unapplied migrations against the live database."""
    from django.db import connection
    from django.db.migrations.executor import MigrationExecutor
    executor = MigrationExecutor(connection)
    targets = executor.loader.graph.leaf_nodes()
    return executor.migration_plan(targets)


def _backup_database(db_path: Path, backup_dir: Path, keep: int = 5) -> Path | None:
    """Copy `db_path` into `backup_dir` with a timestamped name, prune to last `keep`.

    Returns the backup path, or None if `db_path` does not exist (first launch).
    """
    import shutil
    if not db_path.exists():
        return None
    backup_dir.mkdir(parents=True, exist_ok=True)
    timestamp = time.strftime('%Y-%m-%d_%H%M%S')
    backup_path = backup_dir / f'{db_path.name}.pre-migrate-{timestamp}.bak'
    shutil.copy2(db_path, backup_path)
    existing = sorted(
        backup_dir.glob(f'{db_path.name}.pre-migrate-*.bak'),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for old in existing[keep:]:
        try:
            old.unlink()
        except OSError:
            pass
    return backup_path


def main() -> None:
    # When running as a PyInstaller bundle, sys._MEIPASS is the extracted
    # directory containing all bundled files (templates, static, migrations).
    if getattr(sys, 'frozen', False):
        app_dir = sys._MEIPASS
    else:
        app_dir = str(Path(__file__).resolve().parent)

    # User-writable data directory: database, logs, backups, secret key.
    data_dir = Path.home() / '.gcforge'
    data_dir.mkdir(parents=True, exist_ok=True)

    os.environ.setdefault('GCFORGE_APP_DIR', app_dir)
    os.environ.setdefault('GCFORGE_DATA_DIR', str(data_dir))
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'gcforge.settings')

    # Add app_dir to sys.path so Django can find gcforge/settings.py etc.
    if app_dir not in sys.path:
        sys.path.insert(0, app_dir)

    import django
    django.setup()

    # Check for pending migrations; back up the SQLite DB before applying any.
    # Skipping migrate when nothing is pending avoids copying the (potentially
    # multi-GB) database on every routine launch.
    from django.core.management import call_command as mgmt
    from django.db import connection

    print('Checking database migrations...')
    pending = _pending_migrations()
    if not pending:
        print('Database is up to date.')
    else:
        db_path = Path(connection.settings_dict['NAME'])
        backup_dir = data_dir / 'backups'
        if db_path.exists():
            size_mb = db_path.stat().st_size / (1024 * 1024)
            print(
                f'Found {len(pending)} pending migration(s). '
                f'Backing up database ({size_mb:.0f} MB) before applying...'
            )
            backup_path = _backup_database(db_path, backup_dir)
            print(f'Backup created: {backup_path}')
        else:
            backup_path = None
            print(f'First-time setup: applying {len(pending)} migration(s)...')
        try:
            mgmt('migrate', '--run-syncdb', verbosity=1)
        except Exception:
            print('ERROR: Migration failed. Details below:', file=sys.stderr)
            traceback.print_exc()
            if backup_path:
                print(
                    f'\nYour previous database has been backed up to:\n  {backup_path}',
                    file=sys.stderr,
                )
                print(
                    f'To restore, copy that file over:\n  {db_path}',
                    file=sys.stderr,
                )
            sys.exit(1)

    # Create default admin account on first launch.
    from django.contrib.auth import get_user_model
    User = get_user_model()
    if not User.objects.filter(is_superuser=True).exists():
        User.objects.create_superuser('admin', '', 'admin')
        print('Created default account  username: admin  password: admin')

    # Honour an explicit port (used by CI smoke tests / advanced users); otherwise
    # pick a random free one.
    port_env = os.environ.get('GCFORGE_PORT', '')
    port = int(port_env) if port_env.isdigit() else _find_free_port()
    url = f'http://127.0.0.1:{port}/'

    server_thread = threading.Thread(target=_run_server, args=(port,), daemon=True)
    server_thread.start()

    # flush=True so the URL is visible immediately even when stdout is piped
    # (a frozen app block-buffers stdout otherwise).
    print(f'Starting GCForge at {url} ...', flush=True)

    if _wait_for_server(port):
        webbrowser.open(url)
        print('Press Ctrl+C to quit.', flush=True)
    else:
        print('ERROR: Server did not start within 60 seconds.', file=sys.stderr, flush=True)
        sys.exit(1)

    server_thread.join()


if __name__ == '__main__':
    main()
