"""Bulk-fetch missing offline images across the local DB.

Walks every entity that may have referenced an image URL during sync and
pipes each URL through ``image_cache.prefetch`` (which is idempotent and
respects the per-category enable toggles + exclusion list — so categories
the user has disabled are skipped automatically).

Triggered from the Update menu on the cache list page; runs in the
shared task executor.
"""
from __future__ import annotations

import logging
import time
from typing import Callable

from geocaches.services import image_cache as ic

# Routes to the gcforge log file (see LOGGING in settings); per-image fetches
# done during normal page renders stay silent — only bulk runs are logged.
logger = logging.getLogger("geocaches.image_cache")


def download_missing_images(*, task_info, plan=None, cache_ids=None, trackable_ids=None) -> dict:
    """Walk the local DB for image URLs and prefetch each one.

    Callers usually pre-build the ``plan`` via :func:`build_plan` so they can
    short-circuit when there's nothing to do; the ``cache_ids``/``trackable_ids``
    fallback is kept for direct invocation (e.g. from a management command).
    """
    if plan is None:
        plan = build_plan(cache_ids=cache_ids, trackable_ids=trackable_ids)
    task_info.total = len(plan)
    task_info.phase = "Starting…"

    # Opportunistic auto-prune. 0 disables.
    auto_days = ic.auto_delete_days()
    auto_deleted = 0
    if auto_days > 0:
        task_info.phase = f"Pruning images older than {auto_days}d…"
        auto_deleted = ic.clear_older_than(auto_days)

    enabled_categories = {c for c, _, _ in plan}
    stats: dict[str, dict] = {c: {"attempted": 0, "stored": 0} for c in enabled_categories}

    scope_bits = []
    if cache_ids is not None:
        scope_bits.append(f"{len(cache_ids)} cache(s)")
    if trackable_ids is not None:
        scope_bits.append(f"{len(trackable_ids)} trackable(s)")
    scope_label = ", ".join(scope_bits) or "ad-hoc"
    t0 = time.monotonic()
    logger.info(
        "Image cache bulk fill started: scope=%s, %d URL(s) across %d categor(ies)",
        scope_label, len(plan), len(enabled_categories),
    )

    # Pre-load entities referenced in the plan to avoid per-image DB lookups.
    _entity_cache: dict[tuple, object] = {}
    for entry in plan:
        if len(entry) == 5:
            _, _, _, etype, eid = entry
            if etype and eid and (etype, eid) not in _entity_cache:
                label = ic._PROXY_ENTITY_MODELS.get(etype)
                if label:
                    from django.apps import apps
                    model = apps.get_model(label)
                    obj = model.objects.filter(pk=eid).first()
                    _entity_cache[(etype, eid)] = obj

    last_category = ""
    for idx, entry in enumerate(plan):
        category, url, state = entry[0], entry[1], entry[2]
        etype = entry[3] if len(entry) > 3 else ""
        eid   = entry[4] if len(entry) > 4 else 0
        if task_info.cancel_event.is_set():
            break
        if category != last_category:
            task_info.phase = ic.CATEGORIES.get(category, {}).get("label", category)
            last_category = category
        linked = None
        if etype and eid:
            entity = _entity_cache.get((etype, eid))
            if entity is not None:
                linked = {etype: entity}
        try:
            res = ic.prefetch(url, category=category, state=state, linked=linked)
            stats[category]["attempted"] += 1
            if res is not None:
                stats[category]["stored"] += 1
        except Exception as exc:  # noqa: BLE001
            logger.warning("image-cache fill %s %s: %s", category, url, exc)
        task_info.completed = idx + 1

    elapsed = round(time.monotonic() - t0, 1)
    cancelled = task_info.cancel_event.is_set()
    summary_parts = [f"{c}={s['stored']}/{s['attempted']}" for c, s in sorted(stats.items())]
    prune_note = f", auto-pruned {auto_deleted}" if auto_deleted else ""
    logger.info(
        "Image cache bulk fill %s in %.1fs (scope=%s): %s%s",
        "cancelled" if cancelled else "finished",
        elapsed, scope_label,
        ", ".join(summary_parts) or "no work",
        prune_note,
    )
    return {"by_category": stats, "total_seen": len(plan), "elapsed_sec": elapsed, "auto_deleted": auto_deleted}


# ── Plan builder ─────────────────────────────────────────────────────────────
# Each plan entry is (category, url, state). state is "" for categories with
# no f/u/m split. We pre-filter on enabled categories to avoid generating
# work for things the user has switched off.

def build_plan(cache_ids=None, trackable_ids=None) -> list[tuple[str, str, str]]:
    """Build a (category, url, state) plan for whatever scopes were passed."""
    out: list[tuple[str, str, str]] = []
    if cache_ids is not None:
        out += _cache_images_and_descriptions(cache_ids)
        out += _cache_logs(cache_ids)
        out += _alc_images(cache_ids)
    if trackable_ids is not None:
        out += _trackable_icons(trackable_ids)
        out += _trackable_listings(trackable_ids)
    return out


def _trackable_icons(trackable_ids):
    if not ic.is_category_enabled("tb_icon"):
        return []
    from geocaches.models import Trackable
    qs = Trackable.objects.exclude(icon_url="")
    if trackable_ids is not None:
        qs = qs.filter(id__in=trackable_ids)
    return [
        ("tb_icon", url, "")
        for url in qs.values_list("icon_url", flat=True)
        if url and url.startswith(("http://", "https://"))
    ]


def _trackable_listings(trackable_ids):
    if not ic.is_category_enabled("tb_listing"):
        return []
    from geocaches.models import TrackableImage
    qs = TrackableImage.objects.filter(log__isnull=True)
    if trackable_ids is not None:
        qs = qs.filter(trackable_id__in=trackable_ids)
    items: list[tuple[str, str, str]] = []
    for url, thumb, large in qs.values_list("url", "thumbnail_url", "large_url"):
        for u in (url, thumb, large):
            if u and u.startswith(("http://", "https://")):
                items.append(("tb_listing", u, ""))
    return items


def _cache_images_and_descriptions(cache_ids):
    # Walk every Geocache once, harvesting its background image, attached
    # Image rows, and any inline <img> tags in the description bodies. State
    # is computed per cache; the per-cache state then drives the per-URL
    # toggle check via is_category_enabled.
    from geocaches.models import Geocache, Image
    out: list[tuple[str, str, str]] = []

    if not _any_cache_listing_enabled():
        return out

    image_qs = Image.objects.all()
    if cache_ids is not None:
        image_qs = image_qs.filter(geocache_id__in=cache_ids)
    image_urls_by_cache: dict[int, list[str]] = {}
    for cache_id, url in image_qs.values_list("geocache_id", "url"):
        if url:
            image_urls_by_cache.setdefault(cache_id, []).append(url)

    caches = Geocache.objects.only(
        "id", "owner", "placed_by", "found",
        "background_image_url", "long_description", "short_description",
    )
    if cache_ids is not None:
        caches = caches.filter(id__in=cache_ids)
    for cache in caches.iterator(chunk_size=200):
        state = ic.state_for_cache(cache)
        for url in image_urls_by_cache.get(cache.id, []):
            _push_cache_url(out, url, state, cache.id)
        if cache.background_image_url:
            _push_cache_url(out, cache.background_image_url, state, cache.id)
        for body in (cache.long_description, cache.short_description):
            if body and "<img" in body.lower():
                ic.prefetch_html_images(body, resolver_prefetch=_make_inline_resolver(out, state, cache.id))
    return out


def _make_inline_resolver(out: list, state: str, cache_id: int = 0) -> Callable[[str], None]:
    def _resolve(url: str) -> None:
        _push_cache_url(out, url, state, cache_id)
    return _resolve


def _push_cache_url(out, url: str, state: str, cache_id: int = 0):
    if not url or not url.startswith(("http://", "https://")):
        return
    cat = ic.category_for_cache_image(url)
    if ic.is_category_enabled(cat, state=state):
        out.append((cat, url, state, "geocache", cache_id))


def _any_cache_listing_enabled() -> bool:
    return any(
        ic.is_category_enabled(cat, state=s)
        for cat in ("cache_listing_gc", "cache_listing_other")
        for s in ("found", "unfound", "mine")
    )


def _cache_logs(cache_ids):
    if not _cache_log_any_enabled():
        return []
    from geocaches.models import Log
    out: list[tuple[str, str, str]] = []

    state_by_cache = _state_by_cache(cache_ids)

    qs = Log.objects.filter(text__icontains="<img").only("geocache_id", "text")
    if cache_ids is not None:
        qs = qs.filter(geocache_id__in=cache_ids)
    for log in qs.iterator(chunk_size=500):
        state = state_by_cache.get(log.geocache_id, "")
        if not ic.is_category_enabled("cache_log", state=state):
            continue
        ic.prefetch_html_images(
            log.text,
            resolver_prefetch=lambda url, s=state, lid=log.id: out.append(("cache_log", url, s, "log", lid)),
        )
    return out


def _cache_log_any_enabled() -> bool:
    return any(
        ic.is_category_enabled("cache_log", state=s) for s in ("found", "unfound", "mine")
    )


def _state_by_cache(cache_ids=None) -> dict[int, str]:
    from geocaches.models import Geocache
    names = ic._my_usernames()
    res: dict[int, str] = {}
    qs = Geocache.objects.all()
    if cache_ids is not None:
        qs = qs.filter(id__in=cache_ids)
    for pk, owner, placed_by, found in qs.values_list(
        "id", "owner", "placed_by", "found",
    ):
        owner = (owner or "").lower()
        placed_by = (placed_by or "").lower()
        if names and (owner in names or placed_by in names):
            res[pk] = "mine"
        elif found:
            res[pk] = "found"
        else:
            res[pk] = "unfound"
    return res


def _alc_images(cache_ids):
    if not _alc_any_enabled():
        return []
    from geocaches.models import Adventure, ALJournalEntry, ALStageDetail
    out: list[tuple[str, str, str]] = []
    state_by_cache = _state_by_cache(cache_ids)

    adv_qs = Adventure.objects.prefetch_related("stages").only("id", "key_image_url")
    if cache_ids is not None:
        adv_qs = adv_qs.filter(stages__id__in=cache_ids).distinct()
    for adv in adv_qs:
        parent = adv.stages.filter(al_detail__isnull=True).first()
        state = state_by_cache.get(parent.id if parent else 0, "unfound")
        if adv.key_image_url:
            out.append(("alc", adv.key_image_url, state, "geocache", parent.id if parent else 0))

    stage_qs = ALStageDetail.objects.all()
    journal_qs = ALJournalEntry.objects.all()
    if cache_ids is not None:
        stage_qs = stage_qs.filter(geocache_id__in=cache_ids)
        journal_qs = journal_qs.filter(geocache_id__in=cache_ids)
    for cache_id, url in stage_qs.values_list("geocache_id", "key_image_url"):
        if url:
            out.append(("alc", url, state_by_cache.get(cache_id, ""), "geocache", cache_id))
    for cache_id, url in journal_qs.values_list("geocache_id", "journal_image_url"):
        if url:
            out.append(("alc", url, state_by_cache.get(cache_id, ""), "geocache", cache_id))
    return out


def _alc_any_enabled() -> bool:
    return any(
        ic.is_category_enabled("alc", state=s) for s in ("found", "unfound", "mine")
    )
