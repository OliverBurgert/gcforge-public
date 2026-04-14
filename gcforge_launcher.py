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

    # Run migrations on first launch (or after an update).
    from django.core.management import call_command as mgmt
    print('Checking database migrations...')
    mgmt('migrate', '--run-syncdb', verbosity=0)

    # Create default admin account on first launch.
    from django.contrib.auth import get_user_model
    User = get_user_model()
    if not User.objects.filter(is_superuser=True).exists():
        User.objects.create_superuser('admin', '', 'admin')
        print('Created default account  username: admin  password: admin')

    port = _find_free_port()
    url = f'http://127.0.0.1:{port}/'

    server_thread = threading.Thread(target=_run_server, args=(port,), daemon=True)
    server_thread.start()

    print(f'Starting GCForge at {url} ...')

    if _wait_for_server(port):
        webbrowser.open(url)
        print('Press Ctrl+C to quit.')
    else:
        print('ERROR: Server did not start within 60 seconds.', file=sys.stderr)
        sys.exit(1)

    server_thread.join()


if __name__ == '__main__':
    main()
