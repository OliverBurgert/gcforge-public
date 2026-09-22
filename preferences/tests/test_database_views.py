"""View-level tests for the Database/Profiles tab — create_database(), switch_database(),
and rename_profile(), using the Django test client against a temp BASE_DIR/DATA_DIR.

The migrate subprocess is always mocked — these tests never actually run migrations.
"""

import tempfile
from pathlib import Path
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.urls import reverse

from gcforge import profiles as gcforge_profiles


class _ProfileViewTestBase(TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.base_dir = root / "app"
        self.data_dir = root / "data"
        self.base_dir.mkdir()
        self.data_dir.mkdir()
        self.databases_dir = self.data_dir / "databases"
        (self.data_dir / "db.sqlite3").touch()  # pre-existing default profile

        self._override = override_settings(
            BASE_DIR=self.base_dir, DATA_DIR=self.data_dir, DATABASES_DIR=self.databases_dir,
        )
        self._override.enable()
        self.addCleanup(self._override.disable)

    def _touch(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
        return path


class CreateDatabaseTest(_ProfileViewTestBase):
    @patch("preferences.views.database._migrate_new_database", return_value=(True, ""))
    def test_without_set_active_leaves_conf_untouched(self, mock_migrate):
        existing_target = self._touch(self.databases_dir / "existing.sqlite3")
        gcforge_profiles.write_active_db(existing_target, self.base_dir, self.data_dir)

        resp = self.client.post(reverse("preferences:create_database"), {"db_name": "sarah"})

        self.assertEqual(resp.status_code, 302)
        self.assertTrue((self.databases_dir / "sarah.sqlite3").exists())
        self.assertEqual(gcforge_profiles.read_active_db(self.base_dir, self.data_dir), existing_target)
        mock_migrate.assert_called_once()

    @patch("preferences.views.database._migrate_new_database", return_value=(True, ""))
    def test_with_set_active_writes_conf(self, mock_migrate):
        resp = self.client.post(
            reverse("preferences:create_database"), {"db_name": "sarah", "set_active": "1"}
        )

        self.assertEqual(resp.status_code, 302)
        new_path = self.databases_dir / "sarah.sqlite3"
        self.assertEqual(gcforge_profiles.read_active_db(self.base_dir, self.data_dir), new_path)

    @patch("preferences.views.database._migrate_new_database", return_value=(False, "boom"))
    def test_migrate_failure_removes_half_created_file_and_leaves_conf(self, mock_migrate):
        existing_target = self._touch(self.databases_dir / "existing.sqlite3")
        gcforge_profiles.write_active_db(existing_target, self.base_dir, self.data_dir)

        resp = self.client.post(
            reverse("preferences:create_database"), {"db_name": "sarah", "set_active": "1"}
        )

        self.assertEqual(resp.status_code, 302)
        self.assertFalse((self.databases_dir / "sarah.sqlite3").exists())
        self.assertEqual(gcforge_profiles.read_active_db(self.base_dir, self.data_dir), existing_target)

    @patch("preferences.views.database._migrate_new_database")
    def test_name_collision_rejected_before_migrating(self, mock_migrate):
        self._touch(self.databases_dir / "sarah.sqlite3")

        resp = self.client.post(reverse("preferences:create_database"), {"db_name": "sarah"})

        self.assertEqual(resp.status_code, 302)
        mock_migrate.assert_not_called()


class SwitchDatabaseTest(_ProfileViewTestBase):
    def test_switch_to_default_removes_conf(self):
        other = self._touch(self.databases_dir / "sarah.sqlite3")
        gcforge_profiles.write_active_db(other, self.base_dir, self.data_dir)

        resp = self.client.post(
            reverse("preferences:switch_database"), {"db_path": str(self.data_dir / "db.sqlite3")}
        )

        self.assertEqual(resp.status_code, 302)
        self.assertIsNone(gcforge_profiles.read_active_db(self.base_dir, self.data_dir))

    def test_switch_to_named_profile_writes_conf(self):
        other = self._touch(self.databases_dir / "sarah.sqlite3")

        resp = self.client.post(reverse("preferences:switch_database"), {"db_path": str(other)})

        self.assertEqual(resp.status_code, 302)
        self.assertEqual(gcforge_profiles.read_active_db(self.base_dir, self.data_dir), other)

    def test_switch_to_nonexistent_path_rejected(self):
        ghost = self.databases_dir / "ghost.sqlite3"

        resp = self.client.post(reverse("preferences:switch_database"), {"db_path": str(ghost)})

        self.assertEqual(resp.status_code, 302)
        msg = self.client.session.get("db_switch_msg")
        self.assertIsNotNone(msg)
        self.assertFalse(msg["ok"])


class RenameProfileViewTest(_ProfileViewTestBase):
    def test_renames_inactive_profile(self):
        other = self._touch(self.databases_dir / "sarah.sqlite3")

        resp = self.client.post(
            reverse("preferences:rename_profile"), {"path": str(other), "new_name": "sarah2"}
        )

        self.assertEqual(resp.status_code, 302)
        self.assertFalse(other.exists())
        self.assertTrue((self.databases_dir / "sarah2.sqlite3").exists())
        msg = self.client.session.get("db_switch_msg")
        self.assertTrue(msg["ok"])

    def test_rejects_renaming_the_active_profile(self):
        default_path = self.data_dir / "db.sqlite3"  # implicit active — no conf written

        resp = self.client.post(
            reverse("preferences:rename_profile"), {"path": str(default_path), "new_name": "oliver"}
        )

        self.assertEqual(resp.status_code, 302)
        self.assertTrue(default_path.exists())
        msg = self.client.session.get("db_switch_msg")
        self.assertFalse(msg["ok"])
