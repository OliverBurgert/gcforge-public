"""
Django settings for GCForge — public / packaged build.

Replaces gcforge/settings.py in the public repo.
Do NOT commit this file to the public repo — it is copied there by prepare_publish.sh.
"""

import configparser
import math as _math
import os
import secrets
from pathlib import Path

# APP_DIR: where bundled app files live (templates, static, migrations).
# The launcher sets GCFORGE_APP_DIR to sys._MEIPASS when running as a
# PyInstaller bundle, otherwise falls back to the project root.
if os.environ.get('GCFORGE_APP_DIR'):
    BASE_DIR = Path(os.environ['GCFORGE_APP_DIR'])
else:
    BASE_DIR = Path(__file__).resolve().parent.parent

# DATA_DIR: user-writable directory for database, logs, and backups.
# The launcher sets GCFORGE_DATA_DIR; default is ~/.gcforge.
DATA_DIR = Path(os.environ.get('GCFORGE_DATA_DIR', Path.home() / '.gcforge'))
DATA_DIR.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# Secret key — generated once, stored in user's home directory
# ---------------------------------------------------------------------------

def _load_or_create_secret_key() -> str:
    key_path = Path.home() / ".gcforge" / "secret_key"
    key_path.parent.mkdir(parents=True, exist_ok=True)
    if key_path.exists():
        return key_path.read_text(encoding="utf-8").strip()
    key = secrets.token_urlsafe(50)
    key_path.write_text(key, encoding="utf-8")
    return key


SECRET_KEY = _load_or_create_secret_key()

# ---------------------------------------------------------------------------
# Core settings
# ---------------------------------------------------------------------------

# Beta note: DEBUG=True so Django's runserver serves static files without
# needing collectstatic or WhiteNoise configuration. Acceptable for a
# localhost-only desktop app; revisit for v1.0.
DEBUG = True
ALLOWED_HOSTS = ['127.0.0.1', 'localhost']

# GC API is not available in the public build.
HAS_GC_API = False


def _resolve_database_path():
    """Resolve active database: env var > config file > default (DATA_DIR)."""
    env = os.environ.get("GCFORGE_DATABASE")
    if env:
        return Path(env)
    conf_path = BASE_DIR / "gcforge.conf"
    if conf_path.exists():
        cfg = configparser.ConfigParser()
        cfg.read(conf_path)
        db_path = cfg.get("database", "path", fallback="")
        if db_path:
            return Path(db_path)
    return DATA_DIR / "db.sqlite3"


# ---------------------------------------------------------------------------
# Application definition
# ---------------------------------------------------------------------------

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'geocaches',
    'accounts',
    'preferences',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'gcforge.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'preferences.context_processors.forging_scope',
            ],
        },
    },
]

WSGI_APPLICATION = 'gcforge.wsgi.application'

DATA_UPLOAD_MAX_NUMBER_FIELDS = 10000

# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': _resolve_database_path(),
        'OPTIONS': {
            'timeout': 30,
        },
    }
}

DATABASES_DIR = DATA_DIR / 'databases'

# ---------------------------------------------------------------------------
# SQLite custom functions + WAL mode
# ---------------------------------------------------------------------------

def _haversine_km(lat1, lon1, lat2, lon2):
    if None in (lat1, lon1, lat2, lon2):
        return None
    R = 6371.0
    phi1, phi2 = _math.radians(lat1), _math.radians(lat2)
    dphi = _math.radians(lat2 - lat1)
    dlambda = _math.radians(lon2 - lon1)
    a = _math.sin(dphi / 2) ** 2 + _math.cos(phi1) * _math.cos(phi2) * _math.sin(dlambda / 2) ** 2
    return 2 * R * _math.asin(_math.sqrt(min(a, 1.0)))


def _bearing_deg(lat1, lon1, lat2, lon2):
    if None in (lat1, lon1, lat2, lon2):
        return None
    phi1, phi2 = _math.radians(lat1), _math.radians(lat2)
    dlambda = _math.radians(lon2 - lon1)
    x = _math.sin(dlambda) * _math.cos(phi2)
    y = _math.cos(phi1) * _math.sin(phi2) - _math.sin(phi1) * _math.cos(phi2) * _math.cos(dlambda)
    return (_math.degrees(_math.atan2(x, y)) + 360) % 360


def _configure_sqlite(sender, connection, **kwargs):
    if connection.vendor == 'sqlite':
        connection.connection.create_function('haversine_km', 4, _haversine_km)
        connection.connection.create_function('bearing_deg', 4, _bearing_deg)
        connection.cursor().execute('PRAGMA journal_mode=WAL;')
        connection.cursor().execute('PRAGMA synchronous=NORMAL;')
        connection.cursor().execute('PRAGMA busy_timeout=30000;')


from django.db.backends.signals import connection_created
connection_created.connect(_configure_sqlite)

# ---------------------------------------------------------------------------
# Password validation
# ---------------------------------------------------------------------------

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

# ---------------------------------------------------------------------------
# Internationalisation
# ---------------------------------------------------------------------------

LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'Europe/Berlin'
USE_I18N = True
USE_TZ = True

# ---------------------------------------------------------------------------
# Static files
# ---------------------------------------------------------------------------

STATIC_URL = 'static/'
STATICFILES_DIRS = [BASE_DIR / "static"]

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

LOG_DIR = DATA_DIR / 'logs'
LOG_DIR.mkdir(exist_ok=True)

BACKUP_DIR = DATA_DIR / 'backups'
BACKUP_DIR.mkdir(exist_ok=True)
BACKUP_ROTATE_COUNT = 5

LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'gcforge': {
            'format': '{asctime}  {levelname:<7}  {message}',
            'style': '{',
            'datefmt': '%Y-%m-%d %H:%M:%S',
        },
    },
    'handlers': {
        'gcforge_file': {
            'class': 'gcforge.log_handlers.CopyTruncateRotatingFileHandler',
            'filename': LOG_DIR / 'gcforge.log',
            'maxBytes': 2 * 1024 * 1024,
            'backupCount': 4,
            'formatter': 'gcforge',
            'encoding': 'utf-8',
        },
    },
    'loggers': {
        'geocaches.enrichment': {'handlers': ['gcforge_file'], 'level': 'INFO', 'propagate': False},
        'geocaches.import':     {'handlers': ['gcforge_file'], 'level': 'INFO', 'propagate': False},
        'geocaches.backup':     {'handlers': ['gcforge_file'], 'level': 'INFO', 'propagate': False},
        'geocaches.sync':       {'handlers': ['gcforge_file'], 'level': 'INFO', 'propagate': False},
        'geocaches.update_task':{'handlers': ['gcforge_file'], 'level': 'INFO', 'propagate': False},
    },
}
