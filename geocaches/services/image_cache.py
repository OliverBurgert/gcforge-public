"""Generalized local image cache.

Two entry points cover the two halves of the design (`docs/trackables-plan.md`
covers the trackables side; cache/ALC sides will hook here as those slices
land):

  - ``prefetch(source_url, *, category)`` — called from sync code while the
    URL is in hand. Downloads now if not already cached. Best-effort.

  - ``url_for(source_url, *, category)`` — called from templates / JSON
    serializers. Returns the URL to use in HTML: a proxy URL that the
    browser will request, which on miss downloads + records + serves.

Phase A scope: download, store, serve. Settings toggles and tracker
exclusions arrive in Phase B and short-circuit ``url_for`` to return the
original URL.
"""
from __future__ import annotations

import hashlib
import logging
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from email.utils import formatdate
from pathlib import Path
from typing import Optional

from django.conf import settings
from django.urls import reverse

logger = logging.getLogger(__name__)

CACHE_ROOT: Path = settings.DATA_DIR / "cached_images"

# The CachedImage row write is serialised through the process-wide
# ``db_write()`` lock (geocaches.db_lock) — the same re-entrant lock the
# importers / enrichment / delete paths use — so all in-process writers share a
# single serializer. A page that renders many uncached images fires a burst of
# concurrent proxy misses; on SQLite those parallel update_or_create writes
# collide ("database is locked"), and an unhandled error turns into a 500 →
# broken image on first load (fine after reload, when the file is a cache hit).
# Downloads stay parallel; only the brief write is serialised.

# Category metadata for the settings UI.
#   label:     display name
#   splits:    sub-toggles for f/u/m-style enable flags ([] = single toggle)
#   default:   default value when the user hasn't toggled anything
CATEGORIES: dict[str, dict] = {
    "tb_icon":              {"label": "Trackable type icons",          "splits": [],                          "default": True},
    "tb_listing":           {"label": "Trackable listing images",      "splits": [],                          "default": True},
    "tb_log":               {"label": "Trackable log images",          "splits": [],                          "default": False},
    "cache_listing_gc":     {"label": "Cache listing images (GC/OC-hosted)", "splits": ["found", "unfound", "mine"], "default": False},
    "cache_listing_other":  {"label": "Cache listing images (other hosts)", "splits": ["found", "unfound", "mine"], "default": False},
    "cache_log":            {"label": "Cache log images",              "splits": ["found", "unfound", "mine"], "default": False},
    "alc":                  {"label": "Adventure Lab images",          "splits": ["found", "unfound", "mine"], "default": False},
    "souvenir":             {"label": "Souvenir images",               "splits": [],                          "default": True},
    "treasure":             {"label": "Treasure images",               "splits": [],                          "default": True},
}

DEFAULT_EXCLUSIONS = "\n".join([
    # External stats / tracker / counter badges
    "project-gc.com",
    "gsak.net",
    "maxmind.com",
    # Smileys + small icons inlined in cache logs / descriptions —
    # tiny shared assets, no point caching per-log copies.
    "/images/icons/",
    "/images/attributes/",
    "/resource2/tinymce/",
    "/StatBar/",
])


def is_category_enabled(category: str, *, state: str = "") -> bool:
    """Return True if caching is enabled for the (category, state) pair.

    ``state`` is one of "" / "found" / "unfound" / "mine"; ignored for
    categories that don't expose a state split.
    """
    from preferences.models import UserPreference
    meta = CATEGORIES.get(category)
    if meta is None:
        return False
    if meta["splits"]:
        sub = state if state in meta["splits"] else meta["splits"][0]
        return bool(UserPreference.get(_pref_key(category, sub), meta["default"]))
    return bool(UserPreference.get(_pref_key(category), meta["default"]))


def is_excluded(url: str) -> bool:
    """Substring-match against the user's tracker exclusion list."""
    if not url:
        return False
    from preferences.models import UserPreference
    raw = UserPreference.get("image_cache.exclusions", DEFAULT_EXCLUSIONS) or ""
    for line in raw.splitlines():
        token = line.strip()
        if token and token in url:
            return True
    return False


def disk_usage_by_category() -> dict[str, dict]:
    """Aggregated stats per category for the settings UI."""
    from django.db.models import Count, Sum
    from geocaches.models import CachedImage
    rows = (
        CachedImage.objects.values("category")
        .annotate(n=Count("id"), b=Sum("bytes"))
    )
    out = {}
    for r in rows:
        out[r["category"]] = {"count": r["n"], "bytes": r["b"] or 0}
    return out


def clear_category(category: str) -> int:
    """Delete every cached image (and its file) for a category. Returns count."""
    from geocaches.models import CachedImage
    n = 0
    for img in CachedImage.objects.filter(category=category):
        _path_for(img).unlink(missing_ok=True)
        img.delete()
        n += 1
    return n


def clear_excluded() -> int:
    """Delete cached images whose source URL matches the current exclusion list.

    Lets the user retroactively purge files that were cached before a
    pattern was added to the exclusions.
    """
    from geocaches.models import CachedImage
    n = 0
    for img in CachedImage.objects.all().iterator(chunk_size=500):
        if is_excluded(img.source_url):
            _path_for(img).unlink(missing_ok=True)
            img.delete()
            n += 1
    return n


def sweep_non_image_files() -> int:
    """Drop cached files whose stored bytes don't look like an image.

    Each row's file is opened, the first ~256 bytes sniffed via
    :func:`_looks_like_image`. Non-image hits delete both the row and the
    file. Catches content-type spoofing (e.g. parked-domain landing pages
    served as ``text/html`` but pointed to by an image URL) that may have
    bypassed earlier versions of the download guards.
    """
    from geocaches.models import CachedImage
    n = 0
    for img in CachedImage.objects.all().iterator(chunk_size=200):
        path = _path_for(img)
        if not path.is_file():
            continue
        try:
            with path.open("rb") as fh:
                head = fh.read(512)
        except OSError as exc:
            logger.warning("sweep_non_image_files: read %s: %s", path, exc)
            continue
        if _looks_like_image(head, img.mime_type or ""):
            continue
        path.unlink(missing_ok=True)
        img.delete()
        n += 1
    return n


def sweep_orphan_files() -> int:
    """Delete files in CACHE_ROOT that have no matching CachedImage row.

    Useful after edge cases like a row delete with the file write surviving,
    a partial migration, or external cleanup of the DB. Walks every category
    directory and compares filenames against the registry.
    """
    if not CACHE_ROOT.exists():
        return 0
    from geocaches.models import CachedImage
    known: set[tuple[str, str]] = set(
        CachedImage.objects.values_list("category", "filename")
    )
    n = 0
    for cat_dir in CACHE_ROOT.iterdir():
        if not cat_dir.is_dir():
            continue
        category = cat_dir.name
        for path in cat_dir.iterdir():
            if not path.is_file():
                continue
            if (category, path.name) in known:
                continue
            try:
                path.unlink()
                n += 1
            except OSError as exc:
                logger.warning("sweep_orphan_files: could not delete %s: %s", path, exc)
    return n


def clear_older_than(days: int) -> int:
    """Delete cached images last seen more than ``days`` ago. Returns count.

    ``days <= 0`` is a no-op (returns 0). Use that to disable auto-delete.
    """
    from datetime import timedelta
    from geocaches.models import CachedImage
    if days <= 0:
        return 0
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    n = 0
    for img in CachedImage.objects.filter(last_seen_at__lt=cutoff):
        _path_for(img).unlink(missing_ok=True)
        img.delete()
        n += 1
    return n


def auto_delete_days() -> int:
    """Days threshold for the auto-prune that runs at the start of every
    'Download missing offline images' task. ``0`` disables auto-prune.
    """
    from preferences.models import UserPreference
    try:
        return max(0, int(UserPreference.get("image_cache.auto_delete_days", 0)))
    except (ValueError, TypeError):
        return 0


def _pref_key(category: str, sub: str = "") -> str:
    return f"image_cache.{category}" + (f".{sub}" if sub else "")


# ── State + category helpers ──────────────────────────────────────────────────

_GC_HOSTS = (
    "geocaching.com",
    "img.geocaching.com",
    "opencaching.de",
    "opencaching.com",
)


def category_for_cache_image(url: str) -> str:
    """Return ``cache_listing_gc`` or ``cache_listing_other`` based on URL host."""
    try:
        host = urllib.parse.urlsplit(url).netloc.lower()
    except Exception:  # noqa: BLE001
        return "cache_listing_other"
    if any(host.endswith(h) for h in _GC_HOSTS):
        return "cache_listing_gc"
    return "cache_listing_other"


def state_for_cache(cache) -> str:
    """Return ``mine`` / ``found`` / ``unfound`` for a Geocache row.

    Best-effort: ``mine`` is decided by case-insensitive match on the
    user's GC/OC usernames (from ``UserAccount``, fallback to the
    ``gc_username`` preference).
    """
    if cache is None:
        return ""
    usernames = _my_usernames()
    owner = (getattr(cache, "owner", "") or "").lower()
    placed_by = (getattr(cache, "placed_by", "") or "").lower()
    if usernames and (owner in usernames or placed_by in usernames):
        return "mine"
    if getattr(cache, "found", False):
        return "found"
    return "unfound"


def _my_usernames() -> set[str]:
    from accounts.models import UserAccount
    from preferences.models import UserPreference
    names = {u.lower() for u in UserAccount.objects.values_list("username", flat=True) if u}
    fallback = (UserPreference.get("gc_username", "") or "").strip().lower()
    if fallback:
        names.add(fallback)
    return names


# ── Inline HTML <img src> rewriting ──────────────────────────────────────────

# Matches src="..." or src='...' on an <img> tag. Captures (prefix, url, suffix)
# so the substitution preserves the quoting style intact.
_IMG_SRC_RE = re.compile(
    r'(<img\b[^>]*?\bsrc\s*=\s*)(["\'])([^"\']+)(["\'])',
    re.IGNORECASE,
)


def rewrite_html_images(html: str, *, resolver) -> str:
    """Rewrite ``<img src>`` URLs in ``html`` via the given resolver.

    ``resolver(url) -> str`` is called per image URL and returns the URL to
    substitute. This indirection lets callers pick the category per URL (e.g.
    cache_listing_gc vs cache_listing_other based on host).
    """
    if not html or "<img" not in html.lower():
        return html

    def _sub(match):
        prefix, q1, src, q2 = match.groups()
        return f"{prefix}{q1}{resolver(src)}{q2}"

    return _IMG_SRC_RE.sub(_sub, html)


def prefetch_html_images(html: str, *, resolver_prefetch) -> int:
    """Walk ``<img src>`` URLs and feed each through ``resolver_prefetch(url)``.

    Returns the number of URLs attempted.
    """
    if not html or "<img" not in html.lower():
        return 0
    n = 0
    for match in _IMG_SRC_RE.finditer(html):
        src = match.group(3)
        if src:
            resolver_prefetch(src)
            n += 1
    return n


def rewrite_html_for_cache(html: str, cache) -> str:
    """Rewrite img URLs in a cache-description HTML body.

    Per-URL category split (gc/other) based on host; state from the cache.
    """
    state = state_for_cache(cache)
    lid = cache.pk
    def _resolve(src):
        return url_for(src, category=category_for_cache_image(src), state=state,
                       linked_type="geocache", linked_id=lid)
    return rewrite_html_images(html, resolver=_resolve)


def prefetch_html_for_cache(html: str, cache) -> int:
    state = state_for_cache(cache)
    def _do(src):
        prefetch(src, category=category_for_cache_image(src), state=state)
    return prefetch_html_images(html, resolver_prefetch=_do)


def rewrite_html_for_log(html: str, cache, log=None) -> str:
    state = state_for_cache(cache)
    ltype = "log" if log is not None else ""
    lid = log.pk if log is not None else 0
    def _resolve(src):
        return url_for(src, category="cache_log", state=state,
                       linked_type=ltype, linked_id=lid)
    return rewrite_html_images(html, resolver=_resolve)


def prefetch_html_for_log(html: str, cache) -> int:
    state = state_for_cache(cache)
    def _do(src):
        prefetch(src, category="cache_log", state=state)
    return prefetch_html_images(html, resolver_prefetch=_do)


def rewrite_html_for_tb_log(html: str, trackable) -> str:
    """Rewrite <img src> URLs in a TB-log text body (tb_log category)."""
    def _resolve(src):
        return url_for(src, category="tb_log")
    return rewrite_html_images(html, resolver=_resolve)


def prefetch_html_for_tb_log(html: str, trackable, *, log=None) -> int:
    def _do(src):
        linked = {"trackable": trackable}
        if log is not None:
            linked["trackable_log"] = log
        prefetch(src, category="tb_log", linked=linked)
    return prefetch_html_images(html, resolver_prefetch=_do)


def url_for(
    source_url: str,
    *,
    category: str,
    state: str = "",
    linked_type: str = "",
    linked_id: int = 0,
) -> str:
    """Return the URL to render for a given source URL + category.

    When caching is disabled (toggle off or URL on the exclusion list),
    returns the source URL directly so the browser still gets the image
    but bypasses our server entirely.

    ``linked_type`` / ``linked_id`` encode the owning entity into the proxy
    URL so that ``serve_proxy`` can attach the M2M link on first serve.
    """
    if not source_url:
        return ""
    if not source_url.startswith(("http://", "https://")):
        return source_url
    if is_excluded(source_url) or not is_category_enabled(category, state=state):
        return source_url
    proxy = reverse("geocaches:cached_image_proxy") + "?u=" + urllib.parse.quote(source_url, safe="") + "&c=" + category
    if state:
        proxy += "&s=" + state
    if linked_type and linked_id:
        proxy += f"&etype={linked_type}&eid={linked_id}"
    return proxy


def prefetch(
    source_url: str,
    *,
    category: str,
    state: str = "",
    linked: Optional[dict] = None,
) -> Optional["CachedImage"]:  # noqa: F821
    """Download ``source_url`` and record it as a cached image. Idempotent.

    Honors the per-category toggle and the exclusion list; returns ``None``
    when caching is off for this category or the URL matches an exclusion.
    Existing rows older than the user's refresh interval are re-checked
    via conditional GET.

    ``linked`` attaches owner entities so an entity-delete signal can
    cascade-purge the row when its last link is gone. Recognised keys:
    ``geocache``, ``trackable``, ``adventure``, ``log``, ``trackable_log``.
    """
    from geocaches.models import CachedImage
    if not source_url or not source_url.startswith(("http://", "https://")):
        return None
    if is_excluded(source_url) or not is_category_enabled(category, state=state):
        return None
    existing = CachedImage.objects.filter(category=category, source_url=source_url).first()
    if existing and (_path_for(existing).exists()):
        if _needs_refresh(existing):
            existing = _refresh(existing)
        else:
            existing.save(update_fields=["last_seen_at"])
        _attach_links(existing, linked)
        return existing
    img = _download(source_url, category=category)
    if img is not None:
        _attach_links(img, linked)
    return img


_LINK_FIELD_BY_KEY = {
    "geocache":      "linked_geocaches",
    "trackable":     "linked_trackables",
    "adventure":     "linked_adventures",
    "log":           "linked_logs",
    "trackable_log": "linked_trackable_logs",
}


def _attach_links(img, linked: Optional[dict]) -> None:
    if not linked or img is None:
        return
    for key, owner in linked.items():
        if owner is None:
            continue
        field_name = _LINK_FIELD_BY_KEY.get(key)
        if not field_name:
            continue
        # Best-effort: this M2M write fires on every proxy serve for linked
        # categories, so a burst can contend on SQLite. The link is re-added on
        # the next serve, so a transient failure must not break the image.
        try:
            getattr(img, field_name).add(owner)
        except Exception as exc:  # noqa: BLE001
            logger.warning("image_cache._attach_links(%s) failed: %s", field_name, exc)


_PROXY_ENTITY_MODELS = {
    "geocache":     "geocaches.Geocache",
    "log":          "geocaches.Log",
    "trackable":    "geocaches.Trackable",
    "adventure":    "geocaches.Adventure",
    "trackable_log": "geocaches.TrackableLog",
}


def _linked_from_request(request) -> Optional[dict]:
    etype = request.GET.get("etype", "")
    eid   = request.GET.get("eid", "")
    if not etype or not eid:
        return None
    label = _PROXY_ENTITY_MODELS.get(etype)
    if not label:
        return None
    try:
        eid_int = int(eid)
    except (ValueError, TypeError):
        return None
    from django.apps import apps
    model = apps.get_model(label)
    entity = model.objects.filter(pk=eid_int).first()
    if entity is None:
        return None
    return {etype: entity}


def serve_proxy(request):
    """View entry point for ``/cached-images/proxy/?u=&c=&s=``."""
    from django.http import HttpResponseBadRequest, HttpResponseRedirect
    from geocaches.models import CachedImage

    source_url = request.GET.get("u", "").strip()
    category   = request.GET.get("c", "").strip()
    state      = request.GET.get("s", "").strip()
    if not source_url or not category:
        return HttpResponseBadRequest("missing u or c")

    if is_excluded(source_url) or not is_category_enabled(category, state=state):
        return HttpResponseRedirect(source_url)

    linked = _linked_from_request(request)

    img = CachedImage.objects.filter(category=category, source_url=source_url).first()
    if img:
        path = _path_for(img)
        if path.is_file():
            # last_seen_at is bookkeeping — never let its write (which contends
            # on a cache-hit burst, e.g. a reload of a many-image page) break
            # the image serve.
            try:
                img.save(update_fields=["last_seen_at"])
            except Exception as exc:  # noqa: BLE001
                logger.warning("image_cache: last_seen_at bump failed: %s", exc)
            if linked:
                _attach_links(img, linked)
            return _file_response(path, img.mime_type)

    img = _download(source_url, category=category)
    if img is None:
        return HttpResponseRedirect(source_url)
    if linked:
        _attach_links(img, linked)
    return _file_response(_path_for(img), img.mime_type)


def refresh_days() -> int:
    """Days after which a cached image is checked against the source.

    0 disables the refresh entirely.
    """
    from preferences.models import UserPreference
    try:
        return max(0, int(UserPreference.get("image_cache.refresh_days", 30)))
    except (ValueError, TypeError):
        return 30


def refresh_stale(*, days: Optional[int] = None) -> int:
    """Re-check every cached image older than ``days`` (default: settings value).

    Each row goes through :func:`_refresh` which does a conditional GET and
    only overwrites on 200. Returns the count visited.
    """
    from geocaches.models import CachedImage
    threshold = refresh_days() if days is None else days
    if threshold <= 0:
        return 0
    cutoff = datetime.now(timezone.utc) - timedelta(days=threshold)
    n = 0
    for img in CachedImage.objects.filter(downloaded_at__lt=cutoff).iterator(chunk_size=200):
        _refresh(img)
        n += 1
    return n


def _needs_refresh(img) -> bool:
    days = refresh_days()
    if days <= 0:
        return False
    return img.downloaded_at < datetime.now(timezone.utc) - timedelta(days=days)


def _refresh(img):
    """Conditional GET; 304 keeps the file + bumps downloaded_at, 200 overwrites."""
    from geocaches.models import CachedImage
    target = _path_for(img)
    if not target.exists():
        return _download(img.source_url, category=img.category)

    mtime = target.stat().st_mtime
    req = urllib.request.Request(
        img.source_url,
        headers={
            "User-Agent": "GCForge/0.1",
            "If-Modified-Since": formatdate(mtime, usegmt=True),
        },
    )
    now = datetime.now(timezone.utc)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = resp.read()
            mime = resp.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        if not _looks_like_image(data, mime):
            logger.info(
                "image_cache._refresh(%s): upstream now returns non-image (mime=%r); keeping cached file",
                img.source_url, mime,
            )
            CachedImage.objects.filter(pk=img.pk).update(downloaded_at=now)
        else:
            target.write_bytes(data)
            CachedImage.objects.filter(pk=img.pk).update(
                mime_type=mime or img.mime_type,
                bytes=len(data),
                downloaded_at=now,
            )
    except urllib.error.HTTPError as exc:
        if exc.code == 304:
            CachedImage.objects.filter(pk=img.pk).update(downloaded_at=now)
        else:
            logger.warning("image_cache._refresh(%s): HTTP %d", img.source_url, exc.code)
            CachedImage.objects.filter(pk=img.pk).update(downloaded_at=now)
    except Exception as exc:  # noqa: BLE001
        logger.warning("image_cache._refresh(%s): %s", img.source_url, exc)
        CachedImage.objects.filter(pk=img.pk).update(downloaded_at=now)
    img.refresh_from_db()
    return img


def _download(source_url: str, *, category: str):
    from geocaches.models import CachedImage
    try:
        target_dir = CACHE_ROOT / category
        target_dir.mkdir(parents=True, exist_ok=True)
        req = urllib.request.Request(source_url, headers={"User-Agent": "GCForge/0.1"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = resp.read()
            mime = resp.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
    except Exception as exc:  # noqa: BLE001
        logger.warning("image_cache._download(%s, %s): %s", category, source_url, exc)
        return None

    if not _looks_like_image(data, mime):
        logger.info(
            "image_cache._download(%s): non-image response rejected (mime=%r, head=%r)",
            source_url, mime, data[:16].hex(),
        )
        return None

    filename = _filename_for(source_url, mime=mime)
    target = target_dir / filename
    try:
        target.write_bytes(data)
    except Exception as exc:  # noqa: BLE001
        logger.warning("image_cache._download(%s) write failed: %s", source_url, exc)
        return None

    try:
        from geocaches.db_lock import db_write
        with db_write():
            img, _ = CachedImage.objects.update_or_create(
                category=category,
                source_url=source_url,
                defaults={
                    "filename":  filename,
                    "mime_type": mime or _guess_mime(filename),
                    "bytes":     len(data),
                },
            )
    except Exception as exc:  # noqa: BLE001 — transient DB contention; serve_proxy falls back to source
        logger.warning("image_cache._download(%s) record write failed: %s", source_url, exc)
        return None
    return img


# Magic-byte prefixes for the formats we expect. SVG is text — handled by the
# mime check below since there's no reliable byte signature.
_IMAGE_MAGIC = (
    b"\x89PNG\r\n\x1a\n",  # png
    b"\xff\xd8\xff",         # jpeg
    b"GIF87a", b"GIF89a",    # gif
    b"BM",                   # bmp
    b"II*\x00", b"MM\x00*",  # tiff
)


def _looks_like_image(data: bytes, mime: str) -> bool:
    """Return True when ``data`` is plausibly an image.

    Combines a Content-Type check with magic-byte sniffing so servers that
    mis-label image responses (or that route image URLs to HTML landing
    pages) don't poison the cache.
    """
    if not data:
        return False
    mime = (mime or "").lower()
    if mime.startswith("image/"):
        return True
    if any(data.startswith(sig) for sig in _IMAGE_MAGIC):
        return True
    # WebP: RIFF....WEBP
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return True
    # SVG: detect by a quick start-tag scan (can't rely on bytes here).
    head = data[:200].lstrip().lower()
    if head.startswith(b"<svg") or (head.startswith(b"<?xml") and b"<svg" in data[:512].lower()):
        return True
    return False


_MIME_TO_EXT = {
    "image/png":     "png",
    "image/jpeg":    "jpg",
    "image/jpg":     "jpg",
    "image/gif":     "gif",
    "image/webp":    "webp",
    "image/svg+xml": "svg",
    "image/bmp":     "bmp",
    "image/tiff":    "tif",
}


def _filename_for(source_url: str, *, mime: str = "") -> str:
    """Build the on-disk filename: md5-prefix + extension.

    Extension preference: 1) URL tail (jpg/png/gif/…), 2) mime-type lookup,
    3) literal ``img`` as a last resort.
    """
    h = hashlib.md5(source_url.encode("utf-8")).hexdigest()[:20]
    ext = ""
    # Strip any query string before sniffing the URL tail
    path_only = source_url.split("?", 1)[0]
    tail = path_only.rsplit(".", 1)
    if len(tail) == 2 and 0 < len(tail[1]) <= 5 and tail[1].isalnum():
        candidate = tail[1].lower()
        if candidate in _MIME_TO_EXT.values():
            ext = candidate
    if not ext and mime:
        ext = _MIME_TO_EXT.get(mime, "")
    if not ext:
        ext = "img"
    return f"{h}.{ext}"


def _path_for(img) -> Path:
    return CACHE_ROOT / img.category / img.filename


def _guess_mime(filename: str) -> str:
    ext = filename.rsplit(".", 1)[-1].lower()
    return {
        "jpg":  "image/jpeg", "jpeg": "image/jpeg",
        "png":  "image/png",  "gif":  "image/gif",
        "webp": "image/webp", "svg":  "image/svg+xml",
    }.get(ext, "application/octet-stream")


def _file_response(path: Path, mime: str):
    from django.http import FileResponse
    resp = FileResponse(path.open("rb"), content_type=mime or "application/octet-stream")
    resp["Cache-Control"] = "public, max-age=3600"
    return resp
