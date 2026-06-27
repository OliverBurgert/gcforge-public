"""Tests for geocaches.log_format — the canonical log-text transform module."""
from datetime import date

from django.test import SimpleTestCase, TestCase

from geocaches.log_format import (
    GC_SMILEYS,
    PLACEHOLDER_KEYS,
    expand_placeholders,
    render_for_display,
    smileys_to_unicode,
    to_gc,
    to_oc,
)


class TestSmileyMap(SimpleTestCase):
    def test_all_codes_replaced(self):
        for code, emoji in GC_SMILEYS.items():
            self.assertEqual(smileys_to_unicode(code), emoji,
                             f"smiley {code!r} should become {emoji!r}")

    def test_inline_with_text(self):
        self.assertEqual(
            smileys_to_unicode("Found it [:)] — TFTC [;)]"),
            "Found it 😊 — TFTC 😉",
        )

    def test_no_match(self):
        self.assertEqual(smileys_to_unicode("plain text"), "plain text")


class TestToGc(SimpleTestCase):
    def test_passthrough(self):
        self.assertEqual(to_gc("**bold** _italic_ [:)]"),
                         "**bold** _italic_ [:)]")

    def test_crlf_normalised(self):
        self.assertEqual(to_gc("a\r\nb\rc\nd"), "a\nb\nc\nd")

    def test_empty(self):
        self.assertEqual(to_gc(""), "")
        self.assertIsNone(to_gc(None))


class TestToOc(SimpleTestCase):
    def test_smiley_to_unicode(self):
        self.assertIn("😊", to_oc("hi [:)]"))
        self.assertNotIn("[:)]", to_oc("hi [:)]"))

    def test_markdown_bold(self):
        out = to_oc("**bold**")
        self.assertIn("<strong>bold</strong>", out)

    def test_markdown_italic(self):
        self.assertIn("<em>it</em>", to_oc("*it*"))
        self.assertIn("<em>it</em>", to_oc("_it_"))

    def test_markdown_link(self):
        out = to_oc("see [docs](https://example.com)")
        self.assertIn('<a href="https://example.com"', out)
        self.assertIn('target="_blank"', out)
        self.assertIn(">docs</a>", out)

    def test_bbcode(self):
        self.assertIn("<strong>bold</strong>", to_oc("[b]bold[/b]"))
        self.assertIn("<em>it</em>", to_oc("[i]it[/i]"))

    def test_existing_html_passes_through_sanitised(self):
        out = to_oc("<p>hello <script>alert(1)</script>world</p>")
        self.assertIn("<p>hello world</p>", out)
        self.assertNotIn("<script", out)
        self.assertNotIn("alert(1)", out)

    def test_event_handlers_stripped(self):
        # Wrap in <p> so HTML-detect triggers (sanitise branch).
        out = to_oc('<p><a href="x" onclick="bad()">x</a></p>')
        self.assertNotIn("onclick", out)

    def test_javascript_href_replaced(self):
        out = to_oc('<p><a href="javascript:bad()">x</a></p>')
        self.assertNotIn("javascript:", out)

    def test_lines_become_br(self):
        out = to_oc("line1\nline2")
        self.assertIn("<br>", out)

    def test_block_elements(self):
        self.assertIn("<strong>Hello</strong>", to_oc("# Hello"))
        self.assertIn("<hr>", to_oc("---"))

    def test_empty(self):
        self.assertEqual(to_oc(""), "")


class TestRenderForDisplay(SimpleTestCase):
    def test_oc_source_treated_as_html(self):
        out = render_for_display("<p>hi</p>", source="oc_de")
        self.assertEqual(str(out), "<p>hi</p>")

    def test_oc_source_sanitised(self):
        out = render_for_display(
            "<p>hi <script>alert(1)</script></p>", source="oc_de",
        )
        self.assertNotIn("<script", str(out))

    def test_oc_link_gets_target_blank(self):
        out = render_for_display('<a href="https://x">x</a>', source="oc_de")
        self.assertIn('target="_blank"', str(out))
        self.assertIn('rel="noopener"', str(out))

    def test_gc_source_uses_gc_markup(self):
        out = render_for_display("**bold** [:)]", source="gc")
        self.assertIn("<strong>bold</strong>", str(out))
        self.assertIn("😊", str(out))

    def test_blank_source_uses_gc_markup(self):
        out = render_for_display("**bold**", source="")
        self.assertIn("<strong>bold</strong>", str(out))

    def test_empty(self):
        self.assertEqual(render_for_display("", source="gc"), "")


class _CacheFake:
    """Lightweight Geocache stand-in for placeholder tests."""
    def __init__(self, **kw):
        defaults = {
            "name": "Test Cache", "gc_code": "GCABCDE", "oc_code": "",
            "cache_type": "Traditional Cache", "size": "Small",
            "difficulty": 2.0, "terrain": 3.5,
            "owner": "alice", "hidden_date": date(2020, 5, 1),
            "country": "Germany", "state": "Baden-Württemberg",
            "latitude": 48.123456, "longitude": 9.654321,
        }
        defaults.update(kw)
        for k, v in defaults.items():
            setattr(self, k, v)


class TestPlaceholders(TestCase):
    def test_no_placeholders_passthrough(self):
        self.assertEqual(
            expand_placeholders("plain text", cache=_CacheFake()),
            "plain text",
        )

    def test_cache_fields(self):
        out = expand_placeholders(
            "[name] ([gc_code]) by [owner], D[difficulty]/T[terrain] in [country]",
            cache=_CacheFake(),
        )
        self.assertEqual(out, "Test Cache (GCABCDE) by alice, D2/T3.5 in Germany")

    def test_difficulty_terrain_no_trailing_zero(self):
        out = expand_placeholders("[difficulty]/[terrain]",
                                  cache=_CacheFake(difficulty=1.0, terrain=4.0))
        self.assertEqual(out, "1/4")

    def test_lat_lon_six_decimals(self):
        out = expand_placeholders("[lat],[lon]", cache=_CacheFake())
        self.assertEqual(out, "48.123456,9.654321")

    def test_log_type_and_log_date(self):
        out = expand_placeholders(
            "[log_type] on [log_date]",
            cache=_CacheFake(), log_type="Found it",
            log_date=date(2026, 5, 10),
        )
        self.assertEqual(out, "Found it on 2026-05-10")

    def test_log_date_falls_back_to_today(self):
        out = expand_placeholders("[log_date]", cache=_CacheFake())
        # Don't pin to a real date — just check it's an ISO date string.
        self.assertRegex(out, r"^\d{4}-\d{2}-\d{2}$")

    def test_unknown_placeholder_left_intact(self):
        out = expand_placeholders("[name] / [does_not_exist]", cache=_CacheFake())
        self.assertEqual(out, "Test Cache / [does_not_exist]")

    def test_empty_text(self):
        self.assertEqual(expand_placeholders("", cache=_CacheFake()), "")

    def test_no_cache(self):
        # Without a cache, cache-derived placeholders become empty strings.
        out = expand_placeholders(
            "[name] | [log_type]", cache=None, log_type="Write note",
        )
        self.assertEqual(out, " | Write note")

    def test_placeholder_keys_sanity(self):
        # All keys claimed by the public PLACEHOLDER_KEYS constant must
        # actually substitute on a populated context.
        text = " ".join(f"[{k}]=[{k}]" for k in PLACEHOLDER_KEYS if k != "find_count")
        out = expand_placeholders(text, cache=_CacheFake(), log_type="Found it",
                                  log_date=date(2026, 5, 10))
        self.assertNotIn(text, out)  # something changed
        # No square-bracketed key from the contract should remain unsubstituted.
        for k in PLACEHOLDER_KEYS:
            if k == "find_count":
                continue
            self.assertNotIn(f"[{k}]=[{k}]", out, f"placeholder [{k}] not expanded")


class _UserFake:
    def __init__(self, username):
        self.username = username


class TestFindCount(SimpleTestCase):
    def test_find_count_left_as_literal(self):
        # [find_count] is intentionally NOT expanded server-side — the toolbar
        # JS substitutes it from the "Find #" input at insert time so manual
        # user adjustments stick.
        out = expand_placeholders(
            "find #[find_count] for [name]",
            cache=_CacheFake(), user=_UserFake("me"),
        )
        self.assertEqual(out, "find #[find_count] for Test Cache")


class TestRealWorldSnapshots(SimpleTestCase):
    """Snapshots that exercise the transform on representative log inputs."""

    def test_typical_gc_found_log(self):
        text = "TFTC! [:)]\n\nNice **hide**, took my [^] for the day."
        out = to_oc(text)
        self.assertIn("😊", out)
        self.assertIn("<strong>hide</strong>", out)
        self.assertIn("👍", out)
        self.assertIn("<br>", out)

    def test_gc_passthrough_keeps_legacy_codes(self):
        text = "TFTC [:)] visit our blog: [url=https://example.com]here[/url]"
        out = to_gc(text)
        self.assertIn("[:)]", out)
        self.assertIn("[url=https://example.com]", out)

    def test_oc_legacy_html_log(self):
        text = '<p>Schöner Cache, danke!</p><p>Gefunden mit <b>Familie</b>.</p>'
        out = to_oc(text)
        self.assertIn("<b>Familie</b>", out)
        self.assertIn("Schöner Cache", out)

    def test_ascii_art_asterisks_preserved(self):
        # Decorative asterisks adjacent to non-word punctuation must NOT be
        # treated as markdown italic.
        out = to_oc('.\\¤*""*¤.,,.¤*""*¤,.\\☆°*')
        self.assertNotIn("<em>", out)
        self.assertEqual(out.count("*"), 5)  # all five asterisks survive

    def test_real_italic_still_works(self):
        self.assertIn("<em>quick</em>", to_oc("the *quick* fox"))
        self.assertIn("<em>start</em>", to_oc("*start* of line"))
        self.assertIn("<em>end</em>", to_oc("at the *end*"))
        self.assertIn("<em>only</em>", to_oc("*only*"))

    def test_inline_no_match_without_flanking(self):
        # Stuck mid-word — not italic.
        self.assertNotIn("<em>", to_oc("foo*bar*baz"))

    def test_italic_followed_by_punctuation(self):
        out = to_oc("Wow, *amazing*! Right?")
        self.assertIn("<em>amazing</em>", out)
