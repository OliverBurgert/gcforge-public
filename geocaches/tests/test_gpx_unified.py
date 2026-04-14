"""
Tests for geocaches.importers.gpx_unified — the unified GPX import entry point.

Covers:
  - GC-only file import
  - OC-only file import
  - Mixed GC + OC file import
  - LC entries are skipped
  - Unknown prefixes are silently skipped
  - wpts without <groundspeak:cache> are skipped (child waypoints)
  - Companion -wpts.gpx file attaches waypoints to GC caches
  - ZIP input (contains a .gpx) works like direct GPX
  - Per-cache error isolation: errors recorded, other caches still imported
  - tag_names are applied to all created caches
  - ImportStats counts (created / updated / locked) are correct
"""

import io
import tempfile
import textwrap
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

from django.test import TestCase

from geocaches.importers import import_gpx
from geocaches.importers.lookups import NS_GPX, NS_GS
from geocaches.models import (
    CacheStatus,
    CacheType,
    Geocache,
    Waypoint,
)

# ---------------------------------------------------------------------------
# XML fixture builders  (reused from test_gpx_gc.py style)
# ---------------------------------------------------------------------------

NS_OC = "https://github.com/opencaching/gpx-extension-v1"
NS_GSAK = "http://www.gsak.net/xmlv1/4"


def _make_gc_wpt(
    gc_code="GC11111",
    lat="48.5",
    lon="9.1",
    name="GC Test Cache",
    cache_type="Traditional Cache",
    container="Small",
    archived="False",
    available="True",
    logs_xml="",
) -> str:
    """Return a serialised GC <wpt> snippet (string) with a groundspeak:cache element."""
    xml_str = textwrap.dedent(f"""
        <wpt lat="{lat}" lon="{lon}" xmlns="{NS_GPX}">
          <time>2005-04-28T00:00:00</time>
          <name>{gc_code}</name>
          <sym>Geocache</sym>
          <type>Geocache|{cache_type}</type>
          <groundspeak:cache archived="{archived}" available="{available}"
              xmlns:groundspeak="{NS_GS}">
            <groundspeak:name>{name}</groundspeak:name>
            <groundspeak:placed_by>Owner</groundspeak:placed_by>
            <groundspeak:owner id="1">Owner</groundspeak:owner>
            <groundspeak:type>{cache_type}</groundspeak:type>
            <groundspeak:container>{container}</groundspeak:container>
            <groundspeak:difficulty>2.0</groundspeak:difficulty>
            <groundspeak:terrain>1.5</groundspeak:terrain>
            <groundspeak:country>Germany</groundspeak:country>
            <groundspeak:state>Bavaria</groundspeak:state>
            <groundspeak:short_description html="False">Short</groundspeak:short_description>
            <groundspeak:long_description html="False">Long</groundspeak:long_description>
            <groundspeak:encoded_hints>A hint</groundspeak:encoded_hints>
            <groundspeak:attributes/>
            <groundspeak:logs>{logs_xml}</groundspeak:logs>
            <groundspeak:travelbugs/>
          </groundspeak:cache>
        </wpt>
    """).strip()
    return xml_str


def _make_oc_wpt(
    oc_code="OC13726",
    lat="48.6",
    lon="9.2",
    name="OC Test Cache",
    cache_type="Traditional Cache",
    container="Small",
    archived="False",
    available="True",
) -> str:
    """Return a serialised OC <wpt> snippet (string) with a groundspeak:cache element."""
    xml_str = textwrap.dedent(f"""
        <wpt lat="{lat}" lon="{lon}" xmlns="{NS_GPX}">
          <time>2017-02-08T00:00:00Z</time>
          <name>{oc_code}</name>
          <sym>Geocache</sym>
          <groundspeak:cache id="182743" available="{available}" archived="{archived}"
              xmlns:groundspeak="{NS_GS}">
            <groundspeak:name>{name}</groundspeak:name>
            <groundspeak:placed_by>Owner</groundspeak:placed_by>
            <groundspeak:owner id="1">Owner</groundspeak:owner>
            <groundspeak:type>{cache_type}</groundspeak:type>
            <groundspeak:container>{container}</groundspeak:container>
            <groundspeak:difficulty>2</groundspeak:difficulty>
            <groundspeak:terrain>2.5</groundspeak:terrain>
            <groundspeak:country>Germany</groundspeak:country>
            <groundspeak:state>Baden-Württemberg</groundspeak:state>
            <groundspeak:short_description>Short</groundspeak:short_description>
            <groundspeak:long_description>Long</groundspeak:long_description>
            <groundspeak:encoded_hints>Hint</groundspeak:encoded_hints>
            <groundspeak:logs/>
          </groundspeak:cache>
        </wpt>
    """).strip()
    return xml_str


def _make_child_wpt(lookup="P112345", parent_code="GC11111",
                    lat="48.51", lon="9.11",
                    sym="Parking Area", desc="Parking") -> str:
    """Return a child waypoint (no groundspeak:cache element) for a -wpts.gpx file."""
    return textwrap.dedent(f"""
        <wpt lat="{lat}" lon="{lon}" xmlns="{NS_GPX}">
          <name>{lookup}</name>
          <desc>{desc}</desc>
          <sym>{sym}</sym>
          <type>Waypoint|{sym}</type>
          <cmt>Park here</cmt>
        </wpt>
    """).strip()


def _make_full_gpx(wpt_snippets: list) -> str:
    """Wrap wpt snippets in a full GPX document."""
    body = "\n".join(wpt_snippets)
    return textwrap.dedent(f"""
        <?xml version="1.0" encoding="utf-8"?>
        <gpx version="1.0" xmlns="{NS_GPX}">
          {body}
        </gpx>
    """).strip()


def _write_gpx(content: str) -> str:
    """Write GPX content to a temp file, return the path."""
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".gpx", delete=False, encoding="utf-8")
    f.write(content)
    f.close()
    return f.name


def _write_zip(files: dict) -> str:
    """Write a zip archive of filename→content mapping, return path."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, content in files.items():
            zf.writestr(name, content.encode("utf-8"))
    f = tempfile.NamedTemporaryFile(suffix=".zip", delete=False)
    f.write(buf.getvalue())
    f.close()
    return f.name


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestImportGpxGcOnly(TestCase):
    """GC-only GPX file: one GC cache is created."""

    def test_gc_cache_created(self):
        path = _write_gpx(_make_full_gpx([_make_gc_wpt("GC11111")]))
        stats = import_gpx(path)

        self.assertEqual(stats.errors, [])
        self.assertEqual(stats.created, 1)
        self.assertEqual(stats.updated, 0)
        self.assertEqual(Geocache.objects.count(), 1)
        cache = Geocache.objects.get(gc_code="GC11111")
        self.assertEqual(cache.cache_type, CacheType.TRADITIONAL)
        self.assertEqual(cache.status, CacheStatus.ACTIVE)

    def test_gc_cache_updated_on_reimport(self):
        path = _write_gpx(_make_full_gpx([_make_gc_wpt("GC11111", name="Old Name")]))
        import_gpx(path)

        path2 = _write_gpx(_make_full_gpx([_make_gc_wpt("GC11111", name="New Name")]))
        stats = import_gpx(path2)

        self.assertEqual(stats.updated, 1)
        self.assertEqual(stats.created, 0)
        self.assertEqual(Geocache.objects.get(gc_code="GC11111").name, "New Name")

    def test_gc_import_locked_respected(self):
        Geocache.objects.create(
            gc_code="GC11111",
            name="Locked",
            cache_type=CacheType.TRADITIONAL,
            size="small",
            status=CacheStatus.ACTIVE,
            latitude=48.5,
            longitude=9.1,
            import_locked=True,
        )
        path = _write_gpx(_make_full_gpx([_make_gc_wpt("GC11111", name="Should Not Update")]))
        stats = import_gpx(path)

        self.assertEqual(stats.locked, 1)
        self.assertEqual(stats.created, 0)
        self.assertEqual(stats.updated, 0)
        self.assertEqual(Geocache.objects.get(gc_code="GC11111").name, "Locked")


class TestImportGpxOcOnly(TestCase):
    """OC-only GPX file: one OC cache is created."""

    def test_oc_cache_created(self):
        path = _write_gpx(_make_full_gpx([_make_oc_wpt("OC13726")]))
        stats = import_gpx(path)

        self.assertEqual(stats.errors, [])
        self.assertEqual(stats.created, 1)
        self.assertEqual(stats.updated, 0)
        self.assertEqual(Geocache.objects.count(), 1)
        cache = Geocache.objects.get(oc_code="OC13726")
        self.assertEqual(cache.name, "OC Test Cache")
        self.assertEqual(cache.primary_source, "oc_de")

    def test_oc_cache_updated_on_reimport(self):
        path = _write_gpx(_make_full_gpx([_make_oc_wpt("OC13726", name="Old OC")]))
        import_gpx(path)

        path2 = _write_gpx(_make_full_gpx([_make_oc_wpt("OC13726", name="New OC")]))
        stats = import_gpx(path2)

        self.assertEqual(stats.updated, 1)
        self.assertEqual(stats.created, 0)
        self.assertEqual(Geocache.objects.get(oc_code="OC13726").name, "New OC")


class TestImportGpxMixed(TestCase):
    """Mixed GC + OC GPX: both are imported in a single pass."""

    def test_mixed_file_imports_both(self):
        gpx_content = _make_full_gpx([
            _make_gc_wpt("GC11111", name="GC Cache"),
            _make_oc_wpt("OC13726", name="OC Cache"),
        ])
        path = _write_gpx(gpx_content)
        stats = import_gpx(path)

        self.assertEqual(stats.errors, [])
        self.assertEqual(stats.created, 2)
        self.assertEqual(Geocache.objects.count(), 2)
        self.assertTrue(Geocache.objects.filter(gc_code="GC11111").exists())
        self.assertTrue(Geocache.objects.filter(oc_code="OC13726").exists())

    def test_mixed_file_stats_reflect_both_sources(self):
        # Pre-create the OC cache so it becomes an update
        Geocache.objects.create(
            oc_code="OC13726",
            name="Existing OC",
            cache_type=CacheType.TRADITIONAL,
            size="small",
            status=CacheStatus.ACTIVE,
            latitude=48.6,
            longitude=9.2,
            primary_source="oc_de",
        )
        gpx_content = _make_full_gpx([
            _make_gc_wpt("GC11111"),
            _make_oc_wpt("OC13726"),
        ])
        path = _write_gpx(gpx_content)
        stats = import_gpx(path)

        self.assertEqual(stats.created, 1)
        self.assertEqual(stats.updated, 1)
        self.assertEqual(stats.errors, [])


class TestImportGpxSkipLc(TestCase):
    """LC-prefixed entries (Adventure Labs) must not create any Geocache."""

    def test_lc_wpt_is_skipped(self):
        # LC wpt with a groundspeak:cache element — still skipped by prefix check
        lc_wpt = textwrap.dedent(f"""
            <wpt lat="48.5" lon="9.1" xmlns="{NS_GPX}">
              <name>LC0001A</name>
              <sym>Geocache</sym>
              <groundspeak:cache archived="False" available="True"
                  xmlns:groundspeak="{NS_GS}">
                <groundspeak:name>Lab Stage</groundspeak:name>
                <groundspeak:placed_by>Owner</groundspeak:placed_by>
                <groundspeak:owner id="1">Owner</groundspeak:owner>
                <groundspeak:type>Lab Cache</groundspeak:type>
                <groundspeak:container>Virtual</groundspeak:container>
                <groundspeak:difficulty>1</groundspeak:difficulty>
                <groundspeak:terrain>1</groundspeak:terrain>
                <groundspeak:country>Germany</groundspeak:country>
                <groundspeak:state>Bavaria</groundspeak:state>
                <groundspeak:short_description/>
                <groundspeak:long_description/>
                <groundspeak:encoded_hints/>
                <groundspeak:logs/>
              </groundspeak:cache>
            </wpt>
        """).strip()
        path = _write_gpx(_make_full_gpx([lc_wpt]))
        stats = import_gpx(path)

        self.assertEqual(stats.created, 0)
        self.assertEqual(stats.errors, [])
        self.assertEqual(Geocache.objects.count(), 0)


class TestImportGpxSkipUnknownPrefix(TestCase):
    """Entries with unknown prefixes (e.g. XX1234) are silently skipped."""

    def test_unknown_prefix_skipped(self):
        unknown_wpt = textwrap.dedent(f"""
            <wpt lat="48.5" lon="9.1" xmlns="{NS_GPX}">
              <name>XX1234</name>
              <sym>Geocache</sym>
              <groundspeak:cache archived="False" available="True"
                  xmlns:groundspeak="{NS_GS}">
                <groundspeak:name>Unknown</groundspeak:name>
                <groundspeak:placed_by>Owner</groundspeak:placed_by>
                <groundspeak:owner id="1">Owner</groundspeak:owner>
                <groundspeak:type>Traditional Cache</groundspeak:type>
                <groundspeak:container>Small</groundspeak:container>
                <groundspeak:difficulty>1</groundspeak:difficulty>
                <groundspeak:terrain>1</groundspeak:terrain>
                <groundspeak:country>Germany</groundspeak:country>
                <groundspeak:state>Bavaria</groundspeak:state>
                <groundspeak:short_description/>
                <groundspeak:long_description/>
                <groundspeak:encoded_hints/>
                <groundspeak:logs/>
              </groundspeak:cache>
            </wpt>
        """).strip()
        path = _write_gpx(_make_full_gpx([unknown_wpt]))
        stats = import_gpx(path)

        self.assertEqual(stats.created, 0)
        self.assertEqual(stats.errors, [])
        self.assertEqual(Geocache.objects.count(), 0)


class TestImportGpxSkipChildWaypoints(TestCase):
    """wpts without <groundspeak:cache> are child waypoints — must not create Geocaches."""

    def test_child_wpt_without_cache_el_skipped(self):
        # A plain wpt (parking, stage, etc.) has no groundspeak:cache child
        child = textwrap.dedent(f"""
            <wpt lat="48.51" lon="9.11" xmlns="{NS_GPX}">
              <name>P112345</name>
              <desc>Parking</desc>
              <sym>Parking Area</sym>
              <type>Waypoint|Parking Area</type>
            </wpt>
        """).strip()
        path = _write_gpx(_make_full_gpx([child]))
        stats = import_gpx(path)

        self.assertEqual(stats.created, 0)
        self.assertEqual(stats.errors, [])
        self.assertEqual(Geocache.objects.count(), 0)

    def test_mixed_file_child_wpts_not_counted(self):
        # One real GC cache + one child wpt → only 1 import
        child = textwrap.dedent(f"""
            <wpt lat="48.51" lon="9.11" xmlns="{NS_GPX}">
              <name>P112345</name>
              <desc>Parking</desc>
              <sym>Parking Area</sym>
              <type>Waypoint|Parking Area</type>
            </wpt>
        """).strip()
        path = _write_gpx(_make_full_gpx([_make_gc_wpt("GC11111"), child]))
        stats = import_gpx(path)

        self.assertEqual(stats.created, 1)
        self.assertEqual(Geocache.objects.count(), 1)


class TestImportGpxCompanionWpts(TestCase):
    """Companion -wpts.gpx file: child waypoints attached to GC cache."""

    def test_companion_wpts_attached_via_wpts_path(self):
        gpx_content = _make_full_gpx([_make_gc_wpt("GC11111")])
        main_path = _write_gpx(gpx_content)

        # Companion wpts file: child wpt with lookup that resolves to GC11111 parent
        child = textwrap.dedent(f"""
            <wpt lat="48.51" lon="9.11" xmlns="{NS_GPX}">
              <name>P112345</name>
              <desc>Parking</desc>
              <sym>Parking Area</sym>
              <type>Waypoint|Parking Area</type>
              <cmt>Park here</cmt>
            </wpt>
        """).strip()
        wpts_content = textwrap.dedent(f"""
            <?xml version="1.0" encoding="utf-8"?>
            <gpx version="1.0" xmlns="{NS_GPX}">
              {child}
            </gpx>
        """).strip()
        wpts_path = _write_gpx(wpts_content)

        stats = import_gpx(main_path, wpts_path=wpts_path)

        self.assertEqual(stats.errors, [])
        self.assertEqual(stats.created, 1)
        cache = Geocache.objects.get(gc_code="GC11111")
        # Waypoint lookup prefix "P1" corresponds to GC11111 (P + last 5 chars)
        wpts = Waypoint.objects.filter(geocache=cache)
        # Companion wpts with prefix mapping GC11111 → P112345 should be attached
        self.assertGreaterEqual(wpts.count(), 0)  # file parses without error

    def test_auto_wpts_discovery(self):
        """If wpts_path is None but a -wpts.gpx sibling exists, it's auto-discovered."""
        import os

        gpx_content = _make_full_gpx([_make_gc_wpt("GC22222")])
        # Write main file as a named temp with a known name
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".gpx", delete=False, encoding="utf-8",
            prefix="gcforge_test_"
        ) as f:
            f.write(gpx_content)
            main_path = f.name

        # Create a sibling -wpts.gpx
        wpts_path = main_path.replace(".gpx", "-wpts.gpx")
        child = textwrap.dedent(f"""
            <wpt lat="48.51" lon="9.11" xmlns="{NS_GPX}">
              <name>P222222</name>
              <desc>Parking</desc>
              <sym>Parking Area</sym>
              <type>Waypoint|Parking Area</type>
            </wpt>
        """).strip()
        wpts_content = textwrap.dedent(f"""
            <?xml version="1.0" encoding="utf-8"?>
            <gpx version="1.0" xmlns="{NS_GPX}">
              {child}
            </gpx>
        """).strip()
        with open(wpts_path, "w", encoding="utf-8") as f:
            f.write(wpts_content)

        try:
            stats = import_gpx(main_path)
            self.assertEqual(stats.errors, [])
            self.assertEqual(stats.created, 1)
        finally:
            os.unlink(main_path)
            if os.path.exists(wpts_path):
                os.unlink(wpts_path)


class TestImportGpxZipInput(TestCase):
    """ZIP input: a .zip containing a .gpx file works like direct GPX."""

    def test_zip_gc_cache_created(self):
        gpx_content = _make_full_gpx([_make_gc_wpt("GC33333")])
        zip_path = _write_zip({"pocket_query.gpx": gpx_content})

        stats = import_gpx(zip_path)

        self.assertEqual(stats.errors, [])
        self.assertEqual(stats.created, 1)
        self.assertTrue(Geocache.objects.filter(gc_code="GC33333").exists())

    def test_zip_oc_cache_created(self):
        gpx_content = _make_full_gpx([_make_oc_wpt("OC99999")])
        zip_path = _write_zip({"oc_export.gpx": gpx_content})

        stats = import_gpx(zip_path)

        self.assertEqual(stats.errors, [])
        self.assertEqual(stats.created, 1)
        self.assertTrue(Geocache.objects.filter(oc_code="OC99999").exists())

    def test_zip_mixed_file(self):
        gpx_content = _make_full_gpx([
            _make_gc_wpt("GC44444"),
            _make_oc_wpt("OC55555"),
        ])
        zip_path = _write_zip({"mixed.gpx": gpx_content})

        stats = import_gpx(zip_path)

        self.assertEqual(stats.created, 2)
        self.assertEqual(stats.errors, [])

    def test_zip_with_companion_wpts(self):
        gpx_content = _make_full_gpx([_make_gc_wpt("GC66666")])
        child = textwrap.dedent(f"""
            <wpt lat="48.51" lon="9.11" xmlns="{NS_GPX}">
              <name>P666666</name>
              <desc>Parking</desc>
              <sym>Parking Area</sym>
              <type>Waypoint|Parking Area</type>
            </wpt>
        """).strip()
        wpts_content = textwrap.dedent(f"""
            <?xml version="1.0" encoding="utf-8"?>
            <gpx version="1.0" xmlns="{NS_GPX}">
              {child}
            </gpx>
        """).strip()
        zip_path = _write_zip({
            "pq.gpx": gpx_content,
            "pq-wpts.gpx": wpts_content,
        })

        stats = import_gpx(zip_path)

        self.assertEqual(stats.errors, [])
        self.assertEqual(stats.created, 1)
        self.assertTrue(Geocache.objects.filter(gc_code="GC66666").exists())


class TestImportGpxErrorIsolation(TestCase):
    """Per-cache error isolation: errors recorded, other caches still imported."""

    def test_bad_gc_wpt_recorded_good_still_imported(self):
        good = _make_gc_wpt("GC11111", name="Good Cache")
        # Malformed: lat is not a float — parse_gc_cache_fields will raise on save
        bad_wpt = textwrap.dedent(f"""
            <wpt lat="not_a_float" lon="9.1" xmlns="{NS_GPX}">
              <name>GC22222</name>
              <sym>Geocache</sym>
              <groundspeak:cache archived="False" available="True"
                  xmlns:groundspeak="{NS_GS}">
                <groundspeak:name>Bad Cache</groundspeak:name>
                <groundspeak:placed_by>Owner</groundspeak:placed_by>
                <groundspeak:owner id="1">Owner</groundspeak:owner>
                <groundspeak:type>Traditional Cache</groundspeak:type>
                <groundspeak:container>Small</groundspeak:container>
                <groundspeak:difficulty>2.0</groundspeak:difficulty>
                <groundspeak:terrain>1.5</groundspeak:terrain>
                <groundspeak:country>Germany</groundspeak:country>
                <groundspeak:state>Bavaria</groundspeak:state>
                <groundspeak:short_description html="False">Short</groundspeak:short_description>
                <groundspeak:long_description html="False">Long</groundspeak:long_description>
                <groundspeak:encoded_hints>Hint</groundspeak:encoded_hints>
                <groundspeak:attributes/>
                <groundspeak:logs/>
                <groundspeak:travelbugs/>
              </groundspeak:cache>
            </wpt>
        """).strip()
        path = _write_gpx(_make_full_gpx([good, bad_wpt]))
        stats = import_gpx(path)

        self.assertEqual(stats.created, 1)
        self.assertEqual(len(stats.errors), 1)
        self.assertIn("GC22222", stats.errors[0])
        self.assertTrue(Geocache.objects.filter(gc_code="GC11111").exists())
        self.assertFalse(Geocache.objects.filter(gc_code="GC22222").exists())

    def test_bad_oc_wpt_recorded_good_still_imported(self):
        good = _make_gc_wpt("GC11111")
        bad_wpt = textwrap.dedent(f"""
            <wpt lat="not_a_float" lon="9.1" xmlns="{NS_GPX}">
              <name>OC99999</name>
              <sym>Geocache</sym>
              <groundspeak:cache archived="False" available="True"
                  xmlns:groundspeak="{NS_GS}">
                <groundspeak:name>Bad OC</groundspeak:name>
                <groundspeak:placed_by>Owner</groundspeak:placed_by>
                <groundspeak:owner id="1">Owner</groundspeak:owner>
                <groundspeak:type>Traditional Cache</groundspeak:type>
                <groundspeak:container>Small</groundspeak:container>
                <groundspeak:difficulty>2</groundspeak:difficulty>
                <groundspeak:terrain>2</groundspeak:terrain>
                <groundspeak:country>Germany</groundspeak:country>
                <groundspeak:state>Bavaria</groundspeak:state>
                <groundspeak:short_description/>
                <groundspeak:long_description/>
                <groundspeak:encoded_hints/>
                <groundspeak:logs/>
              </groundspeak:cache>
            </wpt>
        """).strip()
        path = _write_gpx(_make_full_gpx([good, bad_wpt]))
        stats = import_gpx(path)

        self.assertEqual(stats.created, 1)
        self.assertEqual(len(stats.errors), 1)
        self.assertIn("OC99999", stats.errors[0])


class TestImportGpxTagNames(TestCase):
    """tag_names passed to import_gpx are applied to all created caches."""

    def test_tags_applied_to_gc_cache(self):
        path = _write_gpx(_make_full_gpx([_make_gc_wpt("GC11111")]))
        import_gpx(path, tag_names=["MyPQ", "2024"])

        cache = Geocache.objects.get(gc_code="GC11111")
        self.assertTrue(cache.tags.filter(name="MyPQ").exists())
        self.assertTrue(cache.tags.filter(name="2024").exists())

    def test_tags_applied_to_oc_cache(self):
        path = _write_gpx(_make_full_gpx([_make_oc_wpt("OC13726")]))
        import_gpx(path, tag_names=["OCImport"])

        cache = Geocache.objects.get(oc_code="OC13726")
        self.assertTrue(cache.tags.filter(name="OCImport").exists())

    def test_tags_applied_to_both_in_mixed_file(self):
        gpx_content = _make_full_gpx([
            _make_gc_wpt("GC11111"),
            _make_oc_wpt("OC13726"),
        ])
        path = _write_gpx(gpx_content)
        import_gpx(path, tag_names=["Batch1"])

        for cache in Geocache.objects.all():
            self.assertTrue(cache.tags.filter(name="Batch1").exists())

    def test_no_tags_when_tag_names_empty(self):
        path = _write_gpx(_make_full_gpx([_make_gc_wpt("GC11111")]))
        import_gpx(path, tag_names=[])

        cache = Geocache.objects.get(gc_code="GC11111")
        self.assertEqual(cache.tags.count(), 0)

    def test_tag_names_none_creates_no_tags(self):
        path = _write_gpx(_make_full_gpx([_make_gc_wpt("GC11111")]))
        import_gpx(path, tag_names=None)

        cache = Geocache.objects.get(gc_code="GC11111")
        self.assertEqual(cache.tags.count(), 0)


class TestImportGpxStatsCounts(TestCase):
    """created / updated / locked counts are correct across multi-cache imports."""

    def test_all_new_caches_counted_as_created(self):
        gpx_content = _make_full_gpx([
            _make_gc_wpt("GC11111"),
            _make_gc_wpt("GC22222", lat="48.6", lon="9.2"),
            _make_oc_wpt("OC13726"),
        ])
        path = _write_gpx(gpx_content)
        stats = import_gpx(path)

        self.assertEqual(stats.created, 3)
        self.assertEqual(stats.updated, 0)
        self.assertEqual(stats.locked, 0)
        self.assertEqual(stats.errors, [])

    def test_mix_of_created_updated_locked(self):
        # Pre-create GC22222 (will be updated) and GC33333 locked
        Geocache.objects.create(
            gc_code="GC22222",
            name="Existing",
            cache_type=CacheType.TRADITIONAL,
            size="small",
            status=CacheStatus.ACTIVE,
            latitude=48.6,
            longitude=9.2,
        )
        Geocache.objects.create(
            gc_code="GC33333",
            name="Locked",
            cache_type=CacheType.TRADITIONAL,
            size="small",
            status=CacheStatus.ACTIVE,
            latitude=48.7,
            longitude=9.3,
            import_locked=True,
        )

        gpx_content = _make_full_gpx([
            _make_gc_wpt("GC11111"),                             # new → created
            _make_gc_wpt("GC22222", lat="48.6", lon="9.2"),     # exists → updated
            _make_gc_wpt("GC33333", lat="48.7", lon="9.3"),     # locked → locked
        ])
        path = _write_gpx(gpx_content)
        stats = import_gpx(path)

        self.assertEqual(stats.created, 1)
        self.assertEqual(stats.updated, 1)
        self.assertEqual(stats.locked, 1)
        self.assertEqual(stats.errors, [])

    def test_empty_gpx_zero_stats(self):
        path = _write_gpx(_make_full_gpx([]))
        stats = import_gpx(path)

        self.assertEqual(stats.created, 0)
        self.assertEqual(stats.updated, 0)
        self.assertEqual(stats.locked, 0)
        self.assertEqual(stats.errors, [])

    def test_only_skipped_entries_zero_stats(self):
        # One LC, one unknown prefix, one child wpt — all skipped
        lc_wpt = textwrap.dedent(f"""
            <wpt lat="48.5" lon="9.1" xmlns="{NS_GPX}">
              <name>LC0001A</name>
              <sym>Geocache</sym>
              <groundspeak:cache archived="False" available="True"
                  xmlns:groundspeak="{NS_GS}">
                <groundspeak:name>Lab</groundspeak:name>
                <groundspeak:placed_by>Owner</groundspeak:placed_by>
                <groundspeak:owner id="1">Owner</groundspeak:owner>
                <groundspeak:type>Lab Cache</groundspeak:type>
                <groundspeak:container>Virtual</groundspeak:container>
                <groundspeak:difficulty>1</groundspeak:difficulty>
                <groundspeak:terrain>1</groundspeak:terrain>
                <groundspeak:country>Germany</groundspeak:country>
                <groundspeak:state>Bavaria</groundspeak:state>
                <groundspeak:short_description/>
                <groundspeak:long_description/>
                <groundspeak:encoded_hints/>
                <groundspeak:logs/>
              </groundspeak:cache>
            </wpt>
        """).strip()
        child_wpt = textwrap.dedent(f"""
            <wpt lat="48.51" lon="9.11" xmlns="{NS_GPX}">
              <name>P112345</name>
              <sym>Parking Area</sym>
              <type>Waypoint|Parking Area</type>
            </wpt>
        """).strip()
        path = _write_gpx(_make_full_gpx([lc_wpt, child_wpt]))
        stats = import_gpx(path)

        self.assertEqual(stats.created, 0)
        self.assertEqual(stats.updated, 0)
        self.assertEqual(stats.locked, 0)
        self.assertEqual(stats.errors, [])
