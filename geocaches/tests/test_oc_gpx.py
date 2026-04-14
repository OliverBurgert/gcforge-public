"""
Tests for OC GPX format detection and importer (geocaches.importers.gpx_oc).
"""

import tempfile
import textwrap
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

from django.test import TestCase

from geocaches.importers.detect import detect_gpx_format
from geocaches.importers.gpx_oc import (
    NS_GSAK,
    NS_OC,
    extract_oc_attribution,
    import_oc_gpx,
    parse_oc_cache_fields,
    parse_oc_extension,
    parse_oc_inline_waypoints,
    parse_oc_logs,
)
from geocaches.importers.lookups import NS_GPX, NS_GS, gpx, gs
from geocaches.models import (
    CacheSize,
    CacheStatus,
    CacheType,
    Geocache,
    Log,
    LogType,
    OCExtension,
    Waypoint,
    WaypointType,
)
from geocaches.services import save_geocache

# ---------------------------------------------------------------------------
# Shared XML snippets
# ---------------------------------------------------------------------------

MINIMAL_GC_GPX = textwrap.dedent("""\
    <?xml version="1.0" encoding="utf-8"?>
    <gpx xmlns="http://www.topografix.com/GPX/1/0"
         creator="Groundspeak" version="1.0">
    </gpx>
""")

MINIMAL_OC_GPX = textwrap.dedent("""\
    <?xml version="1.0" encoding="utf-8"?>
    <gpx xmlns="http://www.topografix.com/GPX/1/0"
         creator="Opencaching.de - https://www.opencaching.de/" version="1.0">
    </gpx>
""")


def _full_oc_gpx(
    oc_code="OC13726",
    name="Test Cache",
    cache_type="Traditional Cache",
    container="Small",
    lat="48.5",
    lon="9.1",
    difficulty="2",
    terrain="2.5",
    archived="False",
    available="True",
    owner="Owner",
    country="Germany",
    state="Baden-Württemberg",
    trip_time="0.5",
    req_passwd="false",
    other_code="GC70A3H",
    logs_xml="",
    extra_wpts="",
):
    """Build a complete OC GPX string with one cache."""
    oc_ext = ""
    oc_parts = []
    if trip_time:
        oc_parts.append(f"      <oc:trip_time>{trip_time}</oc:trip_time>")
    if req_passwd:
        oc_parts.append(f"      <oc:requires_password>{req_passwd}</oc:requires_password>")
    if other_code:
        oc_parts.append(f"      <oc:other_code>{other_code}</oc:other_code>")
    if oc_parts:
        oc_ext = (
            '    <oc:cache xmlns:oc="https://github.com/opencaching/gpx-extension-v1">\n'
            + "\n".join(oc_parts)
            + "\n    </oc:cache>"
        )

    lines = [
        '<?xml version="1.0" encoding="utf-8"?>',
        '<gpx xmlns="http://www.topografix.com/GPX/1/0"',
        '     creator="Opencaching.de - https://www.opencaching.de/" version="1.0">',
        f'  <wpt lat="{lat}" lon="{lon}">',
        '    <time>2017-02-08T00:00:00Z</time>',
        f'    <name>{oc_code}</name>',
        '    <sym>Geocache</sym>',
        f'    <groundspeak:cache id="182743" available="{available}" archived="{archived}"',
        '        xmlns:groundspeak="http://www.groundspeak.com/cache/1/0/1">',
        f'      <groundspeak:name>{name}</groundspeak:name>',
        f'      <groundspeak:placed_by>{owner}</groundspeak:placed_by>',
        f'      <groundspeak:owner id="1">{owner}</groundspeak:owner>',
        f'      <groundspeak:type>{cache_type}</groundspeak:type>',
        f'      <groundspeak:container>{container}</groundspeak:container>',
        f'      <groundspeak:difficulty>{difficulty}</groundspeak:difficulty>',
        f'      <groundspeak:terrain>{terrain}</groundspeak:terrain>',
        f'      <groundspeak:country>{country}</groundspeak:country>',
        f'      <groundspeak:state>{state}</groundspeak:state>',
        '      <groundspeak:short_description>Short</groundspeak:short_description>',
        '      <groundspeak:long_description>Long</groundspeak:long_description>',
        '      <groundspeak:encoded_hints>Hint</groundspeak:encoded_hints>',
        f'      <groundspeak:logs>{logs_xml}</groundspeak:logs>',
        '    </groundspeak:cache>',
    ]
    if oc_ext:
        lines.append(oc_ext)
    lines.append('  </wpt>')
    if extra_wpts:
        lines.append(extra_wpts)
    lines.append('</gpx>')
    return "\n".join(lines)


def _write_tmp(content: str, suffix=".gpx") -> str:
    """Write content to a temp file and return its path."""
    f = tempfile.NamedTemporaryFile(mode="w", suffix=suffix, delete=False, encoding="utf-8")
    f.write(content)
    f.close()
    return f.name


# ===================================================================
# 1. Format detection tests
# ===================================================================


class DetectFormatTests(TestCase):

    def test_detect_gc_format(self):
        path = _write_tmp(MINIMAL_GC_GPX)
        self.assertEqual(detect_gpx_format(path), "gc")

    def test_detect_oc_format(self):
        path = _write_tmp(MINIMAL_OC_GPX)
        self.assertEqual(detect_gpx_format(path), "oc")

    def test_detect_unknown_format(self):
        path = _write_tmp("This is not a GPX file at all.", suffix=".txt")
        self.assertEqual(detect_gpx_format(path), "unknown")

    def test_detect_missing_file(self):
        self.assertEqual(detect_gpx_format("/nonexistent/path/file.gpx"), "unknown")

    def test_detect_zip_gc(self):
        gpx_bytes = MINIMAL_GC_GPX.encode("utf-8")
        tmp = tempfile.NamedTemporaryFile(suffix=".zip", delete=False)
        with zipfile.ZipFile(tmp, "w") as zf:
            zf.writestr("pocket_query.gpx", gpx_bytes)
        tmp.close()
        self.assertEqual(detect_gpx_format(tmp.name), "gc")

    def test_detect_zip_oc(self):
        gpx_bytes = MINIMAL_OC_GPX.encode("utf-8")
        tmp = tempfile.NamedTemporaryFile(suffix=".zip", delete=False)
        with zipfile.ZipFile(tmp, "w") as zf:
            zf.writestr("oc_export.gpx", gpx_bytes)
        tmp.close()
        self.assertEqual(detect_gpx_format(tmp.name), "oc")


# ===================================================================
# 2. OC GPX pure parsing tests (no DB)
# ===================================================================


def _make_oc_wpt_elements(
    oc_code="OC13726",
    lat="48.5",
    lon="9.1",
    name="Test Cache",
    cache_type="Traditional Cache",
    container="Small",
    difficulty="2",
    terrain="2.5",
    archived="False",
    available="True",
):
    """Return (wpt_el, cache_el) parsed from a minimal OC wpt snippet."""
    xml_str = textwrap.dedent(f"""\
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
            <groundspeak:difficulty>{difficulty}</groundspeak:difficulty>
            <groundspeak:terrain>{terrain}</groundspeak:terrain>
            <groundspeak:country>Germany</groundspeak:country>
            <groundspeak:state>Baden-Württemberg</groundspeak:state>
            <groundspeak:short_description>Short</groundspeak:short_description>
            <groundspeak:long_description>Long</groundspeak:long_description>
            <groundspeak:encoded_hints>Hint</groundspeak:encoded_hints>
            <groundspeak:logs></groundspeak:logs>
          </groundspeak:cache>
        </wpt>
    """)
    wpt_el = ET.fromstring(xml_str)
    cache_el = wpt_el.find(f"{{{NS_GS}}}cache")
    return wpt_el, cache_el


class ParseOcCacheFieldsTests(TestCase):

    def test_parse_oc_cache_fields(self):
        wpt_el, cache_el = _make_oc_wpt_elements()
        fields = parse_oc_cache_fields(wpt_el, cache_el)

        self.assertEqual(fields["oc_code"], "OC13726")
        self.assertEqual(fields["name"], "Test Cache")
        self.assertEqual(fields["cache_type"], CacheType.TRADITIONAL)
        self.assertEqual(fields["size"], CacheSize.SMALL)
        self.assertEqual(fields["status"], CacheStatus.ACTIVE)
        self.assertAlmostEqual(fields["latitude"], 48.5)
        self.assertAlmostEqual(fields["longitude"], 9.1)
        self.assertAlmostEqual(fields["difficulty"], 2.0)
        self.assertAlmostEqual(fields["terrain"], 2.5)
        self.assertEqual(fields["country"], "Germany")
        self.assertEqual(fields["primary_source"], "oc_de")


class ParseOcExtensionTests(TestCase):

    def test_parse_oc_extension(self):
        xml_str = textwrap.dedent(f"""\
            <wpt lat="48.5" lon="9.1" xmlns="{NS_GPX}">
              <name>OC13726</name>
              <oc:cache xmlns:oc="{NS_OC}">
                <oc:trip_time>0.5</oc:trip_time>
                <oc:requires_password>true</oc:requires_password>
                <oc:other_code>GC70A3H</oc:other_code>
              </oc:cache>
            </wpt>
        """)
        wpt_el = ET.fromstring(xml_str)
        ext = parse_oc_extension(wpt_el)

        self.assertAlmostEqual(ext["trip_time"], 0.5)
        self.assertTrue(ext["req_passwd"])
        self.assertEqual(ext["other_code"], "GC70A3H")


class ParseOcInlineWaypointsTests(TestCase):

    def test_parse_oc_inline_waypoints(self):
        xml_str = textwrap.dedent(f"""\
            <gpx xmlns="{NS_GPX}" version="1.0">
              <wpt lat="48.5" lon="9.1">
                <name>OC1485B</name>
                <sym>Geocache</sym>
                <groundspeak:cache id="1" available="True" archived="False"
                    xmlns:groundspeak="{NS_GS}">
                  <groundspeak:name>Parent</groundspeak:name>
                </groundspeak:cache>
              </wpt>
              <wpt lat="48.50743" lon="9.07875">
                <name>OC1485B-1</name>
                <cmt>Stage 1: hint text</cmt>
                <desc>Station</desc>
                <sym>Flag, Green</sym>
                <type>Waypoint|Flag, Green</type>
                <gsak:wptExtension xmlns:gsak="{NS_GSAK}">
                  <gsak:Parent>OC1485B</gsak:Parent>
                </gsak:wptExtension>
              </wpt>
            </gpx>
        """)
        root = ET.fromstring(xml_str)
        wpts = parse_oc_inline_waypoints(root)

        self.assertIn("OC1485B", wpts)
        self.assertEqual(len(wpts["OC1485B"]), 1)
        wp = wpts["OC1485B"][0]
        self.assertEqual(wp["lookup"], "OC1485B-1")
        self.assertAlmostEqual(wp["latitude"], 48.50743)
        self.assertAlmostEqual(wp["longitude"], 9.07875)
        self.assertEqual(wp["waypoint_type"], WaypointType.STAGE)
        self.assertEqual(wp["note"], "Stage 1: hint text")


class ParseOcLogsTests(TestCase):

    def test_parse_oc_logs_sets_source(self):
        xml_str = textwrap.dedent(f"""\
            <groundspeak:cache xmlns:groundspeak="{NS_GS}">
              <groundspeak:logs>
                <groundspeak:log id="100">
                  <groundspeak:date>2024-01-15T00:00:00Z</groundspeak:date>
                  <groundspeak:type>Found it</groundspeak:type>
                  <groundspeak:finder id="42">Finder</groundspeak:finder>
                  <groundspeak:text>Found it!</groundspeak:text>
                </groundspeak:log>
                <groundspeak:log id="101">
                  <groundspeak:date>2024-01-16T00:00:00Z</groundspeak:date>
                  <groundspeak:type>Write note</groundspeak:type>
                  <groundspeak:finder id="43">Someone</groundspeak:finder>
                  <groundspeak:text>A note</groundspeak:text>
                </groundspeak:log>
              </groundspeak:logs>
            </groundspeak:cache>
        """)
        cache_el = ET.fromstring(xml_str)
        logs = parse_oc_logs(cache_el)

        self.assertEqual(len(logs), 2)
        for log in logs:
            self.assertEqual(log["source"], "oc_de")


# ===================================================================
# 3. OC GPX import integration tests (DB)
# ===================================================================


class ImportOcGpxTests(TestCase):

    def test_import_oc_gpx_creates_cache(self):
        path = _write_tmp(_full_oc_gpx(other_code=""))
        stats = import_oc_gpx(path)

        self.assertEqual(stats.errors, [])
        self.assertEqual(stats.created, 1)
        self.assertEqual(stats.updated, 0)
        cache = Geocache.objects.get(oc_code="OC13726")
        self.assertEqual(cache.name, "Test Cache")
        self.assertEqual(cache.cache_type, CacheType.TRADITIONAL)
        self.assertEqual(cache.primary_source, "oc_de")

    def test_import_oc_gpx_with_other_code(self):
        path = _write_tmp(_full_oc_gpx(other_code="GC12345"))
        import_oc_gpx(path)

        cache = Geocache.objects.get(gc_code="GC12345")
        self.assertEqual(cache.gc_code, "GC12345")

    def test_import_oc_gpx_creates_oc_extension(self):
        path = _write_tmp(_full_oc_gpx(other_code="", trip_time="1.5", req_passwd="true"))
        import_oc_gpx(path)

        cache = Geocache.objects.get(oc_code="OC13726")
        ext = OCExtension.objects.get(geocache=cache)
        self.assertAlmostEqual(ext.trip_time, 1.5)
        self.assertTrue(ext.req_passwd)

    def test_import_oc_gpx_inline_waypoints(self):
        child_wpt = textwrap.dedent(f"""\
          <wpt lat="48.50743" lon="9.07875" xmlns:gsak="{NS_GSAK}">
            <name>OC13726-1</name>
            <cmt>Stage 1</cmt>
            <desc>Station</desc>
            <sym>Flag, Green</sym>
            <type>Waypoint|Flag, Green</type>
            <gsak:wptExtension>
              <gsak:Parent>OC13726</gsak:Parent>
            </gsak:wptExtension>
          </wpt>
        """)
        path = _write_tmp(_full_oc_gpx(other_code="", extra_wpts=child_wpt))
        import_oc_gpx(path)

        cache = Geocache.objects.get(oc_code="OC13726")
        wpts = Waypoint.objects.filter(geocache=cache)
        self.assertEqual(wpts.count(), 1)
        wp = wpts.first()
        self.assertEqual(wp.lookup, "OC13726-1")
        self.assertAlmostEqual(wp.latitude, 48.50743)

    def test_import_oc_gpx_skips_gc_codes(self):
        """A wpt with a GC code should be skipped by the OC importer."""
        gpx_xml = textwrap.dedent(f"""\
            <?xml version="1.0" encoding="utf-8"?>
            <gpx xmlns="http://www.topografix.com/GPX/1/0"
                 creator="Opencaching.de - https://www.opencaching.de/" version="1.0">
              <wpt lat="48.5" lon="9.1">
                <time>2017-02-08T00:00:00Z</time>
                <name>GC12345</name>
                <sym>Geocache</sym>
                <groundspeak:cache id="1" available="True" archived="False"
                    xmlns:groundspeak="{NS_GS}">
                  <groundspeak:name>GC Cache</groundspeak:name>
                  <groundspeak:placed_by>Owner</groundspeak:placed_by>
                  <groundspeak:owner id="1">Owner</groundspeak:owner>
                  <groundspeak:type>Traditional Cache</groundspeak:type>
                  <groundspeak:container>Small</groundspeak:container>
                  <groundspeak:difficulty>2</groundspeak:difficulty>
                  <groundspeak:terrain>2</groundspeak:terrain>
                  <groundspeak:country>Germany</groundspeak:country>
                  <groundspeak:state>BW</groundspeak:state>
                  <groundspeak:short_description>S</groundspeak:short_description>
                  <groundspeak:long_description>L</groundspeak:long_description>
                  <groundspeak:encoded_hints>H</groundspeak:encoded_hints>
                  <groundspeak:logs></groundspeak:logs>
                </groundspeak:cache>
              </wpt>
            </gpx>
        """)
        path = _write_tmp(gpx_xml)
        stats = import_oc_gpx(path)

        self.assertEqual(stats.created, 0)
        self.assertEqual(stats.updated, 0)
        self.assertEqual(Geocache.objects.count(), 0)


# ===================================================================
# 4. Attribution extraction tests (no DB)
# ===================================================================


class ExtractOcAttributionTests(TestCase):

    def test_extract_attribution_basic(self):
        """HTML ending with OC attribution <p><em>© ... Opencaching.de ...</em></p>."""
        html = (
            "<p>Cache description goes here.</p>\n"
            '<p><em>© 2017 Opencaching.de, CC BY-NC-ND 2.5</em></p>'
        )
        clean, attribution = extract_oc_attribution(html)
        self.assertIn("Cache description", clean)
        self.assertNotIn("Opencaching", clean)
        self.assertIn("Opencaching", attribution)
        self.assertIn("©", attribution)

    def test_extract_attribution_with_protected_areas(self):
        """Attribution followed by a protected-areas block — both captured together."""
        html = (
            "<p>Cache description.</p>\n"
            '<p><em>© 2017 Opencaching.de, CC BY-NC-ND 2.5</em></p>\n'
            "<p>This geocache is probably placed within the following protected areas</p>\n"
            "<ul><li>Some Nature Reserve</li></ul>"
        )
        clean, attribution = extract_oc_attribution(html)
        self.assertEqual(clean, "<p>Cache description.</p>")
        self.assertIn("Opencaching", attribution)
        self.assertIn("protected areas", attribution)

    def test_extract_attribution_none(self):
        """Plain description with no OC attribution — returns original unchanged."""
        html = "<p>Just a normal cache description.</p>"
        clean, attribution = extract_oc_attribution(html)
        self.assertEqual(clean, html)
        self.assertEqual(attribution, "")


# ===================================================================
# 5. Source precedence tests (save_geocache)
# ===================================================================


def _make_base_fields(**overrides):
    """Return minimal Geocache field dict, optionally overriding values."""
    base = {
        "name": "Base Name",
        "cache_type": CacheType.TRADITIONAL,
        "size": CacheSize.SMALL,
        "status": CacheStatus.ACTIVE,
        "latitude": 48.5,
        "longitude": 9.1,
        "primary_source": "gc",
    }
    base.update(overrides)
    return base


class SourcePrecedenceTests(TestCase):

    def test_oc_update_does_not_overwrite_gc_fields(self):
        """OC update must not replace GC-owned fields on an existing GC cache."""
        # Create cache with GC data
        cache = Geocache.objects.create(
            gc_code="GC1234A",
            oc_code="",
            name="GC Name",
            cache_type=CacheType.TRADITIONAL,
            size=CacheSize.SMALL,
            status=CacheStatus.ACTIVE,
            latitude=48.5,
            longitude=9.1,
            primary_source="gc",
        )

        save_geocache(
            oc_code="OC00001",
            gc_code="GC1234A",
            fields={
                "name": "OC Name",
                "cache_type": CacheType.MULTI,
                "size": CacheSize.REGULAR,
                "status": CacheStatus.ACTIVE,
                "latitude": 48.6,
                "longitude": 9.2,
                "oc_code": "OC00001",
                "primary_source": "oc_de",
            },
            update_source="oc",
        )

        cache.refresh_from_db()
        # GC-owned field must NOT be overwritten
        self.assertEqual(cache.name, "GC Name")
        self.assertEqual(cache.cache_type, CacheType.TRADITIONAL)
        # OC code should be stored (not a GC-owned field)
        self.assertEqual(cache.oc_code, "OC00001")

    def test_oc_update_creates_new_cache_normally(self):
        """When no existing cache matches, OC create sets all fields normally."""
        save_geocache(
            oc_code="OC00002",
            fields={
                "name": "OC Only Cache",
                "cache_type": CacheType.TRADITIONAL,
                "size": CacheSize.SMALL,
                "status": CacheStatus.ACTIVE,
                "latitude": 48.5,
                "longitude": 9.1,
                "primary_source": "oc_de",
            },
            update_source="oc",
        )

        cache = Geocache.objects.get(oc_code="OC00002")
        self.assertEqual(cache.name, "OC Only Cache")
        self.assertEqual(cache.primary_source, "oc_de")

    def test_gc_update_overwrites_oc_fields(self):
        """GC import always overwrites shared fields, even on an OC-primary cache."""
        Geocache.objects.create(
            gc_code="",
            oc_code="OC00003",
            name="OC Name",
            cache_type=CacheType.TRADITIONAL,
            size=CacheSize.SMALL,
            status=CacheStatus.ACTIVE,
            latitude=48.5,
            longitude=9.1,
            primary_source="oc_de",
        )

        save_geocache(
            gc_code="GC5555A",
            fields={
                "name": "GC Name",
                "cache_type": CacheType.MULTI,
                "size": CacheSize.REGULAR,
                "status": CacheStatus.ACTIVE,
                "latitude": 48.6,
                "longitude": 9.2,
                "primary_source": "gc",
            },
            update_source="gc",
        )

        cache = Geocache.objects.get(gc_code="GC5555A")
        self.assertEqual(cache.name, "GC Name")
        self.assertEqual(cache.cache_type, CacheType.MULTI)

    def test_oc_update_adds_logs_even_when_skipping_fields(self):
        """OC update skips shared fields but still appends new OC logs."""
        from datetime import date

        Geocache.objects.create(
            gc_code="GC6666A",
            oc_code="",
            name="GC Name",
            cache_type=CacheType.TRADITIONAL,
            size=CacheSize.SMALL,
            status=CacheStatus.ACTIVE,
            latitude=48.5,
            longitude=9.1,
            primary_source="gc",
        )

        logs = [
            {
                "log_type": LogType.FOUND,
                "user_name": "Tester",
                "user_id": "42",
                "logged_date": date(2024, 6, 1),
                "text": "Found it!",
                "source_id": "oc-log-99",
                "source": "oc_de",
            }
        ]

        save_geocache(
            gc_code="GC6666A",
            oc_code="OC00004",
            fields={
                "name": "OC Name",
                "cache_type": CacheType.MULTI,
                "size": CacheSize.REGULAR,
                "status": CacheStatus.ACTIVE,
                "latitude": 48.6,
                "longitude": 9.2,
                "oc_code": "OC00004",
                "primary_source": "oc_de",
            },
            logs=logs,
            update_source="oc",
        )

        cache = Geocache.objects.get(gc_code="GC6666A")
        # GC field must be preserved
        self.assertEqual(cache.name, "GC Name")
        # Log must be added despite field skipping
        self.assertEqual(cache.logs.count(), 1)
        self.assertEqual(cache.logs.first().source, "oc_de")


# ===================================================================
# 6. OC GPX import stores attribution on OCExtension
# ===================================================================


class ImportOcGpxAttributionTests(TestCase):

    def test_import_oc_gpx_stores_attribution(self):
        """Attribution extracted from long_description is stored on OCExtension."""
        import html as html_lib

        attribution_html = (
            '<p><em>\u00a9 2017 Opencaching.de, CC BY-NC-ND 2.5</em></p>'
        )
        long_desc_with_attr = f"<p>A great cache.</p>\n{attribution_html}"
        # Escape the HTML markup so it survives XML parsing as element text
        long_desc_escaped = html_lib.escape(long_desc_with_attr)

        # Build a GPX that embeds the attribution in long_description.
        # Constructed without leading indent so the XML declaration is at column 0.
        gpx_xml = (
            '<?xml version="1.0" encoding="utf-8"?>\n'
            '<gpx xmlns="http://www.topografix.com/GPX/1/0"\n'
            '     creator="Opencaching.de - https://www.opencaching.de/" version="1.0">\n'
            '  <wpt lat="48.5" lon="9.1">\n'
            '    <time>2020-01-01T00:00:00Z</time>\n'
            '    <name>OC99999</name>\n'
            '    <sym>Geocache</sym>\n'
            f'    <groundspeak:cache id="1" available="True" archived="False"\n'
            f'        xmlns:groundspeak="{NS_GS}">\n'
            '      <groundspeak:name>Attribution Cache</groundspeak:name>\n'
            '      <groundspeak:placed_by>Owner</groundspeak:placed_by>\n'
            '      <groundspeak:owner id="1">Owner</groundspeak:owner>\n'
            '      <groundspeak:type>Traditional Cache</groundspeak:type>\n'
            '      <groundspeak:container>Small</groundspeak:container>\n'
            '      <groundspeak:difficulty>2</groundspeak:difficulty>\n'
            '      <groundspeak:terrain>2</groundspeak:terrain>\n'
            '      <groundspeak:country>Germany</groundspeak:country>\n'
            '      <groundspeak:state>BW</groundspeak:state>\n'
            '      <groundspeak:short_description>Short</groundspeak:short_description>\n'
            f'      <groundspeak:long_description>{long_desc_escaped}</groundspeak:long_description>\n'
            '      <groundspeak:encoded_hints>Hint</groundspeak:encoded_hints>\n'
            '      <groundspeak:logs></groundspeak:logs>\n'
            '    </groundspeak:cache>\n'
            '  </wpt>\n'
            '</gpx>\n'
        )

        path = _write_tmp(gpx_xml)
        stats = import_oc_gpx(path)

        self.assertEqual(stats.errors, [], stats.errors)
        self.assertEqual(stats.created, 1)

        cache = Geocache.objects.get(oc_code="OC99999")
        ext = OCExtension.objects.get(geocache=cache)

        # Attribution stored separately
        self.assertIn("Opencaching", ext.attribution_html)
        # Main description is clean (no attribution)
        self.assertNotIn("Opencaching", cache.long_description)
        self.assertIn("A great cache", cache.long_description)
