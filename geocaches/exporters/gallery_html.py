from __future__ import annotations

import io
import urllib.request
import zipfile
from typing import Optional

from django.template.loader import render_to_string

from geocaches.services.gallery import collect_cache_images, local_path_for


def _fetch_to_bytes(url: str) -> Optional[bytes]:
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            return r.read()
    except Exception:
        return None


def build_html_zip(caches, options: dict) -> bytes:
    """Render the HTML bundle and return the ZIP as bytes."""
    cache_sections = []
    filename_map: dict[str, str] = {}  # source_url -> images/<name>
    name_used: set[str] = set()

    def _reserve(url: str) -> str:
        if url in filename_map:
            return filename_map[url]
        local = local_path_for(url)
        base = local.name if local else url.split("?", 1)[0].rsplit("/", 1)[-1] or "image"
        stem, _, ext = base.rpartition(".")
        if not stem:
            stem, ext = base, "img"
        candidate = f"{stem}.{ext}"
        n = 1
        while candidate in name_used:
            candidate = f"{stem}_{n}.{ext}"
            n += 1
        name_used.add(candidate)
        filename_map[url] = candidate
        return candidate

    for cache in caches:
        imgs = collect_cache_images(cache, options)
        section_imgs = []
        for item in imgs:
            rel = _reserve(item["url"])
            section_imgs.append({**item, "rel_path": f"images/{rel}"})
        notes = (
            list(cache.notes.filter(note_type="note"))
            if options.get("user_notes")
            else []
        )
        cache_sections.append({
            "cache": cache,
            "images": section_imgs,
            "notes": notes,
            "options": options,
        })

    html = render_to_string(
        "geocaches/tools/image_gallery_bundle.html",
        {"sections": cache_sections, "options": options},
    )

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("index.html", html.encode("utf-8"))
        for url, rel_name in filename_map.items():
            local = local_path_for(url)
            if local:
                zf.write(local, f"images/{rel_name}")
            else:
                data = _fetch_to_bytes(url)
                if data:
                    zf.writestr(f"images/{rel_name}", data)
                # if fetch also fails, omit the file — the img tag will show broken
    return buf.getvalue()
