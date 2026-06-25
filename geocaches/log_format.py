"""Log text format conversion — canonical authoring format → per-platform output.

Canonical format authored by the user is **Markdown + Unicode emoji**.
Legacy GC smiley codes (`[:)]` etc.) are accepted as input.

- ``to_gc(text)`` — passthrough (CRLF normalised). gc.com renders markdown +
  legacy smiley codes server-side.
- ``to_oc(text)`` — markdown + smiley codes → safe HTML for OC.
- ``render_for_display(text, source)`` — produces safe HTML for the logs view;
  used by ``cache_tags.render_log_text``.
- ``expand_placeholders(...)`` — stub for the templates feature (Phase 4).
"""
from __future__ import annotations

import re

from django.utils.html import escape
from django.utils.safestring import mark_safe


# ---------------------------------------------------------------------------
# Smileys — GC legacy codes mapped to Unicode emoji
# ---------------------------------------------------------------------------

GC_SMILEYS: dict[str, str] = {
    '[:)]':   '😊',
    '[:-)]':  '😊',
    '[:D]':   '😁',
    '[:-D]':  '😁',
    '[8D]':   '😎',
    '[:I]':   '😊',
    '[:P]':   '😛',
    '[:-P]':  '😛',
    '[}:)]':  '😈',
    '[:O]':   '😮',
    '[:-O]':  '😮',
    '[;)]':   '😉',
    '[;-)]':  '😉',
    '[:o)]':  '🤡',
    '[B)]':   '😎',
    '[8]':    '🎱',
    '[:(]':   '🙁',
    '[8)]':   '😊',
    '[:(!!]': '😡',
    '[xx(]':  '😵',
    '[\\|)]': '😴',
    '[|(]':   '😴',
    '[|)]':   '😴',
    '[:X]':   '😘',
    '[^]':    '👍',
    '[V]':    '👎',
    '[?]':    '❓',
    '[LOL]':  '😂',
}


def smileys_to_unicode(text: str) -> str:
    """Replace legacy GC smiley codes with Unicode emoji."""
    for code, emoji in GC_SMILEYS.items():
        text = text.replace(code, emoji)
    return text


# Curated picker list — one canonical entry per visible emoji.
# Used by the compose toolbar's smiley dropdown.
COMPOSE_SMILEYS: list[dict[str, str]] = [
    {"emoji": "😊", "code": "[:)]",   "label": "Smile"},
    {"emoji": "😁", "code": "[:D]",   "label": "Big smile"},
    {"emoji": "😉", "code": "[;)]",   "label": "Wink"},
    {"emoji": "😎", "code": "[8D]",   "label": "Cool"},
    {"emoji": "😛", "code": "[:P]",   "label": "Tongue"},
    {"emoji": "😈", "code": "[}:)]",  "label": "Evil"},
    {"emoji": "😮", "code": "[:O]",   "label": "Shocked"},
    {"emoji": "🤡", "code": "[:o)]",  "label": "Clown"},
    {"emoji": "🙁", "code": "[:(]",   "label": "Frown"},
    {"emoji": "😡", "code": "[:(!!]", "label": "Angry"},
    {"emoji": "😵", "code": "[xx(]",  "label": "Dead"},
    {"emoji": "😴", "code": "[|)]",   "label": "Sleepy"},
    {"emoji": "😘", "code": "[:X]",   "label": "Kiss"},
    {"emoji": "😂", "code": "[LOL]",  "label": "LOL"},
    {"emoji": "👍", "code": "[^]",    "label": "Thumbs up"},
    {"emoji": "👎", "code": "[V]",    "label": "Thumbs down"},
    {"emoji": "❓", "code": "[?]",    "label": "Question"},
    {"emoji": "🎱", "code": "[8]",    "label": "8-ball"},
]


# ---------------------------------------------------------------------------
# Sanitisation — strip script/style/event handlers from existing HTML
# ---------------------------------------------------------------------------

_SCRIPT_RE  = re.compile(r'<script[^>]*>.*?</script>', re.IGNORECASE | re.DOTALL)
_STYLE_RE   = re.compile(r'<style[^>]*>.*?</style>',   re.IGNORECASE | re.DOTALL)
_EVENT_RE   = re.compile(r'\s+on\w+=(?:"[^"]*"|\'[^\']*\'|[^\s>]*)', re.IGNORECASE)
_JS_HREF_RE = re.compile(r'(href|src)=["\']javascript:[^"\']*["\']', re.IGNORECASE)


def sanitize_html(html: str) -> str:
    """Strip script/style/event handlers and javascript: URLs."""
    html = _SCRIPT_RE.sub('', html)
    html = _STYLE_RE.sub('', html)
    html = _EVENT_RE.sub('', html)
    html = _JS_HREF_RE.sub('href="#"', html)
    return html


_LINK_TAG_RE = re.compile(r'<a(\s[^>]*)>', re.IGNORECASE)


def add_link_targets(html: str) -> str:
    """Ensure every <a> tag opens in a new tab with rel=noopener."""
    def _patch(m):
        attrs = m.group(1)
        if 'target=' not in attrs.lower():
            attrs += ' target="_blank" rel="noopener"'
        elif 'rel=' not in attrs.lower():
            attrs += ' rel="noopener"'
        return f'<a{attrs}>'
    return _LINK_TAG_RE.sub(_patch, html)


# ---------------------------------------------------------------------------
# Markup → HTML conversion (GC mini-markup: markdown subset + BBCode + smileys)
# ---------------------------------------------------------------------------

_HTML_TAG_DETECT_RE = re.compile(
    r'<(?:p|br|div|span|b|i|u|em|strong|a[\s>]|ul|ol|li|h[1-6]|blockquote|img|hr)[\s>/]',
    re.IGNORECASE,
)

_BB_DETECT      = re.compile(r'\[(?:b|i|u|url|img)[\]=]', re.IGNORECASE)
_BB_BOLD        = re.compile(r'\[b\](.*?)\[/b\]',        re.IGNORECASE | re.DOTALL)
_BB_ITALIC      = re.compile(r'\[i\](.*?)\[/i\]',        re.IGNORECASE | re.DOTALL)
_BB_UNDERLINE   = re.compile(r'\[u\](.*?)\[/u\]',        re.IGNORECASE | re.DOTALL)
_BB_URL_NAMED   = re.compile(r'\[url=([^\]]+)\](.*?)\[/url\]', re.IGNORECASE | re.DOTALL)
_BB_URL_PLAIN   = re.compile(r'\[url\](https?://[^\[]+)\[/url\]', re.IGNORECASE)
_BB_IMG         = re.compile(r'\[img\](https?://[^\[]+)\[/img\]', re.IGNORECASE)

_MD_BOLD            = re.compile(r'\*\*(.+?)\*\*')
# Italic *X*: opening * must be at start-of-line/string or after whitespace;
# closing * must be at end-of-line/string, before whitespace, or before
# sentence-ending punctuation. Content cannot start or end with whitespace.
# This prevents ASCII art like "¤*""*¤" from being eaten as italic.
_MD_ITALIC_STAR     = re.compile(
    r'(?:^|(?<=[\s\(\[\{]))'
    r'\*([^\s*][^*\n]*?[^\s*]|[^\s*])\*'
    r'(?=$|[\s.,;:!?\)\]\}\-])',
    re.MULTILINE,
)
_MD_ITALIC_UNDER    = re.compile(r'(?<!\w)_([^_\n]+)_(?!\w)')
_MD_LINK            = re.compile(r'\[([^\]]+)\]\((https?://[^\s)]+)[^)]*\)')
_MD_OL              = re.compile(r'^(\d+)\. (.+)$')


def gc_markup_to_html(text: str) -> str:
    """Convert GC mini-markup (markdown subset + BBCode + smileys + old HTML) to safe HTML."""
    text = smileys_to_unicode(text)

    if _HTML_TAG_DETECT_RE.search(text):
        return add_link_targets(sanitize_html(text))

    has_bb = bool(_BB_DETECT.search(text))

    text = str(escape(text))

    if has_bb:
        text = _BB_BOLD.sub(r'<strong>\1</strong>', text)
        text = _BB_ITALIC.sub(r'<em>\1</em>', text)
        text = _BB_UNDERLINE.sub(r'<u>\1</u>', text)
        text = _BB_URL_NAMED.sub(
            r'<a href="\1" target="_blank" rel="noopener">\2</a>', text)
        text = _BB_URL_PLAIN.sub(
            r'<a href="\1" target="_blank" rel="noopener">\1</a>', text)
        text = _BB_IMG.sub(
            r'<a href="\1" target="_blank" rel="noopener">[image]</a>', text)

    lines = text.split('\n')
    processed = []
    for line in lines:
        s = line.lstrip()
        if s.startswith('### '):
            processed.append(f'<strong>{s[4:].rstrip(" #")}</strong>')
        elif s.startswith('## '):
            processed.append(f'<strong>{s[3:].rstrip(" #")}</strong>')
        elif s.startswith('# '):
            processed.append(f'<strong>{s[2:].rstrip(" #")}</strong>')
        elif s.startswith('> '):
            processed.append(
                f'<span class="d-block ms-2 ps-2 border-start text-muted fst-italic">{s[2:]}</span>')
        elif s in ('---', '* * *', '***'):
            processed.append('<hr>')
        elif s.startswith('* ') or s.startswith('- '):
            processed.append(f'• {s[2:]}')
        else:
            m = _MD_OL.match(s)
            if m:
                processed.append(f'{m.group(1)}. {m.group(2)}')
            else:
                processed.append(line)
    text = '\n'.join(processed)

    text = _MD_BOLD.sub(r'<strong>\1</strong>', text)
    text = _MD_ITALIC_STAR.sub(r'<em>\1</em>', text)
    text = _MD_ITALIC_UNDER.sub(r'<em>\1</em>', text)
    text = _MD_LINK.sub(r'<a href="\2" target="_blank" rel="noopener">\1</a>', text)

    text = text.replace('\n', '<br>')

    return text


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def to_gc(text: str) -> str:
    """Convert canonical text for submission to gc.com.

    GC accepts markdown + legacy smiley codes natively, so this is a passthrough
    apart from CRLF normalisation.
    """
    if not text:
        return text
    return text.replace('\r\n', '\n').replace('\r', '\n')


def to_oc(text: str) -> str:
    """Convert canonical text to safe HTML for submission to OC platforms.

    Smiley codes → Unicode emoji, then markdown/BBCode → safe HTML.
    """
    if not text:
        return text
    text = text.replace('\r\n', '\n').replace('\r', '\n')
    return gc_markup_to_html(text)


def render_for_display(text: str, source: str = '') -> str:
    """Produce safe HTML for the logs view.

    OC logs (source starts with ``oc_``) are treated as HTML and sanitised.
    GC / unknown-source logs are converted from GC mini-markup to safe HTML.
    Returns a ``django.utils.safestring.SafeString``.
    """
    if not text:
        return text

    if str(source).startswith('oc_'):
        html = add_link_targets(sanitize_html(text))
        return mark_safe(html)

    return mark_safe(gc_markup_to_html(text))


PLACEHOLDER_KEYS = (
    "name", "gc_code", "oc_code", "type", "size", "difficulty", "terrain",
    "owner", "hidden_date", "country", "state", "lat", "lon",
    "today", "log_date", "log_type",
    "username", "find_count",
    # Trackable placeholders — substituted when expanding a TB log template.
    "tb_name", "tb_code", "tb_owner",
)


def _placeholder_values(
    *, cache, log_type: str, log_date, username: str, trackable=None,
) -> dict[str, str]:
    """Build the substitution dict for ``expand_placeholders``.

    Cache-derived fields are pulled from ``cache`` if set, otherwise empty.
    Numeric values are formatted naturally (no trailing ".0" for whole stars).
    ``trackable`` is an optional object exposing ``name``/``reference_code``/
    ``owner_name`` attributes (or a dict with those keys) — used for TB log
    templates.
    """
    from datetime import date as _date

    def _stars(v):
        if v is None:
            return ""
        f = float(v)
        return f"{f:g}"  # 2.0 → "2", 2.5 → "2.5"

    today = _date.today()
    values: dict[str, str] = {
        "today":     today.isoformat(),
        "log_date":  (log_date.isoformat() if log_date else today.isoformat()),
        "log_type":  log_type or "",
        "username":  username or "",
    }
    if cache is not None:
        values.update({
            "name":         cache.name or "",
            "gc_code":      cache.gc_code or "",
            "oc_code":      cache.oc_code or "",
            "type":         cache.cache_type or "",
            "size":         cache.size or "",
            "difficulty":   _stars(cache.difficulty),
            "terrain":      _stars(cache.terrain),
            "owner":        cache.owner or "",
            "hidden_date":  cache.hidden_date.isoformat() if cache.hidden_date else "",
            "country":      cache.country or "",
            "state":        cache.state or "",
            "lat":          f"{cache.latitude:.6f}" if cache.latitude is not None else "",
            "lon":          f"{cache.longitude:.6f}" if cache.longitude is not None else "",
        })
    else:
        for key in ("name", "gc_code", "oc_code", "type", "size", "difficulty",
                    "terrain", "owner", "hidden_date", "country", "state",
                    "lat", "lon"):
            values.setdefault(key, "")

    def _tb_attr(obj, attr, key):
        if obj is None:
            return ""
        if isinstance(obj, dict):
            return obj.get(key) or obj.get(attr) or ""
        return getattr(obj, attr, "") or ""

    values["tb_name"]  = _tb_attr(trackable, "name", "name")
    values["tb_code"]  = _tb_attr(trackable, "reference_code", "ref_code")
    values["tb_owner"] = _tb_attr(trackable, "owner_name", "owner")
    return values


def expand_placeholders(
    text: str,
    *,
    cache=None,
    user=None,
    log_type: str = "",
    log_date=None,
    trackable=None,
) -> str:
    """Expand ``[name]``-style placeholders against the current context.

    Substitutes the cache/log/user keys in :data:`PLACEHOLDER_KEYS`. The
    ``[find_count]`` placeholder is intentionally **not** expanded here —
    it's substituted client-side at insert time from the value of the
    "Find #" input (see ``gcfFmtInsertTemplate`` in the compose toolbar),
    which is the user-visible source of truth.

    Unknown placeholders are left as-is so the user sees what didn't match.
    """
    if not text:
        return text

    username = ""
    if user is not None:
        username = getattr(user, "username", "") or str(user)
    else:
        from preferences.models import UserPreference
        username = (
            UserPreference.get("gc_username", "")
            or UserPreference.get("oc_username", "")
            or ""
        )

    values = _placeholder_values(
        cache=cache, log_type=log_type, log_date=log_date, username=username,
        trackable=trackable,
    )

    for key in PLACEHOLDER_KEYS:
        if key == "find_count":
            continue
        token = f"[{key}]"
        if token in text and key in values:
            text = text.replace(token, values[key])
    return text
