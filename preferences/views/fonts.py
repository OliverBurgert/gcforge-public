from pathlib import Path

from django.conf import settings
from django.http import HttpResponse
from django.views.decorators.cache import cache_control

# Fallback chain for fonts not in our local set (e.g. proprietary faces).
# Maps any unrecognised font name to its closest local equivalent.
_FONT_FALLBACKS = {
    "Arial Unicode MS Regular": "Noto Sans Regular",
    "Arial Unicode MS Bold":    "Noto Sans Bold",
}


@cache_control(max_age=86400)
def serve_map_glyph(request, fontstack, range_str):
    """
    Serve MapLibre PBF font glyphs from locally hosted files.

    fontstack may be a comma-separated list (e.g. "Open Sans Regular,Noto Sans Regular").
    We try each font in order; unknown fonts are resolved via _FONT_FALLBACKS before
    falling through to Noto Sans Regular as a last resort.

    Emoji/supplementary ranges not covered by any local font return 404, which
    triggers MapLibre's built-in local glyph renderer for those codepoints.
    """
    fonts_dir = Path(settings.BASE_DIR) / "static" / "fonts"
    fonts = [f.strip() for f in fontstack.split(",")]

    candidates = []
    for font in fonts:
        candidates.append(font)
        if font in _FONT_FALLBACKS:
            candidates.append(_FONT_FALLBACKS[font])
    candidates.append("Noto Sans Regular")  # ultimate fallback

    for font in candidates:
        pbf_path = fonts_dir / font / f"{range_str}.pbf"
        if pbf_path.exists():
            return HttpResponse(
                pbf_path.read_bytes(),
                content_type="application/x-protobuf",
            )

    return HttpResponse(status=404, content_type="application/x-protobuf")
