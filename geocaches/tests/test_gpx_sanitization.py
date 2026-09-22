"""
Tests for geocaches.importers.gpx_common's XML sanitization — stripping
XML-1.0-illegal control-character bytes before parsing.

Found live: a real Pocket Query GPX ("Ueberlingen 30km bis 2016-04-01")
failed to import at all with `xml.etree.ElementTree.ParseError: reference
to invalid character number: line 41895, column 221` — a single illegal
control byte deep in one cache's log text/description invalidated the
*entire* multi-thousand-line document (XML parsing is all-or-nothing).
Not specific to the PQ-web path — every GPX/XML entry point in the
importers package had the same unguarded ET.parse()/ET.fromstring() call.
"""

import io
import tempfile
import textwrap
import zipfile

from django.test import SimpleTestCase, TestCase

from geocaches.importers import import_gc_gpx, import_gpx, import_oc_gpx
from geocaches.importers.gpx_common import (
    parse_gpx_bytes,
    parse_gpx_file,
    strip_illegal_xml_bytes,
)
from geocaches.importers.lab2gpx import import_lab2gpx
from geocaches.importers.lookups import NS_GPX, NS_GS
from geocaches.models import Geocache

NS_OC = "https://github.com/opencaching/gpx-extension-v1"


class TestStripIllegalXmlBytes(SimpleTestCase):
    def test_removes_c0_control_bytes(self):
        # NUL, vertical tab, form feed, ESC -- all illegal in XML 1.0.
        data = b"before\x00mid\x0bdle\x0cend\x1btext"
        self.assertEqual(strip_illegal_xml_bytes(data), b"beforemiddleendtext")

    def test_preserves_tab_lf_cr(self):
        data = b"a\tb\nc\rd"
        self.assertEqual(strip_illegal_xml_bytes(data), data)

    def test_preserves_normal_and_unicode_bytes(self):
        data = "Löwen café 日本語".encode("utf-8")
        self.assertEqual(strip_illegal_xml_bytes(data), data)

    def test_empty_input(self):
        self.assertEqual(strip_illegal_xml_bytes(b""), b"")

    def test_removes_illegal_hex_char_references(self):
        # Live-found 2026-08-15: GC's own PQ generator escapes a finder's
        # literal control character as a numeric char-ref in the GPX text
        # rather than emitting the raw byte -- confirmed against a real PQ
        # ("Ueberlingen 30km bis 2016-04-01") at line 41895: "...Höhe
        # &#x1E;„T"..." (0x1E = RECORD SEPARATOR, illegal in XML 1.0).
        # Six printable ASCII bytes -- the raw-byte pass alone doesn't touch it.
        data = "Höhe &#x1E;„T“ merke".encode("utf-8")
        result = strip_illegal_xml_bytes(data)
        self.assertNotIn(b"&#x1E;", result)
        self.assertIn("Höhe".encode(), result)

    def test_removes_illegal_decimal_char_references(self):
        data = b"before&#31;after"
        self.assertEqual(strip_illegal_xml_bytes(data), b"beforeafter")

    def test_preserves_legal_char_references(self):
        # &#65; = 'A' (legal), &#9; = tab (legal), &amp; is not a char-ref at all.
        data = b"x&#65;y&#9;z&amp;w"
        self.assertEqual(strip_illegal_xml_bytes(data), data)


class TestParseGpxBytesAndFile(SimpleTestCase):
    def test_parse_gpx_bytes_survives_an_illegal_control_char(self):
        xml = f'<gpx xmlns="{NS_GPX}"><wpt><name>x\x0by</name></wpt></gpx>'.encode()

        root = parse_gpx_bytes(xml)

        name_el = root.find(f"{{{NS_GPX}}}wpt/{{{NS_GPX}}}name")
        self.assertEqual(name_el.text, "xy")

    def test_parse_gpx_bytes_raises_without_sanitization_would_have_failed(self):
        # Sanity check that the fixture actually reproduces the real bug --
        # confirms this isn't a false-positive test (raw ET would choke on it).
        import xml.etree.ElementTree as ET

        xml = f'<gpx xmlns="{NS_GPX}"><wpt><name>x\x0by</name></wpt></gpx>'.encode()
        with self.assertRaises(ET.ParseError):
            ET.fromstring(xml)

    def test_parse_gpx_file(self):
        f = tempfile.NamedTemporaryFile(mode="wb", suffix=".gpx", delete=False)
        f.write(f'<gpx xmlns="{NS_GPX}"><wpt><name>a\x00b</name></wpt></gpx>'.encode())
        f.close()

        root = parse_gpx_file(f.name)

        name_el = root.find(f"{{{NS_GPX}}}wpt/{{{NS_GPX}}}name")
        self.assertEqual(name_el.text, "ab")


def _gc_wpt_with_illegal_char(gc_code="GC11111") -> str:
    """A GC <wpt> whose long_description carries an illegal control byte,
    mirroring where the real bug was found (log/description text)."""
    return textwrap.dedent(f"""
        <wpt lat="48.5" lon="9.1" xmlns="{NS_GPX}">
          <time>2005-04-28T00:00:00</time>
          <name>{gc_code}</name>
          <sym>Geocache</sym>
          <type>Geocache|Traditional Cache</type>
          <groundspeak:cache archived="False" available="True"
              xmlns:groundspeak="{NS_GS}">
            <groundspeak:name>Test Cache</groundspeak:name>
            <groundspeak:placed_by>Owner</groundspeak:placed_by>
            <groundspeak:owner id="1">Owner</groundspeak:owner>
            <groundspeak:type>Traditional Cache</groundspeak:type>
            <groundspeak:container>Small</groundspeak:container>
            <groundspeak:difficulty>2.0</groundspeak:difficulty>
            <groundspeak:terrain>1.5</groundspeak:terrain>
            <groundspeak:country>Germany</groundspeak:country>
            <groundspeak:state>Bavaria</groundspeak:state>
            <groundspeak:short_description html="False">Short</groundspeak:short_description>
            <groundspeak:long_description html="False">Copy-pasted\x0bcontrol char here</groundspeak:long_description>
            <groundspeak:encoded_hints>A hint</groundspeak:encoded_hints>
            <groundspeak:attributes/>
            <groundspeak:logs/>
            <groundspeak:travelbugs/>
          </groundspeak:cache>
        </wpt>
    """).strip()


def _gc_wpt_with_illegal_char_ref(gc_code="GC55555") -> str:
    """A GC <wpt> whose long_description carries a literal numeric char-ref
    to an illegal codepoint (``&#x1E;``) — the exact shape GC's own PQ
    generator produces, confirmed live 2026-08-15 against a real PQ."""
    return textwrap.dedent(f"""
        <wpt lat="48.5" lon="9.1" xmlns="{NS_GPX}">
          <time>2005-04-28T00:00:00</time>
          <name>{gc_code}</name>
          <sym>Geocache</sym>
          <type>Geocache|Traditional Cache</type>
          <groundspeak:cache archived="False" available="True"
              xmlns:groundspeak="{NS_GS}">
            <groundspeak:name>Test Cache</groundspeak:name>
            <groundspeak:placed_by>Owner</groundspeak:placed_by>
            <groundspeak:owner id="1">Owner</groundspeak:owner>
            <groundspeak:type>Traditional Cache</groundspeak:type>
            <groundspeak:container>Small</groundspeak:container>
            <groundspeak:difficulty>2.0</groundspeak:difficulty>
            <groundspeak:terrain>1.5</groundspeak:terrain>
            <groundspeak:country>Germany</groundspeak:country>
            <groundspeak:state>Bavaria</groundspeak:state>
            <groundspeak:short_description html="False">Short</groundspeak:short_description>
            <groundspeak:long_description html="False">H&#248;he &#x1E;stray ref</groundspeak:long_description>
            <groundspeak:encoded_hints>A hint</groundspeak:encoded_hints>
            <groundspeak:attributes/>
            <groundspeak:logs/>
            <groundspeak:travelbugs/>
          </groundspeak:cache>
        </wpt>
    """).strip()


def _full_gpx(*wpts) -> str:
    body = "\n".join(wpts)
    return f'<?xml version="1.0" encoding="utf-8"?><gpx version="1.0" xmlns="{NS_GPX}">{body}</gpx>'


def _write_gpx_bytes(content: str) -> str:
    f = tempfile.NamedTemporaryFile(mode="wb", suffix=".gpx", delete=False)
    f.write(content.encode("utf-8"))
    f.close()
    return f.name


def _write_zip(files: dict) -> str:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, content in files.items():
            zf.writestr(name, content.encode("utf-8"))
    f = tempfile.NamedTemporaryFile(suffix=".zip", delete=False)
    f.write(buf.getvalue())
    f.close()
    return f.name


class TestUnifiedImporterSurvivesIllegalChars(TestCase):
    """import_gpx() is what every Pocket Query (API and website) feeds
    through -- this is the path the real bug was found on."""

    def test_gpx_file_with_illegal_char_imports_successfully(self):
        content = _full_gpx(_gc_wpt_with_illegal_char("GC11111"))
        path = _write_gpx_bytes(content)

        stats = import_gpx(path)

        self.assertEqual(stats.created, 1)
        self.assertEqual(stats.errors, [])
        cache = Geocache.objects.get(gc_code="GC11111")
        self.assertNotIn("\x0b", cache.long_description)

    def test_zip_with_illegal_char_imports_successfully(self):
        content = _full_gpx(_gc_wpt_with_illegal_char("GC22222"))
        path = _write_zip({"query.gpx": content})

        stats = import_gpx(path)

        self.assertEqual(stats.created, 1)
        self.assertEqual(stats.errors, [])
        self.assertTrue(Geocache.objects.filter(gc_code="GC22222").exists())

    def test_zip_with_illegal_char_reference_imports_successfully(self):
        # Reproduces the real bug report end to end: a PQ zip whose GPX
        # contains a literal "&#x1E;" reference (not a raw byte) in a
        # cache's long_description -- the shape that survived the initial
        # raw-byte-only fix and still failed against the real PQ.
        content = _full_gpx(_gc_wpt_with_illegal_char_ref("GC66666"))
        path = _write_zip({"query.gpx": content})

        stats = import_gpx(path)

        self.assertEqual(stats.created, 1)
        self.assertEqual(stats.errors, [])
        cache = Geocache.objects.get(gc_code="GC66666")
        self.assertNotIn("&#x1E;", cache.long_description)
        self.assertIn("ø", cache.long_description)  # legal ref (&#248;) preserved


class TestGcImporterSurvivesIllegalChars(TestCase):
    def test_non_zip_gc_gpx_with_illegal_char(self):
        content = _full_gpx(_gc_wpt_with_illegal_char("GC33333"))
        path = _write_gpx_bytes(content)

        stats = import_gc_gpx(path)

        self.assertEqual(stats.created, 1)
        self.assertTrue(Geocache.objects.filter(gc_code="GC33333").exists())

    def test_zip_gc_gpx_with_illegal_char(self):
        content = _full_gpx(_gc_wpt_with_illegal_char("GC44444"))
        path = _write_zip({"query.gpx": content})

        stats = import_gc_gpx(path)

        self.assertEqual(stats.created, 1)
        self.assertTrue(Geocache.objects.filter(gc_code="GC44444").exists())


def _oc_wpt_with_illegal_char(oc_code="OC13726") -> str:
    return textwrap.dedent(f"""
        <wpt lat="48.6" lon="9.2" xmlns="{NS_GPX}">
          <time>2017-02-08T00:00:00Z</time>
          <name>{oc_code}</name>
          <sym>Geocache</sym>
          <groundspeak:cache id="182743" available="True" archived="False"
              xmlns:groundspeak="{NS_GS}">
            <groundspeak:name>OC Test Cache</groundspeak:name>
            <groundspeak:placed_by>Owner</groundspeak:placed_by>
            <groundspeak:owner id="1">Owner</groundspeak:owner>
            <groundspeak:type>Traditional Cache</groundspeak:type>
            <groundspeak:container>Small</groundspeak:container>
            <groundspeak:difficulty>2</groundspeak:difficulty>
            <groundspeak:terrain>2.5</groundspeak:terrain>
            <groundspeak:country>Germany</groundspeak:country>
            <groundspeak:state>Baden-Württemberg</groundspeak:state>
            <groundspeak:short_description>Short</groundspeak:short_description>
            <groundspeak:long_description>Bad\x0echar here</groundspeak:long_description>
            <groundspeak:encoded_hints>Hint</groundspeak:encoded_hints>
            <groundspeak:logs/>
          </groundspeak:cache>
        </wpt>
    """).strip()


class TestOcImporterSurvivesIllegalChars(TestCase):
    def test_non_zip_oc_gpx_with_illegal_char(self):
        content = _full_gpx(_oc_wpt_with_illegal_char("OC13726"))
        path = _write_gpx_bytes(content)

        stats = import_oc_gpx(path)

        self.assertEqual(stats.created, 1)
        self.assertTrue(Geocache.objects.filter(oc_code="OC13726").exists())

    def test_zip_oc_gpx_with_illegal_char(self):
        content = _full_gpx(_oc_wpt_with_illegal_char("OC99999"))
        path = _write_zip({"query.gpx": content})

        stats = import_oc_gpx(path)

        self.assertEqual(stats.created, 1)
        self.assertTrue(Geocache.objects.filter(oc_code="OC99999").exists())


class TestLab2GpxSurvivesIllegalChars(TestCase):
    def test_parse_gpx_file_used_by_lab2gpx_survives_illegal_char(self):
        # lab2gpx's own format is elaborate (Adventure Lab specific); a full
        # end-to-end import is covered by test_lab2gpx_import.py. Here we
        # just confirm import_lab2gpx routes through the sanitizing
        # parse_gpx_file() rather than a raw ET.parse() -- i.e. that a file
        # with an illegal control byte at least gets past the XML parse
        # step instead of raising ParseError immediately.
        content = (
            f'<?xml version="1.0" encoding="utf-8"?>'
            f'<gpx xmlns="{NS_GPX}" xmlns:groundspeak="{NS_GS}">'
            f'<wpt><name>LC1AAAA</name><desc>bad\x0bchar</desc></wpt>'
            f'</gpx>'
        )
        path = _write_gpx_bytes(content)

        # Should not raise ET.ParseError -- may legitimately produce zero
        # stages given the minimal fixture, but must get past XML parsing.
        stats = import_lab2gpx(path)
        self.assertEqual(stats.errors, [])
