# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for GCForge.

Bundles the Django app, templates, static files, and migrations into a
--onedir distribution. Run with:
  pyinstaller gcforge.spec --noconfirm
"""

from pathlib import Path
from PyInstaller.utils.hooks import collect_all, collect_submodules

ROOT = Path(SPECPATH)

# ---------------------------------------------------------------------------
# Data files: templates, static assets, migrations
# ---------------------------------------------------------------------------

datas = [
    (str(ROOT / 'templates'),                  'templates'),
    (str(ROOT / 'static'),                     'static'),
    (str(ROOT / 'geocaches'  / 'migrations'),  'geocaches/migrations'),
    (str(ROOT / 'accounts'   / 'migrations'),  'accounts/migrations'),
    (str(ROOT / 'preferences'/ 'migrations'),  'preferences/migrations'),
]

# Collect data files from packages that ship their own datasets.
for _pkg in ('pycountry', 'timezonefinder'):
    _d, _b, _h = collect_all(_pkg)
    datas += _d

# ---------------------------------------------------------------------------
# Hidden imports
# ---------------------------------------------------------------------------

hiddenimports = [
    # Django template loaders
    'django.template.loaders.filesystem',
    'django.template.loaders.app_directories',
    # Django DB backend
    'django.db.backends.sqlite3',
    # Django admin template tags
    'django.contrib.admin.templatetags.log',
    'django.contrib.admin.templatetags.admin_list',
    'django.contrib.admin.templatetags.admin_modify',
    'django.contrib.admin.templatetags.admin_urls',
    # GCForge custom template tags
    'geocaches.templatetags.icon_tags',
    # stdlib modules that analysis sometimes misses
    '_strptime',
    'calendar',
    'email.mime.text',
    'email.mime.multipart',
]

# Django's own migrations (auth, contenttypes, sessions, admin)
for _app in (
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
):
    hiddenimports += collect_submodules(f'{_app}.migrations')

# Project app migrations
for _app in ('geocaches', 'accounts', 'preferences'):
    hiddenimports += collect_submodules(f'{_app}.migrations')

# pycountry / timezonefinder hidden imports collected above
for _pkg in ('pycountry', 'timezonefinder'):
    _, _, _h = collect_all(_pkg)
    hiddenimports += _h

# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------

a = Analysis(
    ['gcforge_launcher.py'],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tkinter', 'matplotlib', 'IPython', 'notebook'],
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='GCForge',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,   # keep console visible in beta so errors are readable
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='GCForge',
)
