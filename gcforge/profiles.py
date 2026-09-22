"""Profile resolution — the single source of truth for "which .sqlite3 is active".

Stdlib-only by design: this module must be importable and fully usable *before*
`django.setup()` runs (the pre-Django profile picker needs it — see
docs/multi-profile-plan.md §5 and blocker B5). Do not add a Django import at
module scope; if a caller needs Django state (e.g. the actually-open DB
connection), it must pass that in as a plain value.

One profile = one SQLite database file. `gcforge/settings.py` and
`scripts/settings_public.py` both delegate their `_resolve_database_path()` to
`resolve_database_path()` here so there is exactly one implementation of the
env > conf > default precedence chain.
"""

from __future__ import annotations

import configparser
import os
from dataclasses import dataclass
from pathlib import Path

# The one-and-only extension profiles are recognized by. `list_profiles()`
# globs on this, which is what excludes -wal/-shm sidecars and backup files
# living elsewhere for free.
PROFILE_SUFFIX = ".sqlite3"

# Characters allowed in a profile name (used by both create and rename).
_SAFE_NAME_CHARS = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"
)


@dataclass
class Profile:
    name: str
    path: Path
    size: int
    mtime: float
    is_default: bool

    @property
    def display_name(self) -> str:
        """Display name — reads as "Default" for the pre-multi-profile db.sqlite3
        until it's renamed (R2), else the filename stem."""
        if self.is_default and self.name == "db":
            return "Default"
        return self.name


def data_dir_from_env(env: dict | None = None) -> Path:
    """Derive DATA_DIR from the environment alone, the way gcforge_launcher.py
    does before django.setup() runs. Used by the pre-Django picker (P2); not
    needed once Django settings are loaded (settings.py already has DATA_DIR).
    """
    env = os.environ if env is None else env
    return Path(env.get("GCFORGE_DATA_DIR", str(Path.home() / ".gcforge")))


def conf_path(data_dir: Path) -> Path:
    return Path(data_dir) / "gcforge.conf"


def legacy_conf_path(base_dir: Path) -> Path:
    return Path(base_dir) / "gcforge.conf"


def default_profile_path(data_dir: Path) -> Path:
    return Path(data_dir) / "db.sqlite3"


def profile_name(path: Path) -> str:
    """Display/lookup name for a profile file: the filename without its .sqlite3
    extension. Only the final suffix is stripped, so a name containing dots
    (e.g. "sarah.2026.sqlite3") keeps its interior dots.
    """
    return Path(path).stem


def _read_conf_file(path: Path) -> Path | None:
    """Read a single gcforge.conf file. Returns None if absent or empty."""
    path = Path(path)
    if not path.exists():
        return None
    cfg = configparser.ConfigParser(interpolation=None)
    cfg.read(path, encoding="utf-8")
    db_path = cfg.get("database", "path", fallback="")
    return Path(db_path) if db_path else None


def read_active_db(base_dir: Path, data_dir: Path) -> Path | None:
    """Return the profile path recorded in gcforge.conf, or None if neither the
    new (DATA_DIR) nor the legacy (BASE_DIR) conf file records one.

    If only the legacy conf exists, this performs the one-time B1 migration:
    the value is re-written to the new location and the legacy file is
    deleted, so there is never more than one source of truth going forward.
    """
    new = _read_conf_file(conf_path(data_dir))
    if new is not None:
        return new
    legacy = _read_conf_file(legacy_conf_path(base_dir))
    if legacy is not None:
        write_active_db(legacy, base_dir, data_dir)
        return legacy
    return None


def write_active_db(path: Path, base_dir: Path, data_dir: Path) -> None:
    """Record `path` as the active profile in DATA_DIR/gcforge.conf.

    Always writes the new location and deletes a legacy BASE_DIR/gcforge.conf
    if one exists (B1) — write is the natural point to converge dev installs
    that still have the old file. `interpolation=None` avoids ConfigParser
    treating a literal "%" in a Windows/user path as interpolation syntax (B4).
    """
    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    cfg = configparser.ConfigParser(interpolation=None)
    cfg["database"] = {"path": str(path)}
    with open(conf_path(data_dir), "w", encoding="utf-8") as f:
        cfg.write(f)
    legacy = legacy_conf_path(base_dir)
    if legacy.exists():
        legacy.unlink()


def clear_active_db(base_dir: Path, data_dir: Path) -> None:
    """Remove both conf files, reverting the active profile to the default."""
    for p in (conf_path(data_dir), legacy_conf_path(base_dir)):
        if p.exists():
            p.unlink()


def resolve_database_path(base_dir: Path, data_dir: Path, env: dict) -> Path:
    """Resolve the database that should be active for this process.

    Precedence: GCFORGE_DATABASE env var > gcforge.conf (new, then legacy,
    with migration) > DATA_DIR/db.sqlite3.
    """
    override = env.get("GCFORGE_DATABASE")
    if override:
        return Path(override)
    active = read_active_db(base_dir, data_dir)
    if active is not None:
        return active
    return default_profile_path(data_dir)


def list_profiles(data_dir: Path) -> list[Profile]:
    """Enumerate profiles: the default db.sqlite3 (if present) then
    DATABASES_DIR/*.sqlite3, sorted by name. Deliberately excludes backups/ —
    backups are not people — and -wal/-shm sidecars (the *.sqlite3 glob
    doesn't match them).
    """
    data_dir = Path(data_dir)
    profiles: list[Profile] = []

    default_path = default_profile_path(data_dir)
    if default_path.exists():
        st = default_path.stat()
        profiles.append(Profile(profile_name(default_path), default_path, st.st_size, st.st_mtime, True))

    databases_dir = data_dir / "databases"
    if databases_dir.exists():
        for f in sorted(databases_dir.glob(f"*{PROFILE_SUFFIX}")):
            st = f.stat()
            profiles.append(Profile(profile_name(f), f, st.st_size, st.st_mtime, False))

    return profiles


def active_profile_display_name(db_path: Path, data_dir: Path) -> str:
    """Display name for `db_path` — "Default" for the pre-multi-profile
    db.sqlite3 until it's renamed (R2), else the filename stem. Used by the
    navbar profile badge.
    """
    db_path = Path(db_path).resolve()
    for p in list_profiles(data_dir):
        if p.path.resolve() == db_path:
            return p.display_name
    return profile_name(db_path)


class ProfileError(ValueError):
    """Base for rename_profile() failures.

    Subclassed rather than distinguished by message text so that Django views
    (which need translated, user-facing text) can dispatch on exception type
    instead of parsing English prose — this module is stdlib-only and must not
    import Django's i18n machinery itself.
    """


class UnknownProfileError(ProfileError):
    """`path` isn't one of list_profiles()'s known profile files."""


class ActiveProfileError(ProfileError):
    """`path` is the database actually active for this process."""


class InvalidNameError(ProfileError):
    """`new_name` sanitized down to nothing."""


class NameCollisionError(ProfileError):
    """`new_name` collides with an existing profile."""

    def __init__(self, name: str):
        self.name = name
        super().__init__(f"A profile named {name!r} already exists.")


def rename_profile(path: Path, new_name: str, base_dir: Path, data_dir: Path) -> Path:
    """Rename a profile file on disk.

    Raises (all ProfileError subclasses, see above) if:
      - `path` isn't one of the profiles list_profiles() actually enumerates;
      - `path` is the database actually active for this process (resolved the
        same way `resolve_database_path()` resolves it — env override included
        — since that DB file is open and locked by Django);
      - `new_name` is empty after sanitization;
      - `new_name` collides with an existing profile.

    The default profile (DATA_DIR/db.sqlite3) is a fixed filename that
    `list_profiles()` looks for by name, so renaming it moves the file into
    DATABASES_DIR like any other named profile rather than renaming in place.

    If the on-disk gcforge.conf currently records `path` as the active target
    (possible even when `path` isn't the DB actually open for *this* process —
    e.g. this process was launched with a GCFORGE_DATABASE override), the conf
    is updated to point at the new path so it stays internally consistent.
    """
    path = Path(path)
    base_dir = Path(base_dir)
    data_dir = Path(data_dir)

    known_paths = {p.path.resolve() for p in list_profiles(data_dir)}
    if path.resolve() not in known_paths:
        raise UnknownProfileError("Not a known profile.")

    active = resolve_database_path(base_dir, data_dir, os.environ)
    if path.resolve() == active.resolve():
        raise ActiveProfileError("Cannot rename the profile that is currently active. Switch away from it first.")

    safe_name = "".join(c for c in new_name.strip() if c in _SAFE_NAME_CHARS)
    if not safe_name:
        raise InvalidNameError("Invalid profile name.")

    databases_dir = data_dir / "databases"
    databases_dir.mkdir(parents=True, exist_ok=True)
    new_path = databases_dir / f"{safe_name}{PROFILE_SUFFIX}"

    if new_path.resolve() == path.resolve():
        return path  # no-op rename

    if new_path.exists():
        raise NameCollisionError(safe_name)

    path.rename(new_path)

    recorded = read_active_db(base_dir, data_dir)
    if recorded is not None and recorded.resolve() == path.resolve():
        write_active_db(new_path, base_dir, data_dir)

    return new_path
