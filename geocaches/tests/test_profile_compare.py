"""Tests for geocaches.services.profile_compare — the P4 cross-profile
comparison. Builds throwaway SQLite files with a minimal geocaches_geocache +
django_migrations schema (same shape as _make_sqlite() in test_backup.py),
independent of Django's own test database. See docs/multi-profile-plan.md §9.
"""

import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from django.conf import settings as django_settings
from django.test import TestCase

from geocaches.services.profile_compare import compare_found, schema_compat

_CREATE_GEOCACHE_TABLE = """
CREATE TABLE geocaches_geocache (
    id INTEGER PRIMARY KEY,
    gc_code TEXT NOT NULL DEFAULT '',
    oc_code TEXT NOT NULL DEFAULT '',
    al_code TEXT NOT NULL DEFAULT '',
    name TEXT NOT NULL DEFAULT '',
    cache_type TEXT NOT NULL DEFAULT '',
    latitude REAL,
    longitude REAL,
    difficulty REAL,
    terrain REAL,
    found INTEGER NOT NULL DEFAULT 0,
    completed INTEGER NOT NULL DEFAULT 0,
    deleted_at TEXT
)
"""

_CREATE_MIGRATIONS_TABLE = """
CREATE TABLE django_migrations (
    id INTEGER PRIMARY KEY,
    app TEXT NOT NULL,
    name TEXT NOT NULL,
    applied TEXT
)
"""

_COLUMNS = (
    "id", "gc_code", "oc_code", "al_code", "name", "cache_type",
    "latitude", "longitude", "difficulty", "terrain", "found", "completed", "deleted_at",
)


def _make_db(path: Path, rows: list[dict], migrations=("0001_initial",)) -> None:
    conn = sqlite3.connect(str(path))
    try:
        conn.execute(_CREATE_GEOCACHE_TABLE)
        conn.execute(_CREATE_MIGRATIONS_TABLE)
        for name in migrations:
            conn.execute(
                "INSERT INTO django_migrations (app, name, applied) VALUES ('geocaches', ?, '2026-01-01')",
                (name,),
            )
        for row in rows:
            defaults = {
                "gc_code": "", "oc_code": "", "al_code": "", "name": "", "cache_type": "Traditional",
                "latitude": 50.0, "longitude": 8.0, "difficulty": 2.0, "terrain": 2.0,
                "found": 0, "completed": 0, "deleted_at": None,
            }
            defaults.update(row)
            values = tuple(defaults[c] for c in _COLUMNS if c != "id")
            conn.execute(
                "INSERT INTO geocaches_geocache (id, gc_code, oc_code, al_code, name, cache_type, "
                "latitude, longitude, difficulty, terrain, found, completed, deleted_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (row["id"],) + values,
            )
        conn.commit()
    finally:
        conn.close()


class CompareFoundTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.active_path = Path(self._tmp.name) / "active.sqlite3"
        self.other_path = Path(self._tmp.name) / "other.sqlite3"

    def test_bucketing_neither_only_me_only_them_both(self):
        _make_db(self.active_path, [
            {"id": 1, "gc_code": "GC001", "found": 0},   # neither
            {"id": 2, "gc_code": "GC002", "found": 1},   # only_me (other hasn't found it)
            {"id": 3, "gc_code": "GC003", "found": 0},   # only_them
            {"id": 4, "gc_code": "GC004", "found": 1},   # both
        ])
        _make_db(self.other_path, [
            {"id": 101, "gc_code": "GC001", "found": 0},
            {"id": 102, "gc_code": "GC002", "found": 0},
            {"id": 103, "gc_code": "GC003", "found": 1},
            {"id": 104, "gc_code": "GC004", "found": 1},
        ])

        results = compare_found(self.active_path, self.other_path, [1, 2, 3, 4])
        by_code = {r["gc_code"]: r for r in results}

        self.assertEqual(by_code["GC001"]["bucket"], "neither")
        self.assertEqual(by_code["GC002"]["bucket"], "only_me")
        self.assertEqual(by_code["GC003"]["bucket"], "only_them")
        self.assertEqual(by_code["GC004"]["bucket"], "both")

    def test_matches_via_gc_code(self):
        _make_db(self.active_path, [{"id": 1, "gc_code": "GC001", "found": 0}])
        _make_db(self.other_path, [{"id": 101, "gc_code": "GC001", "found": 1}])

        results = compare_found(self.active_path, self.other_path, [1])

        self.assertTrue(results[0]["found_by_other"])

    def test_matches_via_oc_code(self):
        _make_db(self.active_path, [{"id": 1, "oc_code": "OC001", "found": 0}])
        _make_db(self.other_path, [{"id": 101, "oc_code": "OC001", "found": 1}])

        results = compare_found(self.active_path, self.other_path, [1])

        self.assertTrue(results[0]["found_by_other"])

    def test_matches_via_al_code(self):
        _make_db(self.active_path, [{"id": 1, "al_code": "AL001", "found": 0}])
        _make_db(self.other_path, [{"id": 101, "al_code": "AL001", "found": 1}])

        results = compare_found(self.active_path, self.other_path, [1])

        self.assertTrue(results[0]["found_by_other"])

    def test_empty_code_on_one_side_never_matches_empty_on_the_other(self):
        # Both sides have a cache with an empty gc_code (default '') — the
        # <> '' guards must stop these from matching each other.
        _make_db(self.active_path, [{"id": 1, "gc_code": "", "oc_code": "", "al_code": "", "found": 0}])
        _make_db(self.other_path, [{"id": 101, "gc_code": "", "oc_code": "", "al_code": "", "found": 1}])

        results = compare_found(self.active_path, self.other_path, [1])

        self.assertFalse(results[0]["found_by_other"])
        self.assertEqual(results[0]["bucket"], "neither")

    def test_completed_on_al_parent_counts_as_found(self):
        _make_db(self.active_path, [{"id": 1, "al_code": "AL001", "found": 0, "completed": 1}])
        _make_db(self.other_path, [{"id": 101, "al_code": "AL001", "found": 0, "completed": 1}])

        results = compare_found(self.active_path, self.other_path, [1])

        self.assertTrue(results[0]["found_by_me"])
        self.assertTrue(results[0]["found_by_other"])

    def test_deleted_rows_on_the_other_side_are_ignored(self):
        _make_db(self.active_path, [{"id": 1, "gc_code": "GC001", "found": 0}])
        _make_db(self.other_path, [
            {"id": 101, "gc_code": "GC001", "found": 1, "deleted_at": "2026-01-01T00:00:00"},
        ])

        results = compare_found(self.active_path, self.other_path, [1])

        self.assertFalse(results[0]["found_by_other"])

    def test_deleted_rows_on_the_active_side_are_excluded_by_where_clause(self):
        _make_db(self.active_path, [
            {"id": 1, "gc_code": "GC001", "found": 0, "deleted_at": "2026-01-01T00:00:00"},
            {"id": 2, "gc_code": "GC002", "found": 0},
        ])
        _make_db(self.other_path, [{"id": 101, "gc_code": "GC002", "found": 0}])

        results = compare_found(self.active_path, self.other_path, [1, 2])

        ids = {r["id"] for r in results}
        self.assertEqual(ids, {2})

    def test_scope_restricts_the_result_set(self):
        _make_db(self.active_path, [
            {"id": 1, "gc_code": "GC001", "found": 0},
            {"id": 2, "gc_code": "GC002", "found": 0},
            {"id": 3, "gc_code": "GC003", "found": 0},
        ])
        _make_db(self.other_path, [{"id": 101, "gc_code": "GC001", "found": 0}])

        results = compare_found(self.active_path, self.other_path, [2])

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["id"], 2)

    def test_empty_scope_returns_empty_results(self):
        _make_db(self.active_path, [{"id": 1, "gc_code": "GC001", "found": 0}])
        _make_db(self.other_path, [{"id": 101, "gc_code": "GC001", "found": 0}])

        results = compare_found(self.active_path, self.other_path, [])

        self.assertEqual(results, [])

    def test_connection_closed_and_detached_even_when_query_raises(self):
        _make_db(self.active_path, [{"id": 1, "gc_code": "GC001", "found": 0}])
        _make_db(self.other_path, [{"id": 101, "gc_code": "GC001", "found": 0}])

        from unittest.mock import patch
        with patch(
            "geocaches.services.profile_compare._COMPARE_SQL",
            "SELECT * FROM nonexistent_table",
        ):
            with self.assertRaises(sqlite3.OperationalError):
                compare_found(self.active_path, self.other_path, [1])

        # If DETACH never ran, re-attaching under the same alias would fail.
        conn = sqlite3.connect(str(self.active_path))
        try:
            conn.execute("ATTACH DATABASE ? AS other", (str(self.other_path),))
            conn.execute("DETACH DATABASE other")
        finally:
            conn.close()


class SchemaCompatTest(TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.other_path = Path(self._tmp.name) / "other.sqlite3"
        self.active_path = Path(self._tmp.name) / "active.sqlite3"

    def test_hard_fails_on_missing_column(self):
        conn = sqlite3.connect(str(self.other_path))
        conn.execute("CREATE TABLE geocaches_geocache (id INTEGER PRIMARY KEY, gc_code TEXT)")
        conn.execute(_CREATE_MIGRATIONS_TABLE)
        conn.commit()
        conn.close()

        ok, msg = schema_compat(self.other_path)

        self.assertFalse(ok)
        self.assertTrue(msg)

    def test_missing_file_hard_fails(self):
        ok, msg = schema_compat(Path(self._tmp.name) / "ghost.sqlite3")

        self.assertFalse(ok)
        self.assertTrue(msg)

    def test_migration_mismatch_warns_but_does_not_fail(self):
        _make_db(self.other_path, [{"id": 1, "gc_code": "GC001"}], migrations=("0001_initial",))
        _make_db(self.active_path, [{"id": 1, "gc_code": "GC001"}],
                 migrations=("0001_initial", "0002_added_field"))

        with patch.dict(django_settings.DATABASES["default"], {"NAME": str(self.active_path)}):
            ok, msg = schema_compat(self.other_path)

        self.assertTrue(ok)
        self.assertTrue(msg)

    def test_matching_migrations_no_warning(self):
        _make_db(self.other_path, [{"id": 1, "gc_code": "GC001"}], migrations=("0001_initial",))
        _make_db(self.active_path, [{"id": 1, "gc_code": "GC001"}], migrations=("0001_initial",))

        with patch.dict(django_settings.DATABASES["default"], {"NAME": str(self.active_path)}):
            ok, msg = schema_compat(self.other_path)

        self.assertTrue(ok)
        self.assertEqual(msg, "")


if __name__ == "__main__":
    unittest.main()
