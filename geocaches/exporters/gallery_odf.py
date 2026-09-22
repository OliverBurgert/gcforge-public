from __future__ import annotations

import html
import io
import re
from html.parser import HTMLParser

from geocaches.services.gallery import collect_cache_images, local_path_for


_BLOCK_TAGS = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "pre"}


class _HtmlToParagraphs(HTMLParser):
    """Collect HTML body text as a list of paragraph strings.

    Block tags (<p>, <br>, <div>, <li>, …) break paragraphs; all other tags
    are stripped and their text content kept. <img>/<script>/<style> are
    dropped silently.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._buf: list[str] = []
        self._paragraphs: list[str] = []
        self._skip_depth = 0

    def _flush(self) -> None:
        text = "".join(self._buf).strip()
        if text:
            text = re.sub(r"\s+", " ", text)
            self._paragraphs.append(text)
        self._buf = []

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "img"):
            self._skip_depth += 1
            return
        if tag in _BLOCK_TAGS:
            self._flush()

    def handle_endtag(self, tag):
        if tag in ("script", "style", "img"):
            if self._skip_depth > 0:
                self._skip_depth -= 1
            return
        if tag in _BLOCK_TAGS:
            self._flush()

    def handle_data(self, data):
        if self._skip_depth:
            return
        self._buf.append(data)

    def close(self):
        super().close()
        self._flush()
        return self._paragraphs


def _html_to_paragraphs(value: str) -> list[str]:
    if not value:
        return []
    if "<" not in value:
        return [s.strip() for s in re.split(r"\n{2,}", value) if s.strip()]
    p = _HtmlToParagraphs()
    p.feed(value)
    return p.close()


def _note_to_paragraphs(note) -> list[str]:
    body = note.body or ""
    fmt = (getattr(note, "format", "") or "plain").lower()
    if fmt == "html":
        return _html_to_paragraphs(body)
    # plain / markdown / unknown: keep markdown source as-is, just split on blank lines
    body = html.unescape(body)
    return [s.strip() for s in re.split(r"\n{2,}", body) if s.strip()]


def build_odf(caches, options: dict) -> bytes:
    """Assemble an ODF text document with per-cache sections."""
    from odf.opendocument import OpenDocumentText
    from odf.style import Style, TextProperties
    from odf.text import H, P
    from odf.draw import Frame, Image

    doc = OpenDocumentText()

    h1style = Style(name="Heading1", family="paragraph")
    h1style.addElement(TextProperties(fontsize="16pt", fontweight="bold"))
    doc.styles.addElement(h1style)

    img_size = options.get("image_size", "page_width")

    def _img_width() -> str:
        if img_size == "no_resize":
            return "14cm"
        elif img_size == "max_width":
            px = options.get("max_width_px", 800)
            cm = round(px * 0.0264583, 1)
            return f"{cm}cm"
        return "14cm"

    def _add_paragraph(text: str) -> None:
        p = P()
        p.addText(text)
        doc.text.addElement(p)

    for cache in caches:
        display_code = cache.gc_code or cache.oc_code or cache.al_code
        title = f"{display_code} — {cache.name}"
        h = H(outlinelevel=1, stylename="Heading1")
        h.addText(title)
        doc.text.addElement(h)

        if options.get("include_coords", False):
            lat = cache.effective_latitude
            lon = cache.effective_longitude
            _add_paragraph(f"Coords: {lat:.5f}, {lon:.5f}")

        if options.get("include_short_description", False) and cache.short_description:
            for para in _html_to_paragraphs(cache.short_description):
                _add_paragraph(para)

        if options.get("include_long_description", False) and cache.long_description:
            for para in _html_to_paragraphs(cache.long_description):
                _add_paragraph(para)

        if options.get("user_notes", True):
            for note in cache.notes.filter(note_type="note"):
                for para in _note_to_paragraphs(note):
                    _add_paragraph(para)

        imgs = collect_cache_images(cache, options)
        w = _img_width()
        for item in imgs:
            local = local_path_for(item["url"])
            if local is None:
                continue
            suffix = local.suffix.lstrip(".").lower() or "jpeg"
            mime = f"image/{suffix.replace('jpg', 'jpeg')}"
            href = doc.addPicture(str(local), mediatype=mime)
            frame = Frame(width=w, height="8cm", anchortype="paragraph")
            img_el = Image(href=href)
            frame.addElement(img_el)
            p = P()
            p.addElement(frame)
            doc.text.addElement(p)
            if item.get("caption"):
                _add_paragraph(item["caption"])

        if options.get("notes_box", False):
            _add_paragraph(" ")

    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()
