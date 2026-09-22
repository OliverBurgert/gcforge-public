"""Cross-profile comparison — "caches in this area that neither of us has
found yet." See docs/multi-profile-plan.md §9 (P4).

Opens the *other* profile's .sqlite3 on a dedicated, short-lived raw
sqlite3 connection and ATTACHes it alongside the active profile's own file,
rather than through Django's ORM/multi-db routing or the pooled Django
connection:

- Routing would need a DATABASES entry for the other profile that can only
  be known at request time, not settings-import time.
- ATTACHing on the pooled Django connection (django.db.connection) would
  persist for the rest of that thread's life, holding a file handle on the
  other profile and leaking `other.` into unrelated queries.

A short-lived connection opened and closed inside the caller is correctly
scoped instead.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from django.conf import settings as django_settings
from django.utils.translation import gettext as _

from geocaches.geo import bearing_deg, haversine_km

# Columns the comparison query and the schema-compatibility guard depend on.
_REQUIRED_COLUMNS = {
    "id", "gc_code", "oc_code", "al_code", "name", "cache_type",
    "latitude", "longitude", "difficulty", "terrain",
    "found", "completed", "deleted_at",
}

_SCOPE_CHUNK_SIZE = 500

# Join key is the code, not the pk — primary keys are unrelated across
# profiles. Three separate EXISTS clauses (not one OR'd predicate) so each is
# a single indexed equality lookup (gc_code/oc_code/al_code all carry
# db_index=True). `completed` is OR'd with `found` because Adventure Lab
# parents must never have found=True (see geocaches/models/cache.py).
# deleted_at IS NULL is applied explicitly on both sides — the ORM applies it
# via LiveCacheManager for normal querysets, but this is raw SQL.
_COMPARE_SQL = """
SELECT c.id, c.gc_code, c.oc_code, c.al_code, c.name, c.cache_type,
       c.latitude, c.longitude, c.difficulty, c.terrain,
       (c.found = 1 OR c.completed = 1) AS found_by_me,
       (   (c.gc_code <> '' AND EXISTS (SELECT 1 FROM other.geocaches_geocache o
              WHERE o.gc_code = c.gc_code AND o.deleted_at IS NULL
                AND (o.found = 1 OR o.completed = 1)))
        OR (c.oc_code <> '' AND EXISTS (SELECT 1 FROM other.geocaches_geocache o
              WHERE o.oc_code = c.oc_code AND o.deleted_at IS NULL
                AND (o.found = 1 OR o.completed = 1)))
        OR (c.al_code <> '' AND EXISTS (SELECT 1 FROM other.geocaches_geocache o
              WHERE o.al_code = c.al_code AND o.deleted_at IS NULL
                AND (o.found = 1 OR o.completed = 1)))
       ) AS found_by_other
  FROM main.geocaches_geocache c
  JOIN temp.scope s ON s.id = c.id
 WHERE c.deleted_at IS NULL
"""


def _register_functions(conn: sqlite3.Connection) -> None:
    """Mirror gcforge/settings.py::_configure_sqlite — a raw connection
    doesn't get these for free the way Django's pooled connection does.
    """
    def _hkm(lat1, lon1, lat2, lon2):
        if None in (lat1, lon1, lat2, lon2):
            return None
        return haversine_km(lat1, lon1, lat2, lon2)

    def _bdeg(lat1, lon1, lat2, lon2):
        if None in (lat1, lon1, lat2, lon2):
            return None
        return bearing_deg(lat1, lon1, lat2, lon2)

    conn.create_function("haversine_km", 4, _hkm)
    conn.create_function("bearing_deg", 4, _bdeg)


def schema_compat(other_db_path: Path) -> tuple[bool, str]:
    """Check `other_db_path` is safe to compare against.

    Returns (False, message) — hard fail — if a required column is missing.
    Returns (True, "") if fully compatible, or (True, warning) when the
    other profile's migration state doesn't match the active one (comparison
    still proceeds; the warning is informational).
    """
    other_db_path = Path(other_db_path)
    if not other_db_path.exists():
        return False, _("Profile database not found.")

    conn = sqlite3.connect(str(other_db_path))
    try:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(geocaches_geocache)")}
        missing = _REQUIRED_COLUMNS - cols
        if missing:
            return False, _(
                "Incompatible profile database — missing column(s): %(cols)s."
            ) % {"cols": ", ".join(sorted(missing))}

        other_max = conn.execute(
            "SELECT MAX(name) FROM django_migrations WHERE app = 'geocaches'"
        ).fetchone()[0]
    finally:
        conn.close()

    active_db_path = Path(django_settings.DATABASES["default"]["NAME"])
    main_conn = sqlite3.connect(str(active_db_path))
    try:
        main_max = main_conn.execute(
            "SELECT MAX(name) FROM django_migrations WHERE app = 'geocaches'"
        ).fetchone()[0]
    finally:
        main_conn.close()

    if other_max != main_max:
        return True, _(
            "This profile was last used with a different GCForge version; "
            "results may be incomplete. Open it once to bring it up to date."
        )
    return True, ""


def _bucket(found_by_me: bool, found_by_other: bool) -> str:
    if found_by_me and found_by_other:
        return "both"
    if found_by_me:
        return "only_me"
    if found_by_other:
        return "only_them"
    return "neither"


def compare_found(active_db_path: Path, other_db_path: Path, scope_pks) -> list[dict]:
    """Compare found-status between the active profile and `other_db_path`.

    `scope_pks` is any iterable of active-profile Geocache pks (typically
    `qs.values_list("pk", flat=True).iterator()` from the normal filter
    engine — see geocaches/views/list.py::_filtered_qs — so ?fx= filter
    expressions, the Now-Forging scope, and the map bbox all apply
    unchanged). Returns one dict per scoped cache with an added "bucket" key:
    "neither" (the headline case), "only_me", "only_them", or "both".
    """
    conn = sqlite3.connect(str(active_db_path))
    try:
        conn.execute("PRAGMA busy_timeout=30000")
        _register_functions(conn)
        conn.execute("ATTACH DATABASE ? AS other", (str(other_db_path),))
        try:
            conn.execute("CREATE TEMP TABLE scope(id INTEGER PRIMARY KEY)")
            batch = []
            for pk in scope_pks:
                batch.append((pk,))
                if len(batch) >= _SCOPE_CHUNK_SIZE:
                    conn.executemany("INSERT INTO temp.scope(id) VALUES (?)", batch)
                    batch = []
            if batch:
                conn.executemany("INSERT INTO temp.scope(id) VALUES (?)", batch)

            cur = conn.execute(_COMPARE_SQL)
            columns = [d[0] for d in cur.description]
            rows = cur.fetchall()
        finally:
            # Python's sqlite3 module opens an implicit transaction on the
            # first DML statement (the scope-table inserts above) and never
            # auto-commits it — DETACH refuses to run with that still open,
            # even (especially) on the exception path above.
            conn.commit()
            conn.execute("DETACH DATABASE other")
    finally:
        conn.close()

    results = []
    for row in rows:
        d = dict(zip(columns, row, strict=True))
        found_by_me = bool(d["found_by_me"])
        found_by_other = bool(d["found_by_other"])
        d["found_by_me"] = found_by_me
        d["found_by_other"] = found_by_other
        d["bucket"] = _bucket(found_by_me, found_by_other)
        results.append(d)
    return results
