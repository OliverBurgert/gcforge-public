import json
import tempfile
from pathlib import Path

from django.test import TestCase


class FileBrowseViewTests(TestCase):

    def test_browse_home_directory(self):
        resp = self.client.get("/browse/")
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.content)
        self.assertIn("current", data)
        self.assertIn("entries", data)
        self.assertEqual(data["current"], str(Path.home()))

    def test_browse_with_dir_param(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a test file
            (Path(tmpdir) / "test.gpx").write_text("<gpx/>")
            (Path(tmpdir) / "subdir").mkdir()

            resp = self.client.get("/browse/", {"dir": tmpdir})
            self.assertEqual(resp.status_code, 200)
            data = json.loads(resp.content)
            self.assertEqual(data["current"], str(Path(tmpdir).resolve()))
            names = [e["name"] for e in data["entries"]]
            self.assertIn("test.gpx", names)
            self.assertIn("subdir", names)

    def test_browse_extension_filter(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "test.gpx").write_text("<gpx/>")
            (Path(tmpdir) / "test.txt").write_text("hello")
            (Path(tmpdir) / "subdir").mkdir()

            resp = self.client.get("/browse/", {"dir": tmpdir, "ext": ".gpx"})
            data = json.loads(resp.content)
            names = [e["name"] for e in data["entries"]]
            self.assertIn("test.gpx", names)
            self.assertNotIn("test.txt", names)
            # Directories always shown
            self.assertIn("subdir", names)

    def test_browse_nonexistent_path(self):
        resp = self.client.get("/browse/", {"dir": "C:\\nonexistent_path_xyz"})
        self.assertEqual(resp.status_code, 400)
        data = json.loads(resp.content)
        self.assertIn("error", data)

    def test_browse_entries_sorted_dirs_first(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "zebra.gpx").write_text("<gpx/>")
            (Path(tmpdir) / "alpha.gpx").write_text("<gpx/>")
            (Path(tmpdir) / "beta_dir").mkdir()
            (Path(tmpdir) / "alpha_dir").mkdir()

            resp = self.client.get("/browse/", {"dir": tmpdir, "ext": ".gpx"})
            data = json.loads(resp.content)
            # Skip ".." entry
            entries = [e for e in data["entries"] if e["name"] != ".."]
            # First entries should be directories
            dir_entries = [e for e in entries if e["is_dir"]]
            file_entries = [e for e in entries if not e["is_dir"]]
            self.assertEqual(dir_entries[0]["name"], "alpha_dir")
            self.assertEqual(dir_entries[1]["name"], "beta_dir")
            self.assertEqual(file_entries[0]["name"], "alpha.gpx")
            self.assertEqual(file_entries[1]["name"], "zebra.gpx")

    def test_browse_file_has_size(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "test.gpx").write_text("x" * 100)
            resp = self.client.get("/browse/", {"dir": tmpdir, "ext": ".gpx"})
            data = json.loads(resp.content)
            file_entry = next(e for e in data["entries"] if e["name"] == "test.gpx")
            self.assertEqual(file_entry["size"], 100)
            self.assertFalse(file_entry["is_dir"])

    def test_browse_parent_directory(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            subdir = Path(tmpdir) / "child"
            subdir.mkdir()
            resp = self.client.get("/browse/", {"dir": str(subdir)})
            data = json.loads(resp.content)
            self.assertEqual(data["parent"], str(Path(tmpdir).resolve()))
            # ".." entry should point to parent
            parent_entry = next(e for e in data["entries"] if e["name"] == "..")
            self.assertEqual(parent_entry["path"], str(Path(tmpdir).resolve()))
