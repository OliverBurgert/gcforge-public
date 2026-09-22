"""Tests for preferences.backup utilities."""

import sqlite3
import tempfile
from datetime import date
from pathlib import Path
from unittest.mock import patch

from django.test import TestCase

from preferences.backup import (
    AUTO_ROTATE_PREFIX,
    _rotate,
    create_backup,
    do_daily_backup,
    fragmentation_info,
    is_auto_rotate,
    list_backups,
    should_vacuum,
)


def _make_sqlite(path: Path) -> None:
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE IF NOT EXISTS t (x INTEGER)")
    conn.commit()
    conn.close()


class IsAutoRotateTest(TestCase):
    def test_auto_rotate_prefix_matches(self):
        self.assertTrue(is_auto_rotate("auto_rotate_gcforge_backup_2025-01-01.sqlite3"))

    def test_manual_backup_not_auto_rotate(self):
        self.assertFalse(is_auto_rotate("my_manual_backup.sqlite3"))

    def test_prefix_in_middle_not_matched(self):
        self.assertFalse(is_auto_rotate("backup_auto_rotate_something.sqlite3"))

    def test_full_path_uses_filename_only(self):
        self.assertTrue(is_auto_rotate("/some/dir/auto_rotate_gcforge.sqlite3"))


class ListBackupsTest(TestCase):
    def test_nonexistent_dir_returns_empty(self):
        result = list_backups(Path("/nonexistent/path/xyz123"))
        self.assertEqual(result, [])

    def test_empty_dir_returns_empty(self):
        with tempfile.TemporaryDirectory() as d:
            result = list_backups(Path(d))
        self.assertEqual(result, [])

    def test_sqlite3_files_returned(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)
            (p / "backup1.sqlite3").touch()
            (p / "backup2.sqlite3").touch()
            result = list_backups(p)
        self.assertEqual(len(result), 2)

    def test_non_sqlite3_files_excluded(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)
            (p / "backup.sqlite3").touch()
            (p / "backup.zip").touch()
            result = list_backups(p)
        self.assertEqual(len(result), 1)

    def test_result_has_expected_keys(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)
            (p / "auto_rotate_test.sqlite3").touch()
            result = list_backups(p)
        self.assertIn("path", result[0])
        self.assertIn("name", result[0])
        self.assertIn("size", result[0])
        self.assertIn("mtime", result[0])
        self.assertIn("auto_rotate", result[0])

    def test_auto_rotate_flag_set_correctly(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)
            (p / "auto_rotate_gcforge.sqlite3").touch()
            (p / "manual_backup.sqlite3").touch()
            result = list_backups(p)
            names = {r["name"]: r["auto_rotate"] for r in result}
        self.assertTrue(names["auto_rotate_gcforge.sqlite3"])
        self.assertFalse(names["manual_backup.sqlite3"])


class RotateTest(TestCase):
    def test_keeps_most_recent_n(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)
            files = []
            for i in range(5):
                f = p / f"{AUTO_ROTATE_PREFIX}gcforge_{i:02d}.sqlite3"
                f.touch()
                import time; time.sleep(0.01)
                files.append(f)
            _rotate(p, keep=3)
            remaining = list(p.glob(f"{AUTO_ROTATE_PREFIX}*.sqlite3"))
        self.assertEqual(len(remaining), 3)

    def test_manual_backups_not_deleted(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)
            manual = p / "manual_backup.sqlite3"
            manual.touch()
            for i in range(5):
                f = p / f"{AUTO_ROTATE_PREFIX}gcforge_{i:02d}.sqlite3"
                f.touch()
            _rotate(p, keep=0)
            self.assertTrue(manual.exists())

    def test_keep_zero_deletes_all_auto(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)
            for i in range(3):
                (p / f"{AUTO_ROTATE_PREFIX}gcforge_{i:02d}.sqlite3").touch()
            _rotate(p, keep=0)
            remaining = list(p.glob(f"{AUTO_ROTATE_PREFIX}*.sqlite3"))
        self.assertEqual(remaining, [])

    def test_keep_more_than_exist_leaves_all(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)
            for i in range(2):
                (p / f"{AUTO_ROTATE_PREFIX}gcforge_{i:02d}.sqlite3").touch()
            _rotate(p, keep=10)
            remaining = list(p.glob(f"{AUTO_ROTATE_PREFIX}*.sqlite3"))
        self.assertEqual(len(remaining), 2)


class CreateBackupTest(TestCase):
    def test_creates_backup_file(self):
        with tempfile.TemporaryDirectory() as d:
            dest = Path(d) / "backup.sqlite3"
            db_path = Path(d) / "source.sqlite3"
            _make_sqlite(db_path)
            with patch("preferences.backup.get_db_path", return_value=db_path):
                create_backup(dest)
            self.assertTrue(dest.exists())

    def test_backup_is_valid_sqlite(self):
        with tempfile.TemporaryDirectory() as d:
            dest = Path(d) / "backup.sqlite3"
            db_path = Path(d) / "source.sqlite3"
            _make_sqlite(db_path)
            with patch("preferences.backup.get_db_path", return_value=db_path):
                create_backup(dest)
            conn = sqlite3.connect(str(dest))
            try:
                tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            finally:
                conn.close()
            self.assertTrue(any(row[0] == "t" for row in tables))

    def test_creates_parent_dirs(self):
        with tempfile.TemporaryDirectory() as d:
            db_path = Path(d) / "source.sqlite3"
            _make_sqlite(db_path)
            dest = Path(d) / "nested" / "dir" / "backup.sqlite3"
            with patch("preferences.backup.get_db_path", return_value=db_path):
                create_backup(dest)
            self.assertTrue(dest.exists())


class FragmentationInfoTest(TestCase):
    def test_returns_expected_keys(self):
        info = fragmentation_info()
        for key in ("page_count", "freelist_count", "page_size", "total_bytes", "free_bytes", "fragmentation_pct"):
            self.assertIn(key, info)

    def test_values_are_numeric(self):
        info = fragmentation_info()
        self.assertIsInstance(info["page_count"], int)
        self.assertIsInstance(info["page_size"], int)
        self.assertIsInstance(info["fragmentation_pct"], float)

    def test_total_bytes_equals_page_count_times_page_size(self):
        info = fragmentation_info()
        self.assertEqual(info["total_bytes"], info["page_count"] * info["page_size"])


class ShouldVacuumTest(TestCase):
    def test_below_threshold_returns_false(self):
        info = {
            "page_count": 1000, "freelist_count": 5, "page_size": 4096,
            "total_bytes": 4096000, "free_bytes": 20480, "fragmentation_pct": 0.5,
        }
        with patch("preferences.backup.fragmentation_info", return_value=info):
            run, _ = should_vacuum(min_free_mb=50.0, min_pct=10.0)
        self.assertFalse(run)

    def test_above_both_thresholds_returns_true(self):
        info = {
            "page_count": 1000, "freelist_count": 200, "page_size": 4096,
            "total_bytes": 4096000, "free_bytes": 819200, "fragmentation_pct": 20.0,
        }
        with patch("preferences.backup.fragmentation_info", return_value=info):
            run, _ = should_vacuum(min_free_mb=0.5, min_pct=10.0)
        self.assertTrue(run)

    def test_returns_info_dict(self):
        info = fragmentation_info()
        _, returned_info = should_vacuum()
        self.assertIn("page_count", returned_info)


class DoDailyBackupTest(TestCase):
    def test_skips_if_auto_disabled(self):
        from preferences.models import UserPreference
        UserPreference.objects.update_or_create(key="backup_auto_enabled", defaults={"value": "false"})
        result = do_daily_backup()
        self.assertIsNone(result)

    def test_skips_if_today_backup_exists(self):
        with tempfile.TemporaryDirectory() as d:
            backup_dir = Path(d)
            today = date.today().isoformat()
            existing = backup_dir / f"{AUTO_ROTATE_PREFIX}gcforge_backup_{today}.sqlite3"
            existing.touch()
            with (
                patch("preferences.backup.get_backup_dir", return_value=backup_dir),
                patch("preferences.models.UserPreference.get", return_value=True),
            ):
                result = do_daily_backup()
            self.assertEqual(result, existing)

    def test_creates_backup_when_absent(self):
        with tempfile.TemporaryDirectory() as d:
            backup_dir = Path(d)
            db_path = Path(d) / "source.sqlite3"
            _make_sqlite(db_path)

            def fake_pref_get(key, default=None):
                if key == "backup_auto_enabled":
                    return True
                return default

            with (
                patch("preferences.backup.get_backup_dir", return_value=backup_dir),
                patch("preferences.backup.get_db_path", return_value=db_path),
                patch("preferences.backup.get_rotate_count", return_value=5),
                patch("preferences.models.UserPreference.get", side_effect=fake_pref_get),
            ):
                result = do_daily_backup()
            self.assertIsNotNone(result)
            self.assertTrue(result.exists())
            today = date.today().isoformat()
            self.assertIn(today, result.name)
