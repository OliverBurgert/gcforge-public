"""
Tests for geocaches.importers.gpx_gc — parser functions and DB save logic.
"""

import io
import tempfile
import textwrap
import xml.etree.ElementTree as ET
import zipfile
from datetime import date
from pathlib import Path

from django.test import TestCase

from geocaches.importers.gpx_gc import (
    ImportStats,
    _load_roots_from_zip,
    _parse_float,
    _parse_wpts_root,
    parse_attributes,
    parse_cache_fields,
    parse_logs,
    parse_wpts_gpx,
    save_geocache,
)
from geocaches.importers.lookups import NS_GPX, NS_GS, gpx, gs
from geocaches.models import (
    Attribute,
    CacheSize,
    CacheStatus,
    CacheType,
    Geocache,
    Log,
    LogType,
    Tag,
    Waypoint,
    WaypointType,
)


# ---------------------------------------------------------------------------
# XML fixture builders
# ---------------------------------------------------------------------------

def _make_wpt(
    gc_code="GC12345",
    lat="48.5",
    lon="9.1",
    time="2005-04-28T00:00:00",
    sym="Geocache",
    cache_type="Traditional Cache",
    container="Small",
    archived="False",
    available="True",
    name="Test Cache",
    owner="TestOwner",
    placed_by="TestOwner",
    difficulty="2.0",
    terrain="1.5",
    country="Germany",
    state="Baden-Württemberg",
    short_desc="Short",
    long_desc="Long",
    hint="A hint",
    logs_xml="",
    attrs_xml="",
    fav_points="",
    travelbugs_xml="",
) -> tuple[ET.Element, ET.Element]:
    """Return (wpt_el, cache_el) parsed from a minimal GPX wpt snippet."""
    fav_el = f"<groundspeak:favorite_points>{fav_points}</groundspeak:favorite_points>" if fav_points else ""
    xml_str = textwrap.dedent(f"""
        <wpt lat="{lat}" lon="{lon}" xmlns="{NS_GPX}">
          <time>{time}</time>
          <name>{gc_code}</name>
          <sym>{sym}</sym>
          <type>Geocache|{cache_type}</type>
          <groundspeak:cache archived="{archived}" available="{available}"
              xmlns:groundspeak="{NS_GS}">
            <groundspeak:name>{name}</groundspeak:name>
            <groundspeak:placed_by>{placed_by}</groundspeak:placed_by>
            <groundspeak:owner id="1">{owner}</groundspeak:owner>
            <groundspeak:type>{cache_type}</groundspeak:type>
            <groundspeak:container>{container}</groundspeak:container>
            <groundspeak:difficulty>{difficulty}</groundspeak:difficulty>
            <groundspeak:terrain>{terrain}</groundspeak:terrain>
            <groundspeak:country>{country}</groundspeak:country>
            <groundspeak:state>{state}</groundspeak:state>
            <groundspeak:short_description html="False">{short_desc}</groundspeak:short_description>
            <groundspeak:long_description html="False">{long_desc}</groundspeak:long_description>
            <groundspeak:encoded_hints>{hint}</groundspeak:encoded_hints>
            {fav_el}
            <groundspeak:attributes>{attrs_xml}</groundspeak:attributes>
            <groundspeak:logs>{logs_xml}</groundspeak:logs>
            <groundspeak:travelbugs>{travelbugs_xml}</groundspeak:travelbugs>
          </groundspeak:cache>
        </wpt>
    """).strip()
    wpt_el = ET.fromstring(xml_str)
    cache_el = wpt_el.find(f"{{{NS_GS}}}cache")
    return wpt_el, cache_el


def _make_log_xml(log_id="111", log_type="Found it", date_str="2023-01-15T19:00:00Z",
                  finder="Finder1", text="Nice!"):
    return textwrap.dedent(f"""
        <groundspeak:log id="{log_id}" xmlns:groundspeak="{NS_GS}">
          <groundspeak:date>{date_str}</groundspeak:date>
          <groundspeak:type>{log_type}</groundspeak:type>
          <groundspeak:finder id="1">{finder}</groundspeak:finder>
          <groundspeak:text encoded="False">{text}</groundspeak:text>
        </groundspeak:log>
    """).strip()


def _make_attr_xml(attr_id="1", inc="1", name="Dogs"):
    return f'<groundspeak:attribute id="{attr_id}" inc="{inc}" xmlns:groundspeak="{NS_GS}">{name}</groundspeak:attribute>'


def _make_full_gpx(wpt_snippets: list[str]) -> str:
    body = "\n".join(wpt_snippets)
    return textwrap.dedent(f"""
        <?xml version="1.0" encoding="utf-8"?>
        <gpx version="1.0" xmlns="{NS_GPX}">
          {body}
        </gpx>
    """).strip()


def _make_wpt_xml(lookup="P112345", lat="48.5", lon="9.1",
                  desc="Parking", sym="Parking Area", cmt="Park here"):
    return textwrap.dedent(f"""
        <wpt lat="{lat}" lon="{lon}" xmlns="{NS_GPX}">
          <name>{lookup}</name>
          <desc>{desc}</desc>
          <sym>{sym}</sym>
          <type>Waypoint|{sym}</type>
          <cmt>{cmt}</cmt>
        </wpt>
    """).strip()


def _make_wpts_gpx(wpt_snippets: list[str]) -> str:
    body = "\n".join(wpt_snippets)
    return textwrap.dedent(f"""
        <?xml version="1.0" encoding="utf-8"?>
        <gpx version="1.0" xmlns="{NS_GPX}">
          {body}
        </gpx>
    """).strip()


# ---------------------------------------------------------------------------
# Tests: parse_cache_fields
# ---------------------------------------------------------------------------

class TestParseCacheFields(TestCase):
    def test_basic_fields(self):
        wpt_el, cache_el = _make_wpt()
        fields = parse_cache_fields(wpt_el, cache_el)

        self.assertEqual(fields["gc_code"], "GC12345")
        self.assertEqual(fields["name"], "Test Cache")
        self.assertEqual(fields["owner"], "TestOwner")
        self.assertAlmostEqual(fields["latitude"], 48.5)
        self.assertAlmostEqual(fields["longitude"], 9.1)
        self.assertEqual(fields["hidden_date"], date(2005, 4, 28))
        self.assertEqual(fields["country"], "Germany")
        self.assertEqual(fields["state"], "Baden-Württemberg")
        self.assertEqual(fields["hint"], "A hint")
        self.assertEqual(fields["short_description"], "Short")
        self.assertEqual(fields["long_description"], "Long")

    def test_cache_type_mapped(self):
        wpt_el, cache_el = _make_wpt(cache_type="Traditional Cache")
        fields = parse_cache_fields(wpt_el, cache_el)
        self.assertEqual(fields["cache_type"], CacheType.TRADITIONAL)

    def test_unknown_cache_type_maps_to_mystery(self):
        wpt_el, cache_el = _make_wpt(cache_type="Unknown Cache")
        fields = parse_cache_fields(wpt_el, cache_el)
        self.assertEqual(fields["cache_type"], CacheType.MYSTERY)

    def test_size_mapped(self):
        wpt_el, cache_el = _make_wpt(container="Micro")
        fields = parse_cache_fields(wpt_el, cache_el)
        self.assertEqual(fields["size"], CacheSize.MICRO)

    def test_status_active(self):
        wpt_el, cache_el = _make_wpt(archived="False", available="True")
        self.assertEqual(parse_cache_fields(wpt_el, cache_el)["status"], CacheStatus.ACTIVE)

    def test_status_disabled(self):
        wpt_el, cache_el = _make_wpt(archived="False", available="False")
        self.assertEqual(parse_cache_fields(wpt_el, cache_el)["status"], CacheStatus.DISABLED)

    def test_status_archived(self):
        wpt_el, cache_el = _make_wpt(archived="True", available="False")
        self.assertEqual(parse_cache_fields(wpt_el, cache_el)["status"], CacheStatus.ARCHIVED)

    def test_difficulty_and_terrain(self):
        wpt_el, cache_el = _make_wpt(difficulty="3.5", terrain="4.0")
        fields = parse_cache_fields(wpt_el, cache_el)
        self.assertAlmostEqual(fields["difficulty"], 3.5)
        self.assertAlmostEqual(fields["terrain"], 4.0)

    def test_fav_points(self):
        wpt_el, cache_el = _make_wpt(fav_points="42")
        self.assertEqual(parse_cache_fields(wpt_el, cache_el)["fav_points"], 42)

    def test_fav_points_missing_defaults_to_zero(self):
        wpt_el, cache_el = _make_wpt(fav_points="")
        self.assertEqual(parse_cache_fields(wpt_el, cache_el)["fav_points"], 0)

    def test_has_trackable_true(self):
        wpt_el, cache_el = _make_wpt(travelbugs_xml='<groundspeak:travelbug id="1" xmlns:groundspeak="http://www.groundspeak.com/cache/1/0/1"/>')
        self.assertTrue(parse_cache_fields(wpt_el, cache_el)["has_trackable"])

    def test_has_trackable_false(self):
        wpt_el, cache_el = _make_wpt(travelbugs_xml="")
        self.assertFalse(parse_cache_fields(wpt_el, cache_el)["has_trackable"])

    def test_html_entity_decoding_in_owner(self):
        wpt_el, cache_el = _make_wpt(owner="T&#252;-K&#228;scher")
        fields = parse_cache_fields(wpt_el, cache_el)
        self.assertEqual(fields["owner"], "Tü-Käscher")

    def test_placed_by_extracted(self):
        wpt_el, cache_el = _make_wpt(owner="AccountName", placed_by="TeamDisplayName")
        fields = parse_cache_fields(wpt_el, cache_el)
        self.assertEqual(fields["placed_by"], "TeamDisplayName")
        self.assertEqual(fields["owner"], "AccountName")

    def test_owner_gc_id_extracted(self):
        wpt_el, cache_el = _make_wpt()
        fields = parse_cache_fields(wpt_el, cache_el)
        self.assertEqual(fields["owner_gc_id"], 1)

    def test_found_false_when_sym_is_geocache(self):
        wpt_el, cache_el = _make_wpt(sym="Geocache")
        fields = parse_cache_fields(wpt_el, cache_el)
        self.assertNotIn("found", fields)

    def test_found_true_when_sym_is_geocache_found(self):
        wpt_el, cache_el = _make_wpt(sym="Geocache Found")
        fields = parse_cache_fields(wpt_el, cache_el)
        self.assertTrue(fields.get("found"))


# ---------------------------------------------------------------------------
# Tests: parse_logs
# ---------------------------------------------------------------------------

class TestParseLogs(TestCase):
    def _get_cache_el_with_logs(self, *log_xmls):
        logs_body = "\n".join(log_xmls)
        _, cache_el = _make_wpt(logs_xml=logs_body)
        return cache_el

    def test_single_found_log(self):
        log_xml = _make_log_xml(log_id="100", log_type="Found it",
                                date_str="2023-06-01T00:00:00Z", finder="Alice")
        cache_el = self._get_cache_el_with_logs(log_xml)
        logs = parse_logs(cache_el)

        self.assertEqual(len(logs), 1)
        self.assertEqual(logs[0]["source_id"], "100")
        self.assertEqual(logs[0]["log_type"], LogType.FOUND)
        self.assertEqual(logs[0]["user_name"], "Alice")
        self.assertEqual(logs[0]["logged_date"], date(2023, 6, 1))

    def test_multiple_logs(self):
        log1 = _make_log_xml(log_id="1", log_type="Found it", date_str="2023-01-01T00:00:00Z")
        log2 = _make_log_xml(log_id="2", log_type="Didn't find it", date_str="2022-12-01T00:00:00Z")
        cache_el = self._get_cache_el_with_logs(log1, log2)
        logs = parse_logs(cache_el)
        self.assertEqual(len(logs), 2)

    def test_log_with_invalid_date_is_skipped(self):
        log_xml = _make_log_xml(log_id="99", date_str="not-a-date")
        cache_el = self._get_cache_el_with_logs(log_xml)
        self.assertEqual(parse_logs(cache_el), [])

    def test_no_logs_element_returns_empty(self):
        # Build cache_el manually without a logs element
        xml_str = f"""<groundspeak:cache xmlns:groundspeak="{NS_GS}"/>"""
        cache_el = ET.fromstring(xml_str)
        self.assertEqual(parse_logs(cache_el), [])

    def test_unknown_log_type_falls_back_to_note(self):
        log_xml = _make_log_xml(log_type="Mystery Log Type")
        _, cache_el = _make_wpt(logs_xml=log_xml)
        logs = parse_logs(cache_el)
        self.assertEqual(logs[0]["log_type"], LogType.NOTE)


# ---------------------------------------------------------------------------
# Tests: parse_attributes
# ---------------------------------------------------------------------------

class TestParseAttributes(TestCase):
    def test_positive_attribute(self):
        attr_xml = _make_attr_xml(attr_id="1", inc="1", name="Dogs")
        _, cache_el = _make_wpt(attrs_xml=attr_xml)
        attrs = parse_attributes(cache_el)
        self.assertEqual(len(attrs), 1)
        self.assertEqual(attrs[0], (1, "Dogs", True))

    def test_negative_attribute(self):
        attr_xml = _make_attr_xml(attr_id="2", inc="0", name="No Dogs")
        _, cache_el = _make_wpt(attrs_xml=attr_xml)
        attrs = parse_attributes(cache_el)
        self.assertEqual(attrs[0], (2, "No Dogs", False))

    def test_multiple_attributes(self):
        attrs_xml = (
            _make_attr_xml("1", "1", "Dogs") +
            _make_attr_xml("25", "1", "Parking nearby")
        )
        _, cache_el = _make_wpt(attrs_xml=attrs_xml)
        self.assertEqual(len(parse_attributes(cache_el)), 2)

    def test_no_attributes_element_returns_empty(self):
        xml_str = f"""<groundspeak:cache xmlns:groundspeak="{NS_GS}"/>"""
        cache_el = ET.fromstring(xml_str)
        self.assertEqual(parse_attributes(cache_el), [])

    def test_invalid_attr_id_is_skipped(self):
        xml_str = f"""
            <groundspeak:cache xmlns:groundspeak="{NS_GS}">
              <groundspeak:attributes>
                <groundspeak:attribute id="bad" inc="1">Dogs</groundspeak:attribute>
              </groundspeak:attributes>
            </groundspeak:cache>
        """
        cache_el = ET.fromstring(xml_str.strip())
        self.assertEqual(parse_attributes(cache_el), [])


# ---------------------------------------------------------------------------
# Tests: parse_wpts_gpx
# ---------------------------------------------------------------------------

class TestParseWptsGpx(TestCase):
    def _write_wpts_file(self, content: str) -> str:
        f = tempfile.NamedTemporaryFile(mode="w", suffix=".gpx", delete=False, encoding="utf-8")
        f.write(content)
        f.close()
        return f.name

    def test_single_waypoint_linked_to_parent(self):
        wpt_xml = _make_wpt_xml(lookup="P112345", lat="48.5", lon="9.1",
                                desc="Parking", sym="Parking Area", cmt="Park here")
        gpx_content = _make_wpts_gpx([wpt_xml])
        path = self._write_wpts_file(gpx_content)

        result = parse_wpts_gpx(path)

        self.assertIn("GC12345", result)
        self.assertEqual(len(result["GC12345"]), 1)
        wpt = result["GC12345"][0]
        self.assertEqual(wpt["lookup"], "P112345")
        self.assertEqual(wpt["prefix"], "P1")
        self.assertEqual(wpt["name"], "Parking")
        self.assertEqual(wpt["waypoint_type"], WaypointType.PARKING)
        self.assertAlmostEqual(wpt["latitude"], 48.5)
        self.assertAlmostEqual(wpt["longitude"], 9.1)
        self.assertEqual(wpt["note"], "Park here")
        self.assertFalse(wpt["is_user_created"])

    def test_multiple_waypoints_grouped_by_parent(self):
        wpt1 = _make_wpt_xml(lookup="P112345", sym="Parking Area")
        wpt2 = _make_wpt_xml(lookup="S112345", sym="Physical Stage")
        wpt3 = _make_wpt_xml(lookup="P167890", sym="Parking Area")
        gpx_content = _make_wpts_gpx([wpt1, wpt2, wpt3])
        path = self._write_wpts_file(gpx_content)

        result = parse_wpts_gpx(path)

        self.assertEqual(len(result["GC12345"]), 2)
        self.assertEqual(len(result["GC67890"]), 1)

    def test_waypoint_with_short_name_is_skipped(self):
        wpt_xml = _make_wpt_xml(lookup="AB")  # only 2 chars — skipped
        gpx_content = _make_wpts_gpx([wpt_xml])
        path = self._write_wpts_file(gpx_content)
        self.assertEqual(parse_wpts_gpx(path), {})

    def test_final_waypoint_type(self):
        wpt_xml = _make_wpt_xml(lookup="FL12345", sym="Final Location")
        path = self._write_wpts_file(_make_wpts_gpx([wpt_xml]))
        result = parse_wpts_gpx(path)
        self.assertEqual(result["GC12345"][0]["waypoint_type"], WaypointType.FINAL)


# ---------------------------------------------------------------------------
# Tests: save_geocache (DB)
# ---------------------------------------------------------------------------

class TestSaveGeocache(TestCase):
    def _basic_fields(self, gc_code="GC99999"):
        return {
            "gc_code": gc_code,
            "name": "Test Cache",
            "owner": "Owner",
            "cache_type": CacheType.TRADITIONAL,
            "size": CacheSize.SMALL,
            "status": CacheStatus.ACTIVE,
            "latitude": 48.5,
            "longitude": 9.1,
            "difficulty": 2.0,
            "terrain": 1.5,
            "short_description": "",
            "long_description": "",
            "hint": "",
            "hidden_date": date(2020, 1, 1),
            "country": "Germany",
            "state": "BW",
            "fav_points": 5,
            "has_trackable": False,
        }

    def test_creates_new_geocache(self):
        _, created, locked = save_geocache(self._basic_fields(), [], [], [])
        self.assertTrue(created)
        self.assertFalse(locked)
        self.assertEqual(Geocache.objects.count(), 1)

    def test_updates_existing_geocache(self):
        save_geocache(self._basic_fields(), [], [], [])
        fields = self._basic_fields()
        fields["name"] = "Updated Name"
        _, created, locked = save_geocache(fields, [], [], [])
        self.assertFalse(created)
        self.assertFalse(locked)
        self.assertEqual(Geocache.objects.get(gc_code="GC99999").name, "Updated Name")

    def test_skips_import_locked_cache(self):
        Geocache.objects.create(
            gc_code="GC99999",
            name="Original",
            cache_type=CacheType.TRADITIONAL,
            size=CacheSize.SMALL,
            status=CacheStatus.ACTIVE,
            latitude=48.5,
            longitude=9.1,
            import_locked=True,
        )
        fields = self._basic_fields()
        fields["name"] = "Should Not Update"
        _, created, locked = save_geocache(fields, [], [], [])
        self.assertFalse(created)
        self.assertTrue(locked)
        self.assertEqual(Geocache.objects.get(gc_code="GC99999").name, "Original")

    def test_applies_tag(self):
        tag = Tag.objects.create(name="MyPQ")
        save_geocache(self._basic_fields(), [], [], [], tags=[tag])
        cache = Geocache.objects.get(gc_code="GC99999")
        self.assertIn(tag, cache.tags.all())

    def test_creates_logs(self):
        logs = [
            {"source_id": "1", "log_type": LogType.FOUND,
             "user_name": "Alice", "logged_date": date(2023, 1, 1), "text": ""},
        ]
        save_geocache(self._basic_fields(), logs, [], [])
        self.assertEqual(Log.objects.count(), 1)

    def test_deduplicates_logs_by_source_id(self):
        logs = [
            {"source_id": "1", "log_type": LogType.FOUND,
             "user_name": "Alice", "logged_date": date(2023, 1, 1), "text": ""},
        ]
        save_geocache(self._basic_fields(), logs, [], [])
        # Second import with same log
        save_geocache(self._basic_fields(), logs, [], [])
        self.assertEqual(Log.objects.count(), 1)

    def test_derives_last_found_date_from_logs(self):
        logs = [
            {"source_id": "1", "log_type": LogType.FOUND,
             "user_name": "A", "logged_date": date(2022, 6, 1), "text": ""},
            {"source_id": "2", "log_type": LogType.FOUND,
             "user_name": "B", "logged_date": date(2023, 3, 15), "text": ""},
            {"source_id": "3", "log_type": LogType.DNF,
             "user_name": "C", "logged_date": date(2023, 6, 1), "text": ""},
        ]
        save_geocache(self._basic_fields(), logs, [], [])
        cache = Geocache.objects.get(gc_code="GC99999")
        self.assertEqual(cache.last_found_date, date(2023, 3, 15))

    def test_creates_attributes(self):
        attrs = [(1, "Dogs", True), (2, "No Dogs", False)]
        save_geocache(self._basic_fields(), [], attrs, [])
        cache = Geocache.objects.get(gc_code="GC99999")
        self.assertEqual(cache.attributes.count(), 2)

    def test_reuses_existing_attributes(self):
        Attribute.objects.create(source=Attribute.Source.GC, attribute_id=1,
                                 is_positive=True, name="Dogs")
        attrs = [(1, "Dogs", True)]
        save_geocache(self._basic_fields(), [], attrs, [])
        self.assertEqual(Attribute.objects.count(), 1)

    def test_creates_waypoints(self):
        wpts = [{"lookup": "P199999", "prefix": "P1", "name": "Parking",
                 "waypoint_type": WaypointType.PARKING, "latitude": 48.5,
                 "longitude": 9.1, "note": "", "is_user_created": False}]
        save_geocache(self._basic_fields(), [], [], wpts)
        self.assertEqual(Waypoint.objects.count(), 1)

    def test_sets_platform_log_count(self):
        logs = [
            {"source_id": str(i), "log_type": LogType.FOUND,
             "user_name": "X", "logged_date": date(2023, 1, i + 1), "text": ""}
            for i in range(3)
        ]
        save_geocache(self._basic_fields(), logs, [], [])
        self.assertEqual(Geocache.objects.get(gc_code="GC99999").platform_log_count, 3)

    def test_found_true_sets_found_flag_and_date(self):
        fields = {**self._basic_fields(), "found": True}
        logs = [{"source_id": "1", "log_type": LogType.FOUND,
                 "user_name": "Me", "logged_date": date(2023, 5, 20), "text": ""}]
        save_geocache(fields, logs, [], [])
        cache = Geocache.objects.get(gc_code="GC99999")
        self.assertTrue(cache.found)
        self.assertEqual(cache.found_date, date(2023, 5, 20))

    def test_found_not_demoted_on_regular_pq_update(self):
        """Re-importing a regular PQ (no found flag) must not reset found=True."""
        Geocache.objects.create(
            gc_code="GC99999", name="Found Cache", cache_type=CacheType.TRADITIONAL,
            size=CacheSize.SMALL, status=CacheStatus.ACTIVE,
            latitude=48.5, longitude=9.1,
            found=True, found_date=date(2023, 1, 1),
        )
        # Regular PQ import has no "found" key in cache_fields
        save_geocache(self._basic_fields(), [], [], [])
        cache = Geocache.objects.get(gc_code="GC99999")
        self.assertTrue(cache.found)
        self.assertEqual(cache.found_date, date(2023, 1, 1))

    def test_found_date_not_overwritten_if_already_set(self):
        """Re-importing My Finds PQ must not overwrite an existing found_date."""
        Geocache.objects.create(
            gc_code="GC99999", name="Found Cache", cache_type=CacheType.TRADITIONAL,
            size=CacheSize.SMALL, status=CacheStatus.ACTIVE,
            latitude=48.5, longitude=9.1,
            found=True, found_date=date(2020, 6, 15),
        )
        fields = {**self._basic_fields(), "found": True}
        logs = [{"source_id": "1", "log_type": LogType.FOUND,
                 "user_name": "Me", "logged_date": date(2023, 5, 20), "text": ""}]
        save_geocache(fields, logs, [], [])
        # Original found_date should be preserved
        self.assertEqual(Geocache.objects.get(gc_code="GC99999").found_date, date(2020, 6, 15))


# ---------------------------------------------------------------------------
# Tests: import_gc_gpx (integration)
# ---------------------------------------------------------------------------

class TestImportGcGpx(TestCase):
    def _write_gpx(self, content: str) -> str:
        f = tempfile.NamedTemporaryFile(mode="w", suffix=".gpx", delete=False, encoding="utf-8")
        f.write(content)
        f.close()
        return f.name

    def _single_cache_gpx(self, gc_code="GC11111", **kwargs):
        wpt_el, cache_el = _make_wpt(gc_code=gc_code, **kwargs)
        import xml.etree.ElementTree as ET
        wpt_str = ET.tostring(wpt_el, encoding="unicode")
        return _make_full_gpx([wpt_str])

    def test_creates_cache_from_gpx(self):
        path = self._write_gpx(self._single_cache_gpx())
        stats = __import__("geocaches.importers", fromlist=["import_gc_gpx"]).import_gc_gpx(path)
        self.assertEqual(stats.created, 1)
        self.assertEqual(stats.updated, 0)
        self.assertEqual(Geocache.objects.count(), 1)

    def test_updates_cache_on_reimport(self):
        path = self._write_gpx(self._single_cache_gpx(name="Old Name"))
        from geocaches.importers import import_gc_gpx
        import_gc_gpx(path)
        path2 = self._write_gpx(self._single_cache_gpx(name="New Name"))
        stats = import_gc_gpx(path2)
        self.assertEqual(stats.updated, 1)
        self.assertEqual(Geocache.objects.get(gc_code="GC11111").name, "New Name")

    def test_respects_import_locked(self):
        from geocaches.importers import import_gc_gpx
        Geocache.objects.create(
            gc_code="GC11111", name="Locked", cache_type=CacheType.TRADITIONAL,
            size=CacheSize.SMALL, status=CacheStatus.ACTIVE,
            latitude=48.5, longitude=9.1, import_locked=True,
        )
        path = self._write_gpx(self._single_cache_gpx(name="Should Not Update"))
        stats = import_gc_gpx(path)
        self.assertEqual(stats.locked, 1)
        self.assertEqual(Geocache.objects.get(gc_code="GC11111").name, "Locked")

    def test_applies_tag_names(self):
        from geocaches.importers import import_gc_gpx
        path = self._write_gpx(self._single_cache_gpx())
        import_gc_gpx(path, tag_names=["MyPQ", "2024"])
        cache = Geocache.objects.get(gc_code="GC11111")
        self.assertTrue(cache.tags.filter(name="MyPQ").exists())
        self.assertTrue(cache.tags.filter(name="2024").exists())

    def test_skips_non_gc_waypoints(self):
        from geocaches.importers import import_gc_gpx
        # A wpt without a GC code prefix should be skipped
        non_gc = textwrap.dedent(f"""
            <wpt lat="48.5" lon="9.1" xmlns="{NS_GPX}">
              <name>OCABC12</name>
              <type>Geocache|Traditional Cache</type>
            </wpt>
        """).strip()
        path = self._write_gpx(_make_full_gpx([non_gc]))
        stats = import_gc_gpx(path)
        self.assertEqual(stats.created, 0)
        self.assertEqual(Geocache.objects.count(), 0)

    def test_import_stats_str(self):
        stats = ImportStats(created=3, updated=1, locked=0)
        self.assertIn("created=3", str(stats))
        self.assertIn("updated=1", str(stats))

    def test_error_does_not_stop_other_caches(self):
        from geocaches.importers import import_gc_gpx
        # Two caches: second has no cache_el so it will error or be skipped.
        # Use two valid caches but corrupt one by having invalid lat
        good = ET.tostring(_make_wpt(gc_code="GC11111")[0], encoding="unicode")
        bad_str = textwrap.dedent(f"""
            <wpt lat="not_a_float" lon="9.1" xmlns="{NS_GPX}">
              <name>GC22222</name>
              <groundspeak:cache archived="False" available="True"
                  xmlns:groundspeak="{NS_GS}">
                <groundspeak:name>Bad</groundspeak:name>
                <groundspeak:type>Traditional Cache</groundspeak:type>
                <groundspeak:container>Small</groundspeak:container>
                <groundspeak:logs/>
                <groundspeak:attributes/>
                <groundspeak:travelbugs/>
              </groundspeak:cache>
            </wpt>
        """).strip()
        path = self._write_gpx(_make_full_gpx([good, bad_str]))
        stats = import_gc_gpx(path)
        self.assertEqual(stats.created, 1)
        self.assertEqual(len(stats.errors), 1)


# ---------------------------------------------------------------------------
# Tests: zip file handling
# ---------------------------------------------------------------------------

class TestZipImport(TestCase):
    def _make_zip(self, files: dict[str, str]) -> str:
        """Write a zip archive containing the given filename→content mapping, return path."""
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            for name, content in files.items():
                zf.writestr(name, content.encode("utf-8"))
        f = tempfile.NamedTemporaryFile(suffix=".zip", delete=False)
        f.write(buf.getvalue())
        f.close()
        return f.name

    def _single_cache_gpx(self, gc_code="GC11111", sym="Geocache", **kwargs):
        wpt_el, _ = _make_wpt(gc_code=gc_code, sym=sym, **kwargs)
        wpt_str = ET.tostring(wpt_el, encoding="unicode")
        return _make_full_gpx([wpt_str])

    def test_load_roots_from_zip_returns_main_root(self):
        gpx_content = self._single_cache_gpx()
        zip_path = self._make_zip({"mycaches.gpx": gpx_content})
        main_root, wpts_root = _load_roots_from_zip(Path(zip_path))
        self.assertIsNotNone(main_root)
        self.assertIsNone(wpts_root)

    def test_load_roots_from_zip_finds_wpts(self):
        gpx_content = self._single_cache_gpx()
        wpts_content = _make_wpts_gpx([_make_wpt_xml()])
        zip_path = self._make_zip({
            "mycaches.gpx": gpx_content,
            "mycaches-wpts.gpx": wpts_content,
        })
        main_root, wpts_root = _load_roots_from_zip(Path(zip_path))
        self.assertIsNotNone(main_root)
        self.assertIsNotNone(wpts_root)

    def test_load_roots_from_zip_raises_if_no_gpx(self):
        zip_path = self._make_zip({"readme.txt": "nothing"})
        with self.assertRaises(ValueError):
            _load_roots_from_zip(Path(zip_path))

    def test_import_gc_gpx_accepts_zip(self):
        from geocaches.importers import import_gc_gpx
        gpx_content = self._single_cache_gpx(gc_code="GC77777")
        zip_path = self._make_zip({"pq.gpx": gpx_content})
        stats = import_gc_gpx(zip_path)
        self.assertEqual(stats.created, 1)
        self.assertEqual(Geocache.objects.count(), 1)

    def test_import_gc_gpx_zip_with_found_sym_sets_found(self):
        from geocaches.importers import import_gc_gpx
        log_xml = _make_log_xml(log_id="42", log_type="Found it",
                                date_str="2024-07-10T12:00:00Z", finder="Me")
        gpx_content = self._single_cache_gpx(
            gc_code="GC88888", sym="Geocache Found", logs_xml=log_xml
        )
        zip_path = self._make_zip({"myfinds.gpx": gpx_content})
        import_gc_gpx(zip_path)
        cache = Geocache.objects.get(gc_code="GC88888")
        self.assertTrue(cache.found)
        self.assertEqual(cache.found_date, date(2024, 7, 10))


# ---------------------------------------------------------------------------
# Tests: _parse_float helper
# ---------------------------------------------------------------------------

class TestParseFloat(TestCase):
    def test_valid_float(self):
        self.assertAlmostEqual(_parse_float("3.5"), 3.5)

    def test_integer_string(self):
        self.assertAlmostEqual(_parse_float("2"), 2.0)

    def test_empty_string_returns_none(self):
        self.assertIsNone(_parse_float(""))

    def test_invalid_string_returns_none(self):
        self.assertIsNone(_parse_float("abc"))

    def test_none_returns_none(self):
        self.assertIsNone(_parse_float(None))
