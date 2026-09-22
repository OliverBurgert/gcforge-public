"""Pure-function tests for gcforge.profiles — profile resolution, conf read/write,
and rename. No Django DB needed; everything runs against temp directories.
"""

import tempfile
import unittest
from pathlib import Path

from gcforge import profiles


def _touch(path: Path, content: bytes = b"") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


class ListProfilesTest(unittest.TestCase):
    def test_empty_data_dir_returns_empty(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(profiles.list_profiles(Path(d)), [])

    def test_single_default_db_returns_one_entry(self):
        with tempfile.TemporaryDirectory() as d:
            data_dir = Path(d)
            _touch(data_dir / "db.sqlite3")
            result = profiles.list_profiles(data_dir)
            self.assertEqual(len(result), 1)
            self.assertEqual(result[0].name, "db")
            self.assertTrue(result[0].is_default)

    def test_default_plus_databases_dir_sorted(self):
        with tempfile.TemporaryDirectory() as d:
            data_dir = Path(d)
            _touch(data_dir / "db.sqlite3")
            _touch(data_dir / "databases" / "sarah.sqlite3")
            _touch(data_dir / "databases" / "adam.sqlite3")
            result = profiles.list_profiles(data_dir)
            names = [p.name for p in result]
            # default first, then databases/ sorted by name
            self.assertEqual(names, ["db", "adam", "sarah"])
            self.assertTrue(result[0].is_default)
            self.assertFalse(result[1].is_default)
            self.assertFalse(result[2].is_default)

    def test_excludes_backups_wal_shm_and_non_sqlite3(self):
        with tempfile.TemporaryDirectory() as d:
            data_dir = Path(d)
            _touch(data_dir / "db.sqlite3")
            _touch(data_dir / "databases" / "sarah.sqlite3")
            _touch(data_dir / "databases" / "sarah.sqlite3-wal")
            _touch(data_dir / "databases" / "sarah.sqlite3-shm")
            _touch(data_dir / "databases" / "notes.txt")
            _touch(data_dir / "backups" / "auto_rotate_gcforge_backup.sqlite3")
            result = profiles.list_profiles(data_dir)
            names = [p.name for p in result]
            self.assertEqual(names, ["db", "sarah"])


class ProfileNameTest(unittest.TestCase):
    def test_strips_extension(self):
        self.assertEqual(profiles.profile_name(Path("sarah.sqlite3")), "sarah")

    def test_handles_dots_in_name(self):
        self.assertEqual(profiles.profile_name(Path("sarah.2026.sqlite3")), "sarah.2026")


class DisplayNameTest(unittest.TestCase):
    def test_default_db_displays_as_default(self):
        with tempfile.TemporaryDirectory() as d:
            data_dir = Path(d)
            _touch(data_dir / "db.sqlite3")
            result = profiles.list_profiles(data_dir)
            self.assertEqual(result[0].display_name, "Default")

    def test_renamed_default_shows_real_name(self):
        with tempfile.TemporaryDirectory() as d:
            data_dir = Path(d)
            _touch(data_dir / "databases" / "oliver.sqlite3")
            result = profiles.list_profiles(data_dir)
            self.assertEqual(result[0].display_name, "oliver")


class ConfRoundTripTest(unittest.TestCase):
    def test_write_then_read_round_trips(self):
        with tempfile.TemporaryDirectory() as d:
            base_dir = Path(d) / "app"
            data_dir = Path(d) / "data"
            base_dir.mkdir()
            data_dir.mkdir()
            target = data_dir / "databases" / "sarah.sqlite3"
            profiles.write_active_db(target, base_dir, data_dir)
            self.assertEqual(profiles.read_active_db(base_dir, data_dir), target)

    def test_round_trips_path_with_percent(self):
        """Regression for B4: a bare ConfigParser applies BasicInterpolation,
        which raises on a literal '%' in the path."""
        with tempfile.TemporaryDirectory() as d:
            base_dir = Path(d) / "app"
            data_dir = Path(d) / "data"
            base_dir.mkdir()
            data_dir.mkdir()
            target = data_dir / "databases" / "100%sure.sqlite3"
            profiles.write_active_db(target, base_dir, data_dir)
            self.assertEqual(profiles.read_active_db(base_dir, data_dir), target)

    def test_round_trips_windows_backslash_path(self):
        with tempfile.TemporaryDirectory() as d:
            base_dir = Path(d) / "app"
            data_dir = Path(d) / "data"
            base_dir.mkdir()
            data_dir.mkdir()
            target = Path(r"C:\Users\sarah\gcforge-data\databases\sarah.sqlite3")
            profiles.write_active_db(target, base_dir, data_dir)
            self.assertEqual(profiles.read_active_db(base_dir, data_dir), target)

    def test_write_deletes_legacy_conf(self):
        """B1 migration: writing the new conf cleans up an existing legacy one."""
        with tempfile.TemporaryDirectory() as d:
            base_dir = Path(d) / "app"
            data_dir = Path(d) / "data"
            base_dir.mkdir()
            data_dir.mkdir()
            legacy = profiles.legacy_conf_path(base_dir)
            legacy.parent.mkdir(parents=True, exist_ok=True)
            legacy.write_text("[database]\npath = /old/path/db.sqlite3\n", encoding="utf-8")

            profiles.write_active_db(data_dir / "databases" / "sarah.sqlite3", base_dir, data_dir)

            self.assertFalse(legacy.exists())

    def test_read_migrates_legacy_conf_to_new_location(self):
        """A dev install that only has the legacy conf gets migrated on read."""
        with tempfile.TemporaryDirectory() as d:
            base_dir = Path(d) / "app"
            data_dir = Path(d) / "data"
            base_dir.mkdir()
            data_dir.mkdir()
            legacy = profiles.legacy_conf_path(base_dir)
            legacy_target = data_dir / "databases" / "sarah.sqlite3"
            legacy.write_text(f"[database]\npath = {legacy_target}\n", encoding="utf-8")

            result = profiles.read_active_db(base_dir, data_dir)

            self.assertEqual(result, legacy_target)
            self.assertFalse(legacy.exists())
            self.assertTrue(profiles.conf_path(data_dir).exists())

    def test_read_prefers_new_conf_over_legacy(self):
        with tempfile.TemporaryDirectory() as d:
            base_dir = Path(d) / "app"
            data_dir = Path(d) / "data"
            base_dir.mkdir()
            data_dir.mkdir()
            new_target = data_dir / "databases" / "new.sqlite3"
            legacy_target = data_dir / "databases" / "old.sqlite3"
            profiles.conf_path(data_dir).write_text(f"[database]\npath = {new_target}\n", encoding="utf-8")
            profiles.legacy_conf_path(base_dir).write_text(f"[database]\npath = {legacy_target}\n", encoding="utf-8")

            self.assertEqual(profiles.read_active_db(base_dir, data_dir), new_target)

    def test_clear_active_db_removes_both_conf_files(self):
        with tempfile.TemporaryDirectory() as d:
            base_dir = Path(d) / "app"
            data_dir = Path(d) / "data"
            base_dir.mkdir()
            data_dir.mkdir()
            profiles.conf_path(data_dir).write_text("[database]\npath = x\n", encoding="utf-8")
            profiles.legacy_conf_path(base_dir).write_text("[database]\npath = y\n", encoding="utf-8")

            profiles.clear_active_db(base_dir, data_dir)

            self.assertFalse(profiles.conf_path(data_dir).exists())
            self.assertFalse(profiles.legacy_conf_path(base_dir).exists())


class ResolveDatabasePathTest(unittest.TestCase):
    def test_env_override_wins_over_conf_and_default(self):
        with tempfile.TemporaryDirectory() as d:
            base_dir = Path(d) / "app"
            data_dir = Path(d) / "data"
            base_dir.mkdir()
            data_dir.mkdir()
            profiles.conf_path(data_dir).write_text(
                f"[database]\npath = {data_dir / 'databases' / 'conf.sqlite3'}\n", encoding="utf-8"
            )
            env = {"GCFORGE_DATABASE": str(data_dir / "databases" / "env.sqlite3")}
            result = profiles.resolve_database_path(base_dir, data_dir, env)
            self.assertEqual(result, data_dir / "databases" / "env.sqlite3")

    def test_conf_wins_over_default_when_no_env(self):
        with tempfile.TemporaryDirectory() as d:
            base_dir = Path(d) / "app"
            data_dir = Path(d) / "data"
            base_dir.mkdir()
            data_dir.mkdir()
            target = data_dir / "databases" / "sarah.sqlite3"
            profiles.conf_path(data_dir).write_text(f"[database]\npath = {target}\n", encoding="utf-8")
            result = profiles.resolve_database_path(base_dir, data_dir, {})
            self.assertEqual(result, target)

    def test_default_when_no_env_and_no_conf(self):
        with tempfile.TemporaryDirectory() as d:
            base_dir = Path(d) / "app"
            data_dir = Path(d) / "data"
            base_dir.mkdir()
            data_dir.mkdir()
            result = profiles.resolve_database_path(base_dir, data_dir, {})
            self.assertEqual(result, data_dir / "db.sqlite3")


class RenameProfileTest(unittest.TestCase):
    def _dirs(self, d):
        base_dir = Path(d) / "app"
        data_dir = Path(d) / "data"
        base_dir.mkdir()
        data_dir.mkdir()
        return base_dir, data_dir

    def test_renames_inactive_named_profile(self):
        with tempfile.TemporaryDirectory() as d:
            base_dir, data_dir = self._dirs(d)
            _touch(data_dir / "db.sqlite3")  # this is the active (default) profile
            old_path = _touch(data_dir / "databases" / "sarah.sqlite3")

            new_path = profiles.rename_profile(old_path, "sarah2", base_dir, data_dir)

            self.assertFalse(old_path.exists())
            self.assertEqual(new_path, data_dir / "databases" / "sarah2.sqlite3")
            self.assertTrue(new_path.exists())

    def test_renames_default_profile_into_databases_dir(self):
        with tempfile.TemporaryDirectory() as d:
            base_dir, data_dir = self._dirs(d)
            default_path = _touch(data_dir / "db.sqlite3")
            # A second profile is active, so the default is inactive and renamable.
            other = _touch(data_dir / "databases" / "sarah.sqlite3")
            profiles.write_active_db(other, base_dir, data_dir)

            new_path = profiles.rename_profile(default_path, "oliver", base_dir, data_dir)

            self.assertFalse(default_path.exists())
            self.assertEqual(new_path, data_dir / "databases" / "oliver.sqlite3")

    def test_raises_when_renaming_the_active_profile(self):
        with tempfile.TemporaryDirectory() as d:
            base_dir, data_dir = self._dirs(d)
            default_path = _touch(data_dir / "db.sqlite3")
            # No conf written → default_path is the implicit active profile.
            with self.assertRaises(profiles.ActiveProfileError):
                profiles.rename_profile(default_path, "oliver", base_dir, data_dir)
            self.assertTrue(default_path.exists())

    def test_rejects_unknown_path(self):
        with tempfile.TemporaryDirectory() as d:
            base_dir, data_dir = self._dirs(d)
            _touch(data_dir / "db.sqlite3")
            rogue = Path(d) / "not-a-profile.sqlite3"
            _touch(rogue)
            with self.assertRaises(profiles.UnknownProfileError):
                profiles.rename_profile(rogue, "oliver", base_dir, data_dir)
            self.assertTrue(rogue.exists())

    def test_rejects_name_collision(self):
        with tempfile.TemporaryDirectory() as d:
            base_dir, data_dir = self._dirs(d)
            _touch(data_dir / "db.sqlite3")
            old_path = _touch(data_dir / "databases" / "sarah.sqlite3")
            _touch(data_dir / "databases" / "adam.sqlite3")

            with self.assertRaises(profiles.NameCollisionError):
                profiles.rename_profile(old_path, "adam", base_dir, data_dir)
            self.assertTrue(old_path.exists())

    def test_updates_conf_when_renaming_its_recorded_target(self):
        """The renamed profile is the one gcforge.conf currently points at
        (but a GCFORGE_DATABASE override elsewhere makes it not "active" for
        this comparison) — the conf should follow the rename.
        """
        with tempfile.TemporaryDirectory() as d:
            base_dir, data_dir = self._dirs(d)
            _touch(data_dir / "db.sqlite3")
            old_path = _touch(data_dir / "databases" / "sarah.sqlite3")
            profiles.write_active_db(old_path, base_dir, data_dir)

            import os
            env_backup = os.environ.get("GCFORGE_DATABASE")
            os.environ["GCFORGE_DATABASE"] = str(data_dir / "db.sqlite3")
            try:
                new_path = profiles.rename_profile(old_path, "sarah2", base_dir, data_dir)
            finally:
                if env_backup is None:
                    os.environ.pop("GCFORGE_DATABASE", None)
                else:
                    os.environ["GCFORGE_DATABASE"] = env_backup

            self.assertEqual(profiles.read_active_db(base_dir, data_dir), new_path)


if __name__ == "__main__":
    unittest.main()
