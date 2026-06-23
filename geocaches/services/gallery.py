from __future__ import annotations

from pathlib import Path
from typing import Optional

from django.conf import settings


def collect_cache_images(cache, options: dict) -> list[dict]:
    """Return ordered [{url, caption, source}] for a single cache.

    Excluded URLs are dropped first. Duplicates within the cache are
    removed. Caching is triggered (prefetch) when the category is enabled.
    """
    from geocaches.services.image_cache import (
        _IMG_SRC_RE,
        category_for_cache_image,
        is_category_enabled,
        is_excluded,
        prefetch,
        state_for_cache,
    )

    seen: set[str] = set()
    items: list[dict] = []
    state = state_for_cache(cache)

    def _add(url: str, caption: str, source: str) -> None:
        if not url or url in seen:
            return
        if is_excluded(url):
            return
        seen.add(url)
        cat = category_for_cache_image(url)
        if is_category_enabled(cat, state=state):
            from geocaches.models import CachedImage
            if not CachedImage.objects.filter(category=cat, source_url=url).exists():
                prefetch(url, category=cat, state=state, linked={"geocache": cache})
        items.append({"url": url, "caption": caption, "source": source})

    def _harvest_html(html: str, caption: str, source: str) -> None:
        if not html or "<img" not in html.lower():
            return
        for m in _IMG_SRC_RE.finditer(html):
            src = m.group(3)
            if src:
                _add(src, caption, source)

    # 1. background image
    if cache.background_image_url:
        _add(cache.background_image_url, "Background image", "listing")

    # 2. Image model rows
    for img in cache.images.all():
        raw = (img.name or img.description or "")[:80] or img.url.rsplit("/", 1)[-1]
        _add(img.url, raw, "listing")

    # 3 & 4. inline <img> in short / long descriptions
    _harvest_html(cache.short_description or "", "", "description")
    _harvest_html(cache.long_description or "", "", "description")

    # 5. log images
    if options.get("include_log_images", True):
        for log in cache.logs.all():
            if not log.text:
                continue
            cap = f"{log.user_name}, {log.logged_date}" if log.user_name else str(log.logged_date)
            _harvest_html(log.text, cap, "log")

    # 6. user note images
    if options.get("user_notes", True):
        for note in cache.notes.filter(note_type="note"):
            _harvest_html(note.body or "", "", "note")

    return items


def local_path_for(url: str) -> Optional[Path]:
    """Return the local filesystem path for a cached image URL, or None."""
    from geocaches.models import CachedImage
    from geocaches.services.image_cache import category_for_cache_image

    cat = category_for_cache_image(url)
    img = CachedImage.objects.filter(category=cat, source_url=url).first()
    if img is None:
        return None
    p = settings.DATA_DIR / "cached_images" / img.category / img.filename
    return p if p.exists() else None


def image_url_for_display(url: str, cache) -> str:
    """Return the proxy/display URL for a source URL in the browser gallery."""
    from geocaches.services.image_cache import (
        category_for_cache_image,
        state_for_cache,
        url_for,
    )
    cat = category_for_cache_image(url)
    state = state_for_cache(cache)
    return url_for(url, category=cat, state=state)
