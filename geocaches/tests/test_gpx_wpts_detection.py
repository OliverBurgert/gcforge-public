"""
Tests for companion -wpts.gpx auto-detection in the GPX import flow.
"""

import os
import tempfile
from pathlib import Path
from unittest.mock import patch

from django.test import TestCase

from geocaches.views import _derive_wpts_path, _is_wpts_file


class IsWptsFileTests(TestCase):

    def test_standard_wpts_filename(self):
        self.assertTrue(_is_wpts_file("6588060-wpts.gpx"))

    def test_uppercase_wpts_filename(self):
        self.assertTrue(_is_wpts_file("6588060-WPTS.GPX"))

    def test_mixed_case_wpts_filename(self):
        self.assertTrue(_is_wpts_file("MyPQ-Wpts.Gpx"))

    def test_regular_gpx_filename(self):
        self.assertFalse(_is_wpts_file("6588060.gpx"))

    def test_zip_filename(self):
        self.assertFalse(_is_wpts_file("6588060.zip"))

    def test_full_path_wpts(self):
        self.assertTrue(_is_wpts_file("C:\\Downloads\\6588060-wpts.gpx"))

    def test_full_path_regular(self):
        self.assertFalse(_is_wpts_file("C:\\Downloads\\6588060.gpx"))


class DeriveWptsPathTests(TestCase):

    def test_companion_found(self):
        with tempfile.TemporaryDirectory() as td:
            main = Path(td) / "6588060.gpx"
            wpts = Path(td) / "6588060-wpts.gpx"
            main.write_text("<gpx/>")
            wpts.write_text("<gpx/>")
            result = _derive_wpts_path(str(main))
            self.assertEqual(result, str(wpts))

    def test_companion_not_found(self):
        with tempfile.TemporaryDirectory() as td:
            main = Path(td) / "6588060.gpx"
            main.write_text("<gpx/>")
            result = _derive_wpts_path(str(main))
            self.assertIsNone(result)

    def test_non_gpx_file(self):
        with tempfile.TemporaryDirectory() as td:
            main = Path(td) / "6588060.zip"
            main.write_text("")
            result = _derive_wpts_path(str(main))
            self.assertIsNone(result)

    def test_wpts_file_as_input(self):
        """Deriving from a -wpts.gpx file should not find a -wpts-wpts.gpx."""
        with tempfile.TemporaryDirectory() as td:
            wpts = Path(td) / "6588060-wpts.gpx"
            wpts.write_text("<gpx/>")
            result = _derive_wpts_path(str(wpts))
            self.assertIsNone(result)
