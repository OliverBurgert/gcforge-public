"""Tests for gcforge.bootstrap_picker — the pre-Django first-run profile picker.

Drives the real http.server.ThreadingHTTPServer against an ephemeral port,
exactly as a browser would (per docs/multi-profile-plan.md §11), rather than
calling the handler methods directly — the server lifecycle (serve_forever /
shutdown from a request thread) is itself part of what needs covering.
"""

import os
import socket
import tempfile
import threading
import time
import unittest
import urllib.parse
import urllib.request
from pathlib import Path
from unittest.mock import patch

from gcforge import bootstrap_picker, profiles


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _touch(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch()
    return path


def _get_with_retry(url: str, attempts: int = 50, delay: float = 0.05) -> str:
    last_exc = None
    for _ in range(attempts):
        try:
            return urllib.request.urlopen(url, timeout=1).read().decode("utf-8")
        except Exception as exc:
            last_exc = exc
            time.sleep(delay)
    raise AssertionError(f"Picker server never came up: {last_exc}")


class MaybePickProfileTest(unittest.TestCase):
    def setUp(self):
        # maybe_pick_profile() calls webbrowser.open() unconditionally when it
        # actually starts the picker server — never let a test pop a real tab.
        patcher = patch("gcforge.bootstrap_picker.webbrowser.open")
        patcher.start()
        self.addCleanup(patcher.stop)
        # It also prints the picker URL for the console user — keep the test
        # runner's output clean.
        printer = patch("builtins.print")
        printer.start()
        self.addCleanup(printer.stop)

    def _dirs(self, d):
        base_dir = Path(d) / "app"
        data_dir = Path(d) / "data"
        base_dir.mkdir()
        data_dir.mkdir()
        return base_dir, data_dir

    def test_returns_false_immediately_with_fewer_than_two_profiles(self):
        with tempfile.TemporaryDirectory() as d:
            base_dir, data_dir = self._dirs(d)
            _touch(data_dir / "db.sqlite3")
            result = bootstrap_picker.maybe_pick_profile(base_dir, data_dir, _free_port())
            self.assertFalse(result)

    def test_returns_false_immediately_when_env_override_set(self):
        with tempfile.TemporaryDirectory() as d:
            base_dir, data_dir = self._dirs(d)
            _touch(data_dir / "db.sqlite3")
            _touch(data_dir / "databases" / "sarah.sqlite3")
            old = os.environ.get("GCFORGE_DATABASE")
            os.environ["GCFORGE_DATABASE"] = str(data_dir / "db.sqlite3")
            try:
                result = bootstrap_picker.maybe_pick_profile(base_dir, data_dir, _free_port())
            finally:
                if old is None:
                    os.environ.pop("GCFORGE_DATABASE", None)
                else:
                    os.environ["GCFORGE_DATABASE"] = old
            self.assertFalse(result)

    def test_serves_picker_and_choose_writes_conf(self):
        with tempfile.TemporaryDirectory() as d:
            base_dir, data_dir = self._dirs(d)
            _touch(data_dir / "db.sqlite3")
            sarah = _touch(data_dir / "databases" / "sarah.sqlite3")
            port = _free_port()
            result_holder = {}

            def run():
                result_holder["result"] = bootstrap_picker.maybe_pick_profile(base_dir, data_dir, port)

            t = threading.Thread(target=run, daemon=True)
            t.start()
            try:
                url = f"http://127.0.0.1:{port}/"
                page = _get_with_retry(url)
                self.assertIn("sarah", page)
                self.assertIn("Default", page)

                body = urllib.parse.urlencode({"path": str(sarah)}).encode()
                req = urllib.request.Request(url + "choose", data=body, method="POST")
                resp = urllib.request.urlopen(req, timeout=5)
                self.assertEqual(resp.status, 200)

                t.join(timeout=5)
                self.assertFalse(t.is_alive())
                self.assertTrue(result_holder["result"])
                self.assertEqual(profiles.read_active_db(base_dir, data_dir), sarah)
            finally:
                t.join(timeout=1)

    def test_choose_default_clears_conf(self):
        with tempfile.TemporaryDirectory() as d:
            base_dir, data_dir = self._dirs(d)
            default_path = _touch(data_dir / "db.sqlite3")
            sarah = _touch(data_dir / "databases" / "sarah.sqlite3")
            profiles.write_active_db(sarah, base_dir, data_dir)  # start with sarah active
            port = _free_port()
            result_holder = {}

            def run():
                result_holder["result"] = bootstrap_picker.maybe_pick_profile(base_dir, data_dir, port)

            t = threading.Thread(target=run, daemon=True)
            t.start()
            try:
                url = f"http://127.0.0.1:{port}/"
                _get_with_retry(url)

                body = urllib.parse.urlencode({"path": str(default_path)}).encode()
                req = urllib.request.Request(url + "choose", data=body, method="POST")
                urllib.request.urlopen(req, timeout=5)

                t.join(timeout=5)
                self.assertTrue(result_holder["result"])
                self.assertIsNone(profiles.read_active_db(base_dir, data_dir))
            finally:
                t.join(timeout=1)

    def test_choose_unknown_path_reprompts_without_shutting_down(self):
        with tempfile.TemporaryDirectory() as d:
            base_dir, data_dir = self._dirs(d)
            _touch(data_dir / "db.sqlite3")
            _touch(data_dir / "databases" / "sarah.sqlite3")
            rogue = Path(d) / "not-a-profile.sqlite3"
            _touch(rogue)
            port = _free_port()
            result_holder = {}

            def run():
                result_holder["result"] = bootstrap_picker.maybe_pick_profile(base_dir, data_dir, port)

            t = threading.Thread(target=run, daemon=True)
            t.start()
            try:
                url = f"http://127.0.0.1:{port}/"
                _get_with_retry(url)

                body = urllib.parse.urlencode({"path": str(rogue)}).encode()
                req = urllib.request.Request(url + "choose", data=body, method="POST")
                page = urllib.request.urlopen(req, timeout=5).read().decode("utf-8")
                self.assertIn("Not a known profile", page)
                self.assertTrue(t.is_alive())  # server kept running, didn't shut down
                self.assertIsNone(profiles.read_active_db(base_dir, data_dir))

                # Clean shutdown for the test.
                sarah = data_dir / "databases" / "sarah.sqlite3"
                body = urllib.parse.urlencode({"path": str(sarah)}).encode()
                req = urllib.request.Request(url + "choose", data=body, method="POST")
                urllib.request.urlopen(req, timeout=5)
                t.join(timeout=5)
            finally:
                t.join(timeout=1)

    def test_rename_updates_listing_without_ending_the_picker(self):
        with tempfile.TemporaryDirectory() as d:
            base_dir, data_dir = self._dirs(d)
            _touch(data_dir / "db.sqlite3")
            sarah = _touch(data_dir / "databases" / "sarah.sqlite3")
            port = _free_port()
            result_holder = {}

            def run():
                result_holder["result"] = bootstrap_picker.maybe_pick_profile(base_dir, data_dir, port)

            t = threading.Thread(target=run, daemon=True)
            t.start()
            try:
                url = f"http://127.0.0.1:{port}/"
                _get_with_retry(url)

                body = urllib.parse.urlencode({"path": str(sarah), "new_name": "sarah2"}).encode()
                req = urllib.request.Request(url + "rename", data=body, method="POST")
                page = urllib.request.urlopen(req, timeout=5).read().decode("utf-8")

                self.assertIn("sarah2", page)
                self.assertFalse(sarah.exists())
                new_path = data_dir / "databases" / "sarah2.sqlite3"
                self.assertTrue(new_path.exists())
                self.assertTrue(t.is_alive())  # renaming doesn't end the picker

                body = urllib.parse.urlencode({"path": str(new_path)}).encode()
                req = urllib.request.Request(url + "choose", data=body, method="POST")
                urllib.request.urlopen(req, timeout=5)
                t.join(timeout=5)
            finally:
                t.join(timeout=1)


if __name__ == "__main__":
    unittest.main()
