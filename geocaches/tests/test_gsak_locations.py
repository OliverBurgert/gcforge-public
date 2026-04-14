"""
Tests for parse_and_import_gsak_locations in geocaches/services.py.

The function reads location data from a GSAK installation directory:
  - gsak.db3  Settings table, Type='LO', Description='Locations'
  - Macros/FoundStatsSQLLite.db3  Home table with historical home positions
  - data/<db_name>/settings.ini   Per-DB centre point (CentreLat/CentreLon/CentreDes)

It returns (unique_candidates, errors, existing):
  unique_candidates — deduplicated list of dicts {name, lat, lon, source, ...}
  errors            — list of error strings (non-fatal)
  existing          — list of ReferencePoint objects currently in the database
"""

import configparser
import sqlite3
import tempfile
from pathlib import Path

from django.test import TestCase

from geocaches.coords import parse_coordinate
from geocaches.services import import_gsak_location_candidates, parse_and_import_gsak_locations
from preferences.models import ReferencePoint


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_gsak_db(path: Path, locations_data: str | None = None):
    """Create a minimal gsak.db3 with an optional LO/Locations row."""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.execute(
        "CREATE TABLE Settings (Type TEXT, Description TEXT, Data TEXT)"
    )
    if locations_data is not None:
        conn.execute(
            "INSERT INTO Settings (Type, Description, Data) VALUES (?,?,?)",
            ("LO", "Locations", locations_data),
        )
    conn.commit()
    conn.close()


def _make_fsg_db(path: Path, rows: list[dict]):
    """Create a minimal FoundStatsSQLLite.db3 with Home rows."""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.execute(
        "CREATE TABLE Home (hdate TEXT, hlat REAL, hlon REAL, hsettings INTEGER)"
    )
    for row in rows:
        conn.execute(
            "INSERT INTO Home (hdate, hlat, hlon, hsettings) VALUES (?,?,?,?)",
            (row["hdate"], row["hlat"], row["hlon"], row.get("hsettings", 1)),
        )
    conn.commit()
    conn.close()


def _make_db_ini(ini_path: Path, lat: str, lon: str, des: str = ""):
    """Write a minimal settings.ini with CentreLat/CentreLon/CentreDes."""
    ini_path.parent.mkdir(parents=True, exist_ok=True)
    cfg = configparser.ConfigParser()
    cfg["General"] = {}
    if lat:
        cfg["General"]["CentreLat"] = lat
    if lon:
        cfg["General"]["CentreLon"] = lon
    if des:
        cfg["General"]["CentreDes"] = des
    with open(str(ini_path), "w", encoding="cp1252") as f:
        cfg.write(f)


# ---------------------------------------------------------------------------
# 1. Empty / missing directory
# ---------------------------------------------------------------------------

class TestEmptyGsakDir(TestCase):

    def test_no_crash_missing_dir(self):
        """Passing a non-existent directory should not raise, just return errors."""
        candidates, errors, existing = parse_and_import_gsak_locations(
            "/tmp/__nonexistent_gsak_dir_xyz__"
        )
        self.assertEqual(candidates, [])
        # Should report that gsak.db3 was not found
        self.assertTrue(any("not found" in e.lower() or "gsak" in e.lower() for e in errors),
                        msg=f"Expected a 'not found' error, got: {errors}")

    def test_no_crash_empty_dir(self):
        """An empty directory (no gsak.db3) should return empty candidates and an error."""
        with tempfile.TemporaryDirectory() as tmpdir:
            candidates, errors, existing = parse_and_import_gsak_locations(tmpdir)
        self.assertEqual(candidates, [])
        self.assertTrue(len(errors) > 0)

    def test_empty_locations_row(self):
        """gsak.db3 exists but Settings table has no LO row → empty candidates, no crash."""
        with tempfile.TemporaryDirectory() as tmpdir:
            gsak_db = Path(tmpdir) / "gsak.db3"
            _make_gsak_db(gsak_db, locations_data=None)
            candidates, errors, existing = parse_and_import_gsak_locations(tmpdir)
        self.assertEqual(candidates, [])
        self.assertEqual(errors, [])


# ---------------------------------------------------------------------------
# 2. Settings table — LO Locations
# ---------------------------------------------------------------------------

class TestLocationsFromSettingsTable(TestCase):

    def test_decimal_coords(self):
        """Decimal-format location is parsed and returned."""
        with tempfile.TemporaryDirectory() as tmpdir:
            _make_gsak_db(
                Path(tmpdir) / "gsak.db3",
                locations_data="Home,48.5,9.1\n",
            )
            candidates, errors, _ = parse_and_import_gsak_locations(tmpdir)

        self.assertEqual(len(candidates), 1)
        c = candidates[0]
        self.assertEqual(c["name"], "Home")
        self.assertAlmostEqual(c["lat"], 48.5)
        self.assertAlmostEqual(c["lon"], 9.1)
        self.assertEqual(c["source"], "GSAK Locations")

    def test_dmm_format_coords(self):
        """DM-format location (e.g. 'N 48 30.000 E 009 06.000') is parsed correctly."""
        with tempfile.TemporaryDirectory() as tmpdir:
            _make_gsak_db(
                Path(tmpdir) / "gsak.db3",
                locations_data="Stuttgart, N 48 30.000 E 009 06.000\n",
            )
            candidates, errors, _ = parse_and_import_gsak_locations(tmpdir)

        self.assertEqual(len(candidates), 1)
        c = candidates[0]
        self.assertEqual(c["name"], "Stuttgart")
        self.assertAlmostEqual(c["lat"], 48.5, places=3)
        self.assertAlmostEqual(c["lon"], 9.1, places=3)

    def test_multiple_locations(self):
        """Multiple lines are all parsed."""
        data = "Home,48.5,9.1\nWork,48.7,9.2\n"
        with tempfile.TemporaryDirectory() as tmpdir:
            _make_gsak_db(Path(tmpdir) / "gsak.db3", locations_data=data)
            candidates, errors, _ = parse_and_import_gsak_locations(tmpdir)

        self.assertEqual(len(candidates), 2)
        names = {c["name"] for c in candidates}
        self.assertEqual(names, {"Home", "Work"})

    def test_comment_and_blank_lines_skipped(self):
        """Comment (#) and blank lines in the data are ignored."""
        data = "# This is a comment\n\nHome,48.5,9.1\n"
        with tempfile.TemporaryDirectory() as tmpdir:
            _make_gsak_db(Path(tmpdir) / "gsak.db3", locations_data=data)
            candidates, errors, _ = parse_and_import_gsak_locations(tmpdir)

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["name"], "Home")

    def test_malformed_lines_skipped(self):
        """Lines without a comma or parseable coords are skipped without crashing."""
        data = "bad line no comma\nHome,48.5,9.1\n"
        with tempfile.TemporaryDirectory() as tmpdir:
            _make_gsak_db(Path(tmpdir) / "gsak.db3", locations_data=data)
            candidates, errors, _ = parse_and_import_gsak_locations(tmpdir)

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["name"], "Home")


# ---------------------------------------------------------------------------
# 3. Per-DB ini files (data/<db>/settings.ini)
# ---------------------------------------------------------------------------

class TestCentrePointsFromIni(TestCase):

    def test_reads_centre_point(self):
        """settings.ini with CentreLat/CentreLon/CentreDes produces a candidate."""
        with tempfile.TemporaryDirectory() as tmpdir:
            _make_gsak_db(Path(tmpdir) / "gsak.db3")
            ini_path = Path(tmpdir) / "data" / "MyDB" / "settings.ini"
            _make_db_ini(ini_path, lat="48.5", lon="9.1", des="My Database Centre")
            candidates, errors, _ = parse_and_import_gsak_locations(tmpdir)

        self.assertEqual(len(candidates), 1)
        c = candidates[0]
        self.assertEqual(c["name"], "My Database Centre")
        self.assertAlmostEqual(c["lat"], 48.5)
        self.assertAlmostEqual(c["lon"], 9.1)
        self.assertIn("MyDB", c["source"])

    def test_uses_dir_name_when_des_missing(self):
        """If CentreDes is absent, the DB directory name is used as the candidate name."""
        with tempfile.TemporaryDirectory() as tmpdir:
            _make_gsak_db(Path(tmpdir) / "gsak.db3")
            ini_path = Path(tmpdir) / "data" / "FooBar" / "settings.ini"
            _make_db_ini(ini_path, lat="48.5", lon="9.1", des="")
            candidates, errors, _ = parse_and_import_gsak_locations(tmpdir)

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["name"], "FooBar")

    def test_multiple_db_dirs(self):
        """Multiple DB subdirectories produce multiple candidates."""
        with tempfile.TemporaryDirectory() as tmpdir:
            _make_gsak_db(Path(tmpdir) / "gsak.db3")
            _make_db_ini(Path(tmpdir) / "data" / "DB1" / "settings.ini",
                         lat="48.5", lon="9.1", des="Centre 1")
            _make_db_ini(Path(tmpdir) / "data" / "DB2" / "settings.ini",
                         lat="49.0", lon="10.0", des="Centre 2")
            candidates, errors, _ = parse_and_import_gsak_locations(tmpdir)

        self.assertEqual(len(candidates), 2)

    def test_ini_without_coords_skipped(self):
        """An ini file with missing lat/lon produces no candidate."""
        with tempfile.TemporaryDirectory() as tmpdir:
            _make_gsak_db(Path(tmpdir) / "gsak.db3")
            ini_path = Path(tmpdir) / "data" / "Empty" / "settings.ini"
            _make_db_ini(ini_path, lat="", lon="", des="No Coords")
            candidates, errors, _ = parse_and_import_gsak_locations(tmpdir)

        self.assertEqual(candidates, [])


# ---------------------------------------------------------------------------
# 4. Deduplication
# ---------------------------------------------------------------------------

class TestDeduplication(TestCase):

    def test_same_location_from_two_sources(self):
        """The same lat/lon/name from Settings and an ini file → single candidate."""
        with tempfile.TemporaryDirectory() as tmpdir:
            _make_gsak_db(
                Path(tmpdir) / "gsak.db3",
                locations_data="MyPlace,48.5,9.1\n",
            )
            _make_db_ini(
                Path(tmpdir) / "data" / "DB1" / "settings.ini",
                lat="48.5000", lon="9.1000", des="MyPlace",
            )
            candidates, errors, _ = parse_and_import_gsak_locations(tmpdir)

        # Deduplication rounds to 4 decimal places; both map to same key
        self.assertEqual(len(candidates), 1)

    def test_different_names_same_coords_not_deduped(self):
        """Different names at the same coords are both kept."""
        with tempfile.TemporaryDirectory() as tmpdir:
            _make_gsak_db(
                Path(tmpdir) / "gsak.db3",
                locations_data="Home,48.5,9.1\n",
            )
            _make_db_ini(
                Path(tmpdir) / "data" / "DB1" / "settings.ini",
                lat="48.5", lon="9.1", des="Office",
            )
            candidates, errors, _ = parse_and_import_gsak_locations(tmpdir)

        # Different names → different dedup keys → both survive
        self.assertEqual(len(candidates), 2)

    def test_case_insensitive_name_dedup(self):
        """Names differing only in case are treated as duplicates."""
        with tempfile.TemporaryDirectory() as tmpdir:
            _make_gsak_db(
                Path(tmpdir) / "gsak.db3",
                locations_data="home,48.5,9.1\n",
            )
            _make_db_ini(
                Path(tmpdir) / "data" / "DB1" / "settings.ini",
                lat="48.5", lon="9.1", des="Home",
            )
            candidates, errors, _ = parse_and_import_gsak_locations(tmpdir)

        self.assertEqual(len(candidates), 1)


# ---------------------------------------------------------------------------
# 5. Already-existing ReferencePoints
# ---------------------------------------------------------------------------

class TestAlreadyExistingLocations(TestCase):

    def test_matching_existing_flagged(self):
        """A candidate whose name+coords match an existing ReferencePoint is flagged."""
        ReferencePoint.objects.create(name="Home", latitude=48.5, longitude=9.1)

        with tempfile.TemporaryDirectory() as tmpdir:
            _make_gsak_db(
                Path(tmpdir) / "gsak.db3",
                locations_data="Home,48.5,9.1\n",
            )
            candidates, errors, existing = parse_and_import_gsak_locations(tmpdir)

        self.assertEqual(len(candidates), 1)
        self.assertTrue(candidates[0]["already_exists"])
        self.assertEqual(len(existing), 1)

    def test_non_matching_not_flagged(self):
        """A candidate with no matching ReferencePoint has already_exists=False."""
        ReferencePoint.objects.create(name="Work", latitude=49.0, longitude=10.0)

        with tempfile.TemporaryDirectory() as tmpdir:
            _make_gsak_db(
                Path(tmpdir) / "gsak.db3",
                locations_data="Home,48.5,9.1\n",
            )
            candidates, errors, existing = parse_and_import_gsak_locations(tmpdir)

        self.assertEqual(len(candidates), 1)
        self.assertFalse(candidates[0]["already_exists"])

    def test_name_only_match_flagged(self):
        """A candidate whose name matches an existing point (different coords) is also flagged."""
        ReferencePoint.objects.create(name="Home", latitude=50.0, longitude=11.0)

        with tempfile.TemporaryDirectory() as tmpdir:
            _make_gsak_db(
                Path(tmpdir) / "gsak.db3",
                locations_data="Home,48.5,9.1\n",
            )
            candidates, errors, _ = parse_and_import_gsak_locations(tmpdir)

        self.assertTrue(candidates[0]["already_exists"])

    def test_existing_list_returned(self):
        """The existing list contains the ReferencePoint objects currently in the DB."""
        rp = ReferencePoint.objects.create(name="Known", latitude=48.5, longitude=9.1)

        with tempfile.TemporaryDirectory() as tmpdir:
            _make_gsak_db(Path(tmpdir) / "gsak.db3")
            _, _, existing = parse_and_import_gsak_locations(tmpdir)

        self.assertIn(rp, existing)


# ---------------------------------------------------------------------------
# 6. Coordinate format parsing (parse_coordinate)
# ---------------------------------------------------------------------------

class TestParseCoordinate(TestCase):
    """Unit tests for geocaches.coords.parse_coordinate used internally."""

    def test_plain_decimal(self):
        self.assertAlmostEqual(parse_coordinate("48.5"), 48.5)
        self.assertAlmostEqual(parse_coordinate("9.1"), 9.1)

    def test_negative_decimal(self):
        self.assertAlmostEqual(parse_coordinate("-48.5"), -48.5)

    def test_dmm_prefix_n(self):
        # N 48 30.000 → 48 + 30/60 = 48.5
        result = parse_coordinate("N 48 30.000")
        self.assertAlmostEqual(result, 48.5, places=4)

    def test_dmm_prefix_e(self):
        # E 009 06.000 → 9 + 6/60 = 9.1
        result = parse_coordinate("E 009 06.000")
        self.assertAlmostEqual(result, 9.1, places=4)

    def test_dmm_prefix_s_is_negative(self):
        result = parse_coordinate("S 48 30.000")
        self.assertAlmostEqual(result, -48.5, places=4)

    def test_dmm_prefix_w_is_negative(self):
        result = parse_coordinate("W 009 06.000")
        self.assertAlmostEqual(result, -9.1, places=4)

    def test_dmm_with_symbols(self):
        result = parse_coordinate("N 48° 18.189'")
        self.assertAlmostEqual(result, 48 + 18.189 / 60, places=4)

    def test_dms_with_symbols(self):
        result = parse_coordinate("N 48° 18' 11.34\"")
        self.assertAlmostEqual(result, 48 + 18 / 60 + 11.34 / 3600, places=4)

    def test_hemisphere_suffix(self):
        result = parse_coordinate("48 18.189 N")
        self.assertAlmostEqual(result, 48 + 18.189 / 60, places=4)

    def test_empty_string_returns_none(self):
        self.assertIsNone(parse_coordinate(""))

    def test_garbage_returns_none(self):
        self.assertIsNone(parse_coordinate("not_a_coord"))


# ---------------------------------------------------------------------------
# 7. FindStatGen3 macro DB — Home history
# ---------------------------------------------------------------------------

class TestFindStatGenHomeHistory(TestCase):

    def test_home_candidate_from_fsg_db(self):
        """A Home row in FoundStatsSQLLite.db3 produces a candidate with is_home=True."""
        with tempfile.TemporaryDirectory() as tmpdir:
            _make_gsak_db(Path(tmpdir) / "gsak.db3")
            fsg_path = Path(tmpdir) / "Macros" / "FoundStatsSQLLite.db3"
            _make_fsg_db(fsg_path, rows=[
                {"hdate": "2023-01-15", "hlat": 48.5, "hlon": 9.1, "hsettings": 1},
            ])
            candidates, errors, _ = parse_and_import_gsak_locations(tmpdir)

        self.assertEqual(len(candidates), 1)
        c = candidates[0]
        self.assertTrue(c.get("is_home"), msg="Candidate should have is_home=True")
        self.assertAlmostEqual(c["lat"], 48.5)
        self.assertAlmostEqual(c["lon"], 9.1)
        self.assertIn("2023-01-15", c["name"])
        self.assertEqual(c["source"], "FindStatGen home history")

    def test_home_valid_from_field(self):
        """The valid_from field on the Home candidate is the date string."""
        with tempfile.TemporaryDirectory() as tmpdir:
            _make_gsak_db(Path(tmpdir) / "gsak.db3")
            fsg_path = Path(tmpdir) / "Macros" / "FoundStatsSQLLite.db3"
            _make_fsg_db(fsg_path, rows=[
                {"hdate": "2020-06-01", "hlat": 48.5, "hlon": 9.1, "hsettings": 1},
            ])
            candidates, errors, _ = parse_and_import_gsak_locations(tmpdir)

        self.assertEqual(candidates[0].get("valid_from"), "2020-06-01")

    def test_multiple_home_rows(self):
        """Multiple Home rows with hsettings=1 each produce a candidate."""
        with tempfile.TemporaryDirectory() as tmpdir:
            _make_gsak_db(Path(tmpdir) / "gsak.db3")
            fsg_path = Path(tmpdir) / "Macros" / "FoundStatsSQLLite.db3"
            _make_fsg_db(fsg_path, rows=[
                {"hdate": "2020-06-01", "hlat": 48.5,  "hlon": 9.1, "hsettings": 1},
                {"hdate": "2022-03-10", "hlat": 48.7,  "hlon": 9.3, "hsettings": 1},
            ])
            candidates, errors, _ = parse_and_import_gsak_locations(tmpdir)

        self.assertEqual(len(candidates), 2)
        self.assertTrue(all(c.get("is_home") for c in candidates))

    def test_invalid_coords_in_fsg_skipped(self):
        """Home rows with non-numeric or out-of-range coords are silently skipped."""
        with tempfile.TemporaryDirectory() as tmpdir:
            _make_gsak_db(Path(tmpdir) / "gsak.db3")
            fsg_path = Path(tmpdir) / "Macros" / "FoundStatsSQLLite.db3"
            # Out-of-range lat
            fsg_path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(str(fsg_path))
            conn.execute(
                "CREATE TABLE Home (hdate TEXT, hlat REAL, hlon REAL, hsettings INTEGER)"
            )
            conn.execute("INSERT INTO Home VALUES ('2023-01-01', 999.0, 9.1, 1)")
            conn.commit()
            conn.close()
            candidates, errors, _ = parse_and_import_gsak_locations(tmpdir)

        self.assertEqual(candidates, [])


# ---------------------------------------------------------------------------
# 8. Import step — creating ReferencePoint objects
# ---------------------------------------------------------------------------

class TestImportStep(TestCase):
    """Verify the view-level import logic (creating ReferencePoint from candidates)."""

    def _do_import(self, candidates: list[dict]) -> list[ReferencePoint]:
        """Simulate the import loop from views.import_gsak_locations POST handler."""
        created = []
        for c in candidates:
            rp = ReferencePoint.objects.create(
                name=c["name"],
                latitude=c["lat"],
                longitude=c["lon"],
                note=c["source"],
                valid_from=c.get("valid_from"),
                is_home=c.get("is_home", False),
            )
            created.append(rp)
        return created

    def test_creates_reference_point(self):
        """Importing a candidate creates a ReferencePoint with correct fields."""
        with tempfile.TemporaryDirectory() as tmpdir:
            _make_gsak_db(
                Path(tmpdir) / "gsak.db3",
                locations_data="Depot,48.5,9.1\n",
            )
            candidates, _, _ = parse_and_import_gsak_locations(tmpdir)

        rp_list = self._do_import(candidates)

        self.assertEqual(len(rp_list), 1)
        rp = ReferencePoint.objects.get(name="Depot")
        self.assertAlmostEqual(rp.latitude, 48.5)
        self.assertAlmostEqual(rp.longitude, 9.1)
        self.assertEqual(rp.note, "GSAK Locations")
        self.assertFalse(rp.is_home)

    def test_home_candidate_sets_is_home(self):
        """Importing a FindStatGen Home candidate sets is_home=True on the ReferencePoint."""
        with tempfile.TemporaryDirectory() as tmpdir:
            _make_gsak_db(Path(tmpdir) / "gsak.db3")
            fsg_path = Path(tmpdir) / "Macros" / "FoundStatsSQLLite.db3"
            _make_fsg_db(fsg_path, rows=[
                {"hdate": "2023-01-15", "hlat": 48.5, "hlon": 9.1, "hsettings": 1},
            ])
            candidates, _, _ = parse_and_import_gsak_locations(tmpdir)

        rp_list = self._do_import(candidates)

        self.assertEqual(len(rp_list), 1)
        rp = rp_list[0]
        self.assertTrue(rp.is_home)
        self.assertEqual(str(rp.valid_from), "2023-01-15")

    def test_import_multiple_candidates(self):
        """Multiple candidates all become ReferencePoint objects."""
        with tempfile.TemporaryDirectory() as tmpdir:
            _make_gsak_db(
                Path(tmpdir) / "gsak.db3",
                locations_data="Alpha,48.5,9.1\nBeta,49.0,10.0\n",
            )
            candidates, _, _ = parse_and_import_gsak_locations(tmpdir)

        self._do_import(candidates)

        self.assertEqual(ReferencePoint.objects.count(), 2)
        names = set(ReferencePoint.objects.values_list("name", flat=True))
        self.assertEqual(names, {"Alpha", "Beta"})

    def test_ini_centre_point_source_field(self):
        """An ini-derived candidate has its DB directory name in the source/note field."""
        with tempfile.TemporaryDirectory() as tmpdir:
            _make_gsak_db(Path(tmpdir) / "gsak.db3")
            _make_db_ini(
                Path(tmpdir) / "data" / "MyDB" / "settings.ini",
                lat="48.5", lon="9.1", des="Centre",
            )
            candidates, _, _ = parse_and_import_gsak_locations(tmpdir)

        rp_list = self._do_import(candidates)

        rp = rp_list[0]
        self.assertIn("MyDB", rp.note)


# ---------------------------------------------------------------------------
# 9. Mixed sources — all three read at once
# ---------------------------------------------------------------------------

class TestMixedSources(TestCase):

    def test_all_three_sources_combined(self):
        """Settings table, FSG db, and ini file are all read in one call."""
        with tempfile.TemporaryDirectory() as tmpdir:
            _make_gsak_db(
                Path(tmpdir) / "gsak.db3",
                locations_data="GsakLoc,48.1,9.0\n",
            )
            _make_fsg_db(
                Path(tmpdir) / "Macros" / "FoundStatsSQLLite.db3",
                rows=[{"hdate": "2021-07-01", "hlat": 48.2, "hlon": 9.2, "hsettings": 1}],
            )
            _make_db_ini(
                Path(tmpdir) / "data" / "DB1" / "settings.ini",
                lat="48.3", lon="9.3", des="IniCentre",
            )
            candidates, errors, _ = parse_and_import_gsak_locations(tmpdir)

        self.assertEqual(errors, [])
        self.assertEqual(len(candidates), 3)
        names = {c["name"] for c in candidates}
        self.assertIn("GsakLoc", names)
        self.assertIn("IniCentre", names)
        # Home candidate names contain the date
        home_candidates = [c for c in candidates if c.get("is_home")]
        self.assertEqual(len(home_candidates), 1)

    def test_errors_do_not_prevent_other_sources(self):
        """If FSG db is corrupt, Settings and ini sources still work."""
        # ignore_cleanup_errors avoids PermissionError on Windows when sqlite3
        # holds a file lock on the corrupt (non-sqlite) file during cleanup.
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
            _make_gsak_db(
                Path(tmpdir) / "gsak.db3",
                locations_data="GsakLoc,48.1,9.0\n",
            )
            # Write a corrupt (non-sqlite) FSG db
            fsg_path = Path(tmpdir) / "Macros" / "FoundStatsSQLLite.db3"
            fsg_path.parent.mkdir(parents=True, exist_ok=True)
            fsg_path.write_text("not a sqlite database")

            _make_db_ini(
                Path(tmpdir) / "data" / "DB1" / "settings.ini",
                lat="48.3", lon="9.3", des="IniCentre",
            )
            candidates, errors, _ = parse_and_import_gsak_locations(tmpdir)

        # Must have at least the Settings and ini candidates
        names = {c["name"] for c in candidates}
        self.assertIn("GsakLoc", names)
        self.assertIn("IniCentre", names)
        # Must report the FSG error
        self.assertTrue(any("FindStatGen" in e or "FoundStat" in e for e in errors),
                        msg=f"Expected FSG error in {errors}")


# ---------------------------------------------------------------------------
# 10. import_gsak_location_candidates — service layer
# ---------------------------------------------------------------------------

class TestImportGsakLocationCandidates(TestCase):
    """Tests for import_gsak_location_candidates (the service-layer import step)."""

    def _candidate(self, name="Test Loc", lat=48.5, lon=9.1,
                   source="GSAK Locations", is_home=False, valid_from=None):
        c = {"name": name, "lat": lat, "lon": lon, "source": source, "is_home": is_home}
        if valid_from is not None:
            c["valid_from"] = valid_from
        return c

    def test_creates_reference_point(self):
        c = self._candidate("MySpot", lat=48.5, lon=9.1)
        imported = import_gsak_location_candidates([c])
        self.assertEqual(imported, ["MySpot"])
        rp = ReferencePoint.objects.get(name="MySpot")
        self.assertAlmostEqual(rp.latitude, 48.5)
        self.assertAlmostEqual(rp.longitude, 9.1)

    def test_note_field_set_from_source(self):
        c = self._candidate(source="DB centre: MyDB")
        import_gsak_location_candidates([c])
        rp = ReferencePoint.objects.get(name="Test Loc")
        self.assertEqual(rp.note, "DB centre: MyDB")

    def test_is_home_flag(self):
        c = self._candidate(name="Home (from 2023-01-01)", is_home=True, valid_from="2023-01-01")
        import_gsak_location_candidates([c])
        rp = ReferencePoint.objects.get(name="Home (from 2023-01-01)")
        self.assertTrue(rp.is_home)
        self.assertEqual(str(rp.valid_from), "2023-01-01")

    def test_is_home_false_by_default(self):
        c = self._candidate()
        import_gsak_location_candidates([c])
        rp = ReferencePoint.objects.get(name="Test Loc")
        self.assertFalse(rp.is_home)

    def test_multiple_candidates_all_created(self):
        candidates = [
            self._candidate("Spot A", lat=48.1, lon=9.1),
            self._candidate("Spot B", lat=48.2, lon=9.2),
            self._candidate("Spot C", lat=48.3, lon=9.3),
        ]
        imported = import_gsak_location_candidates(candidates)
        self.assertEqual(imported, ["Spot A", "Spot B", "Spot C"])
        self.assertEqual(ReferencePoint.objects.count(), 3)

    def test_empty_list_returns_empty(self):
        imported = import_gsak_location_candidates([])
        self.assertEqual(imported, [])
        self.assertEqual(ReferencePoint.objects.count(), 0)

    def test_valid_from_none_when_not_in_candidate(self):
        c = self._candidate()  # no valid_from key
        import_gsak_location_candidates([c])
        rp = ReferencePoint.objects.get(name="Test Loc")
        self.assertIsNone(rp.valid_from)


# ---------------------------------------------------------------------------
# 11. End-to-end: parse → select → import
# ---------------------------------------------------------------------------

class TestEndToEndWorkflow(TestCase):
    """
    Full workflow: build a mock GSAK directory, parse candidates, select a
    subset, import via import_gsak_location_candidates, verify the DB.
    """

    def test_full_workflow_from_settings_table(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
            _make_gsak_db(
                Path(tmpdir) / "gsak.db3",
                locations_data="Home Base,48.5 9.1\nSecret Spot,47.9 8.7\n",
            )
            candidates, errors, _ = parse_and_import_gsak_locations(tmpdir)

        self.assertFalse(errors)
        self.assertEqual(len(candidates), 2)

        # User selects only the first candidate
        imported = import_gsak_location_candidates([candidates[0]])
        self.assertEqual(len(imported), 1)
        self.assertEqual(ReferencePoint.objects.count(), 1)
        rp = ReferencePoint.objects.first()
        self.assertEqual(rp.name, candidates[0]["name"])

    def test_full_workflow_with_home_history(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
            _make_gsak_db(Path(tmpdir) / "gsak.db3")
            _make_fsg_db(
                Path(tmpdir) / "Macros" / "FoundStatsSQLLite.db3",
                rows=[
                    {"hdate": "2022-03-15", "hlat": 48.1, "hlon": 9.0, "hsettings": 1},
                    {"hdate": "2023-06-01", "hlat": 48.5, "hlon": 9.5, "hsettings": 1},
                ],
            )
            candidates, errors, _ = parse_and_import_gsak_locations(tmpdir)

        self.assertFalse(errors)
        home_candidates = [c for c in candidates if c.get("is_home")]
        self.assertEqual(len(home_candidates), 2)

        # Import all home candidates
        imported = import_gsak_location_candidates(home_candidates)
        self.assertEqual(len(imported), 2)
        self.assertEqual(ReferencePoint.objects.filter(is_home=True).count(), 2)

    def test_full_workflow_mixed_sources_selective_import(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
            _make_gsak_db(
                Path(tmpdir) / "gsak.db3",
                locations_data="SettingsLoc,48.0 9.0\n",
            )
            _make_fsg_db(
                Path(tmpdir) / "Macros" / "FoundStatsSQLLite.db3",
                rows=[{"hdate": "2023-01-01", "hlat": 48.5, "hlon": 9.5, "hsettings": 1}],
            )
            _make_db_ini(
                Path(tmpdir) / "data" / "Germany" / "settings.ini",
                lat="48.3", lon="9.3", des="Germany Centre",
            )
            candidates, errors, _ = parse_and_import_gsak_locations(tmpdir)

        self.assertFalse(errors)
        self.assertEqual(len(candidates), 3)

        # Import only the ini-sourced candidate
        ini_candidates = [c for c in candidates if "Germany Centre" in c["name"]]
        import_gsak_location_candidates(ini_candidates)

        self.assertEqual(ReferencePoint.objects.count(), 1)
        rp = ReferencePoint.objects.first()
        self.assertEqual(rp.name, "Germany Centre")
        self.assertIn("Germany", rp.note)

    def test_already_existing_flag_does_not_block_import(self):
        """already_exists is advisory — the service layer does not enforce it."""
        ReferencePoint.objects.create(name="Existing", latitude=48.5, longitude=9.1)
        c = {"name": "Existing", "lat": 48.5, "lon": 9.1,
             "source": "GSAK Locations", "already_exists": True, "is_home": False}
        # Service doesn't check already_exists — that's the view's responsibility
        imported = import_gsak_location_candidates([c])
        self.assertEqual(imported, ["Existing"])
        self.assertEqual(ReferencePoint.objects.filter(name="Existing").count(), 2)


# ---------------------------------------------------------------------------
# 12. View — Post/Redirect/Get behaviour
# ---------------------------------------------------------------------------

class TestImportGsakLocationsView(TestCase):
    """
    Tests for the import_gsak_locations view's PRG behaviour.
    Uses unittest.mock to avoid needing a real GSAK installation.
    """

    def setUp(self):
        from django.contrib.auth import get_user_model
        User = get_user_model()
        self.user = User.objects.create_superuser("admin", "", "admin")
        self.client.force_login(self.user)

    def _mock_candidates(self):
        return [
            {"name": "Spot A", "lat": 48.1, "lon": 9.1,
             "source": "GSAK Locations", "is_home": False, "already_exists": False},
            {"name": "Spot B", "lat": 48.2, "lon": 9.2,
             "source": "GSAK Locations", "is_home": False, "already_exists": False},
        ]

    def test_post_redirects(self):
        """POST should redirect to the same page (PRG pattern)."""
        from unittest.mock import patch
        candidates = self._mock_candidates()
        with patch("geocaches.services.parse_and_import_gsak_locations",
                   return_value=(candidates, [], [])):
            response = self.client.post(
                "/import/gsak-locations/",
                data={"loc_idx": ["0"]},
            )
        self.assertEqual(response.status_code, 302)
        self.assertIn("gsak-locations", response["Location"])

    def test_post_creates_reference_point_and_redirects(self):
        """POST imports the selected candidate then redirects."""
        from unittest.mock import patch
        candidates = self._mock_candidates()
        with patch("geocaches.services.parse_and_import_gsak_locations",
                   return_value=(candidates, [], [])):
            self.client.post(
                "/import/gsak-locations/",
                data={"loc_idx": ["0"]},
            )
        self.assertEqual(ReferencePoint.objects.count(), 1)
        self.assertEqual(ReferencePoint.objects.first().name, "Spot A")

    def test_get_after_post_shows_imported_message(self):
        """After PRG, the GET shows the imported names from the session."""
        from unittest.mock import patch
        candidates = self._mock_candidates()
        with patch("geocaches.services.parse_and_import_gsak_locations",
                   return_value=(candidates, [], [])):
            self.client.post(
                "/import/gsak-locations/",
                data={"loc_idx": ["1"]},
            )
            response = self.client.get("/import/gsak-locations/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Spot B", response.context["imported"])

    def test_imported_message_shown_only_once(self):
        """The imported list is popped from the session — second GET sees nothing."""
        from unittest.mock import patch
        candidates = self._mock_candidates()
        with patch("geocaches.services.parse_and_import_gsak_locations",
                   return_value=(candidates, [], [])):
            self.client.post(
                "/import/gsak-locations/",
                data={"loc_idx": ["0"]},
            )
            self.client.get("/import/gsak-locations/")   # consumes session key
            response = self.client.get("/import/gsak-locations/")
        self.assertEqual(response.context["imported"], [])

    def test_get_without_prior_post_shows_empty_imported(self):
        """Plain GET with no prior POST shows an empty imported list."""
        from unittest.mock import patch
        with patch("geocaches.services.parse_and_import_gsak_locations",
                   return_value=([], [], [])):
            response = self.client.get("/import/gsak-locations/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["imported"], [])
