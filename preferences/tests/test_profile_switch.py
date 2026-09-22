"""Tests for the P3 in-session switch: task guard, restart-mechanism
detection, and port handover — see docs/multi-profile-plan.md §6, §11.
"""

import os
import socket
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.urls import reverse

from gcforge import profiles as gcforge_profiles
from gcforge import restart
from geocaches.tasks import runner as task_runner


class ActiveTasksTest(TestCase):
    def setUp(self):
        with task_runner._lock:
            task_runner._registry.clear()

    def tearDown(self):
        with task_runner._lock:
            task_runner._registry.clear()

    def test_returns_pending_and_running_ignores_terminal(self):
        infos = {
            "p1": task_runner.TaskInfo(id="p1", name="pending", state=task_runner.TaskState.PENDING),
            "r1": task_runner.TaskInfo(id="r1", name="running", state=task_runner.TaskState.RUNNING),
            "d1": task_runner.TaskInfo(id="d1", name="done", state=task_runner.TaskState.COMPLETED),
            "f1": task_runner.TaskInfo(id="f1", name="failed", state=task_runner.TaskState.FAILED),
            "c1": task_runner.TaskInfo(id="c1", name="cancelled", state=task_runner.TaskState.CANCELLED),
        }
        with task_runner._lock:
            task_runner._registry.update(infos)

        result = task_runner.active_tasks()

        self.assertEqual({r["id"] for r in result}, {"p1", "r1"})


class CanAutoRestartTest(TestCase):
    def test_frozen_always_true(self):
        with patch.object(sys, "frozen", True, create=True):
            ok, mode = restart.can_auto_restart()
        self.assertTrue(ok)
        self.assertEqual(mode, "frozen")

    def test_dev_noreload_true(self):
        with patch.object(sys, "argv", ["manage.py", "runserver", "127.0.0.1:8000", "--noreload"]):
            ok, mode = restart.can_auto_restart()
        self.assertTrue(ok)
        self.assertEqual(mode, "noreload")

    def test_autoreload_child_with_trigger_module_true(self):
        with patch.object(sys, "argv", ["manage.py", "runserver", "127.0.0.1:8000"]):
            with patch.dict(os.environ, {"RUN_MAIN": "true"}):
                # gcforge/settings.py already imports it at module load time —
                # it should already be present in a real test run.
                self.assertIn("gcforge._restart_trigger", sys.modules)
                ok, mode = restart.can_auto_restart()
        self.assertTrue(ok)
        self.assertEqual(mode, "autoreload")

    def test_autoreload_child_without_trigger_module_reports_reason(self):
        trigger_mod = sys.modules.pop("gcforge._restart_trigger", None)
        try:
            with patch.object(sys, "argv", ["manage.py", "runserver", "127.0.0.1:8000"]):
                with patch.dict(os.environ, {"RUN_MAIN": "true"}):
                    ok, reason = restart.can_auto_restart()
            self.assertFalse(ok)
            self.assertIn("trigger", reason.lower())
        finally:
            if trigger_mod is not None:
                sys.modules["gcforge._restart_trigger"] = trigger_mod

    def test_plain_manage_test_returns_false(self):
        with patch.object(sys, "argv", ["manage.py", "test"]):
            with patch.dict(os.environ, {}, clear=False):
                os.environ.pop("RUN_MAIN", None)
                ok, reason = restart.can_auto_restart()
        self.assertFalse(ok)
        self.assertTrue(reason)


class WaitForPortFreeTest(TestCase):
    def test_returns_true_promptly_for_a_closed_port(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()  # port is now free

        start = time.time()
        result = restart.wait_for_port_free(port, timeout=5)
        elapsed = time.time() - start

        self.assertTrue(result)
        self.assertLess(elapsed, 2)

    def test_times_out_for_an_open_listening_socket(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.bind(("127.0.0.1", 0))
        # A generous backlog: wait_for_port_free() connects-then-closes on
        # every poll without ever being accept()ed, and a too-small backlog
        # fills up within the test's short window — the OS then refuses new
        # SYNs and the port would look "free" to the probe even though
        # nothing has actually released it. Not a concern for the real
        # use case (the old server is actively accepting connections right
        # up until it calls os._exit(0), which closes the socket outright).
        s.listen(128)
        try:
            port = s.getsockname()[1]
            start = time.time()
            result = restart.wait_for_port_free(port, timeout=1)
            elapsed = time.time() - start

            self.assertFalse(result)
            self.assertGreaterEqual(elapsed, 1)
        finally:
            s.close()


class _SwitchProfileTestBase(TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.base_dir = root / "app"
        self.data_dir = root / "data"
        self.base_dir.mkdir()
        self.data_dir.mkdir()
        self.databases_dir = self.data_dir / "databases"
        (self.data_dir / "db.sqlite3").touch()

        self._override = override_settings(
            BASE_DIR=self.base_dir, DATA_DIR=self.data_dir, DATABASES_DIR=self.databases_dir,
        )
        self._override.enable()
        self.addCleanup(self._override.disable)

        with task_runner._lock:
            task_runner._registry.clear()
        self.addCleanup(self._clear_tasks)

    def _clear_tasks(self):
        with task_runner._lock:
            task_runner._registry.clear()

    def _touch(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
        return path


class SwitchProfileTaskGuardTest(_SwitchProfileTestBase):
    def test_active_task_renders_confirmation_without_restarting(self):
        sarah = self._touch(self.databases_dir / "sarah.sqlite3")
        with task_runner._lock:
            task_runner._registry["t1"] = task_runner.TaskInfo(
                id="t1", name="Importing GPX", state=task_runner.TaskState.RUNNING,
            )

        with patch("gcforge.restart.request_restart") as mock_restart:
            resp = self.client.post(reverse("preferences:switch_profile"), {"db_path": str(sarah)})

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Importing GPX")
        mock_restart.assert_not_called()
        # Conf untouched — the switch never actually happened.
        self.assertIsNone(gcforge_profiles.read_active_db(self.base_dir, self.data_dir))

    def test_no_active_tasks_skips_confirmation(self):
        sarah = self._touch(self.databases_dir / "sarah.sqlite3")

        with patch("gcforge.restart.request_restart") as mock_restart, \
             patch("gcforge.restart.can_auto_restart", return_value=(False, "test")):
            resp = self.client.post(reverse("preferences:switch_profile"), {"db_path": str(sarah)})

        self.assertEqual(resp.status_code, 302)
        mock_restart.assert_not_called()  # can_auto_restart() False → manual fallback
        self.assertEqual(gcforge_profiles.read_active_db(self.base_dir, self.data_dir), sarah)

    def test_confirm_cancels_tasks_and_triggers_restart(self):
        sarah = self._touch(self.databases_dir / "sarah.sqlite3")
        with task_runner._lock:
            task_runner._registry["t1"] = task_runner.TaskInfo(
                id="t1", name="Importing GPX", state=task_runner.TaskState.RUNNING,
            )

        with patch("gcforge.restart.request_restart") as mock_restart, \
             patch("gcforge.restart.can_auto_restart", return_value=(True, "noreload")):
            resp = self.client.post(
                reverse("preferences:switch_profile"), {"db_path": str(sarah), "confirm": "1"},
            )

        self.assertEqual(resp.status_code, 200)
        mock_restart.assert_called_once()
        info = task_runner.get_task("t1")
        self.assertEqual(info["state"], "cancelled")
        self.assertEqual(gcforge_profiles.read_active_db(self.base_dir, self.data_dir), sarah)


class SwitchProfileRestartModeTest(_SwitchProfileTestBase):
    def test_auto_restart_capable_renders_switching_page(self):
        sarah = self._touch(self.databases_dir / "sarah.sqlite3")

        with patch("gcforge.restart.request_restart") as mock_restart, \
             patch("gcforge.restart.can_auto_restart", return_value=(True, "noreload")):
            resp = self.client.post(reverse("preferences:switch_profile"), {"db_path": str(sarah)})

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "sarah")
        self.assertIn(b"gcfProfileSwitchPoll", resp.content)
        mock_restart.assert_called_once()

    def test_not_restart_capable_falls_back_to_manual_message(self):
        sarah = self._touch(self.databases_dir / "sarah.sqlite3")

        with patch("gcforge.restart.request_restart") as mock_restart, \
             patch("gcforge.restart.can_auto_restart", return_value=(False, "test")):
            resp = self.client.post(reverse("preferences:switch_profile"), {"db_path": str(sarah)})

        self.assertEqual(resp.status_code, 302)
        mock_restart.assert_not_called()
        session = self.client.session
        msg = session.get("db_switch_msg")
        self.assertTrue(msg["ok"])
        self.assertIn("restart", msg["text"].lower())

    def test_unknown_path_rejected(self):
        ghost = self.databases_dir / "ghost.sqlite3"

        with patch("gcforge.restart.request_restart") as mock_restart:
            resp = self.client.post(reverse("preferences:switch_profile"), {"db_path": str(ghost)})

        self.assertEqual(resp.status_code, 302)
        mock_restart.assert_not_called()
        msg = self.client.session.get("db_switch_msg")
        self.assertFalse(msg["ok"])
