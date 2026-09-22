"""
Souvenir service — fetch, persist and tag geocaching.com souvenirs.

GC souvenir data carries only id/title/description/image/foundDate/url with
no category metadata (see ``docs/reference/geocaching-com.md`` §4), so
categorisation is done with user-managed :class:`SouvenirTag`s.  A
seeded "Countries" tag is auto-applied to country-named souvenirs on first
import.

**No partner-API token needed as of 2026-08-15**: ``refresh_all()``/
``refresh_latest()`` now go through the same ``api_preferred``/
``web_preferred`` resolver convention as ``geocaches.sync.gc_access`` (the
official API when available, falling back to — or, on ``web_preferred``,
always using — the website scrape in ``geocaches.sync.gc_web.souvenirs``).
See ``docs/reference/geocaching-com-web.md`` for how the website path was
confirmed.
"""

from __future__ import annotations

import functools
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

COUNTRIES_TAG = "Countries"


# ---------------------------------------------------------------------------
# Account + country detection (for Countries auto-tag)
# ---------------------------------------------------------------------------

def gc_account():
    """The local GC :class:`UserAccount` (default, else first); ``None`` if none."""
    from accounts.models import UserAccount
    return (
        UserAccount.objects.filter(platform="gc", is_default=True).first()
        or UserAccount.objects.filter(platform="gc").first()
    )


@functools.lru_cache(maxsize=1)
def _country_names() -> frozenset[str]:
    """Lower-cased set of every English country name (name / common / official)."""
    import pycountry
    names: set[str] = set()
    for c in pycountry.countries:
        for attr in ("name", "common_name", "official_name"):
            v = getattr(c, attr, None)
            if v:
                names.add(v.lower())
    return frozenset(names)


def is_country(title: str) -> bool:
    """True when *title* is exactly a country name (used for the Countries tag)."""
    return (title or "").strip().lower() in _country_names()


def _parse_dt(value: str | None):
    """Parse ``foundDateUtc`` (e.g. ``2026-06-05T12:00:00.000``) as aware UTC."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", ""))
    except ValueError:
        return None
    return dt.replace(tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Fetch + persist
# ---------------------------------------------------------------------------

def _upsert(raw: dict, account) -> bool:
    """Insert/update one souvenir from a raw API dict.  Returns True if created.

    On *creation* of a country-named souvenir, the seeded "Countries" tag is
    auto-applied.  We only do this on first import, so a later manual removal of
    the tag sticks.
    """
    from geocaches.models import Souvenir, SouvenirTag
    gid = raw.get("id")
    if gid is None:
        return False
    title = raw.get("title") or ""
    obj, created = Souvenir.objects.update_or_create(
        gc_id=gid,
        defaults={
            "account": account,
            "title": title,
            "description": raw.get("description") or "",
            "image_path": raw.get("imagePath") or "",
            "thumb_image_path": raw.get("thumbImagePath") or "",
            "url": raw.get("url") or "",
            "found_date": _parse_dt(raw.get("foundDateUtc")),
        },
    )
    if created and is_country(title):
        tag, _ = SouvenirTag.objects.get_or_create(name=COUNTRIES_TAG)
        obj.tags.add(tag)
    return created


def _refresh_all_api() -> dict:
    """Page through every souvenir via the official API and upsert.

    Returns ``{added, updated, total}``.
    """
    from geocaches.sync.gc_access import get_gc_client
    account = gc_account()
    client = get_gc_client()
    added = total = 0
    skip, take = 0, 50
    while True:
        page = client.get_my_souvenirs(skip=skip, take=take)
        if not page:
            break
        for raw in page:
            if _upsert(raw, account):
                added += 1
            total += 1
        if len(page) < take:
            break
        skip += take
    return {"added": added, "updated": total - added, "total": total}


def _refresh_latest_api(page_size: int = 10) -> dict:
    """Fetch newest-first in small pages via the official API, stopping once
    a page yields no new souvenirs (we've reached previously-synced territory)."""
    from geocaches.sync.gc_access import get_gc_client
    account = gc_account()
    client = get_gc_client()
    added = seen = 0
    skip = 0
    while True:
        page = client.get_my_souvenirs(skip=skip, take=page_size)
        if not page:
            break
        new_in_page = 0
        for raw in page:
            seen += 1
            if _upsert(raw, account):
                added += 1
                new_in_page += 1
        if new_in_page == 0 or len(page) < page_size:
            break
        skip += page_size
    return {"added": added, "updated": seen - added, "total": seen}


def _refresh_web() -> dict:
    """Refresh via the website — no partner-API token needed.

    **Confirmed live 2026-08-15**: unlike the official API, the website's
    list page (``list_souvenirs_web()``) has no pagination at all — every
    souvenir comes back in one request regardless of count. That collapses
    the API version's "all" (paginate everything) vs. "latest" (paginate
    newest-first, stop early) distinction to a single operation here: the
    list is always complete and cheap, so both ``refresh_all()`` and
    ``refresh_latest()`` call this when the web backend is active.

    The list page doesn't carry ``description``/full image, though — those
    need a separate per-souvenir detail request
    (``fetch_souvenir_detail_web()``). Only fetched for souvenirs **not
    already in the DB**: a souvenir's description/image never change once
    earned, so there's nothing to gain by re-fetching detail for ones we
    already have — unlike the official API's "all" mode, which exists to
    catch pagination gaps, not to refresh unchanging data. That keeps this
    fast and safe to run on every dashboard visit, with no need for a
    background task the way a full per-item detail sweep would.
    """
    from geocaches.sync.gc_web.souvenirs import fetch_souvenir_detail_web, list_souvenirs_web
    from geocaches.models import Souvenir

    account = gc_account()
    rows = list_souvenirs_web()
    existing_ids = set(Souvenir.objects.values_list("gc_id", flat=True))

    added = total = 0
    for raw in rows:
        total += 1
        gid = raw["id"]
        if gid in existing_ids:
            continue
        try:
            detail = fetch_souvenir_detail_web(gid)
            raw = {**raw, **detail}
        except Exception as exc:  # noqa: BLE001
            logger.warning("Souvenir web detail fetch failed for %s (%s): %s", gid, raw.get("title"), exc)
        if _upsert(raw, account):
            added += 1
    return {"added": added, "updated": total - added, "total": total}


def refresh_all() -> dict:
    """Refresh every souvenir via the active GC backend.

    See ``geocaches.sync.gc_access``'s ``api_preferred``/``web_preferred``
    convention: tries the official API first (falling back to the website
    on any failure), or always uses the website when preferred. Returns
    ``{added, updated, total}``.
    """
    return _refresh(_refresh_all_api)


def refresh_latest() -> dict:
    """Refresh recently-earned souvenirs via the active GC backend.

    Same ``api_preferred``/``web_preferred`` convention as ``refresh_all()``.
    """
    return _refresh(_refresh_latest_api)


def _refresh(api_fn) -> dict:
    from geocaches.sync import gc_access

    if gc_access.gc_access_mode() == "web_preferred":
        return _refresh_web()

    from geocaches.feature_flags import gc_api_available
    if gc_api_available():
        try:
            return api_fn()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Souvenir refresh via official API failed, falling back to web: %s", exc)

    return _refresh_web()


# ---------------------------------------------------------------------------
# Read helpers for the dashboard tab
# ---------------------------------------------------------------------------

def _filtered_qs(tag_ids, include_untagged: bool):
    """Souvenirs filtered by the tag checkboxes.

    ``tag_ids`` is a list of selected SouvenirTag ids (``None`` = no filter,
    show all).  With a filter active, a souvenir shows if it has ≥1 selected tag
    or (when *include_untagged*) has no tags at all.
    """
    from django.db.models import Q
    from geocaches.models import Souvenir
    qs = Souvenir.objects.prefetch_related("tags")
    if tag_ids is None:
        return qs
    q = Q()
    if tag_ids:
        q |= Q(tags__in=tag_ids)
    if include_untagged:
        q |= Q(tags__isnull=True)
    if not tag_ids and not include_untagged:
        return qs.none()
    return qs.filter(q).distinct()


def view_data(order: str, tag_ids=None, include_untagged: bool = True) -> dict:
    """Build the list-partial context for an order + tag filter.

    ``order``: ``date`` (flat, newest-first), ``year`` (template regroups by
    year), or ``tag`` (grouped under each tag + an Untagged group, built here).
    """
    qs = _filtered_qs(tag_ids, include_untagged)
    if order == "tag":
        return {"order": order, "tag_groups": _tag_groups(qs)}
    return {"order": order, "souvenirs": qs.order_by("-found_date", "title")}


def _tag_groups(qs) -> list[dict]:
    """``[{label, souvenirs}]`` — one group per tag (a souvenir appears under
    each of its tags), plus a trailing Untagged group."""
    from geocaches.models import SouvenirTag
    groups = []
    for tag in SouvenirTag.objects.all():
        items = list(qs.filter(tags=tag).order_by("-found_date", "title"))
        if items:
            groups.append({"label": tag.name, "souvenirs": items})
    untagged = list(qs.filter(tags__isnull=True).order_by("-found_date", "title"))
    if untagged:
        from django.utils.translation import gettext as _
        groups.append({"label": _("Untagged"), "souvenirs": untagged})
    return groups


def tag_summary() -> list[dict]:
    """``[{id, name, count}]`` for every tag + an ``untagged`` pseudo-entry,
    for the filter checkboxes and the manage panel."""
    from geocaches.models import Souvenir, SouvenirTag
    tags = [
        {"id": t.id, "name": t.name, "count": t.souvenirs.count()}
        for t in SouvenirTag.objects.all()
    ]
    untagged = Souvenir.objects.filter(tags__isnull=True).count()
    return {"tags": tags, "untagged": untagged}


# ---------------------------------------------------------------------------
# Tag CRUD + assignment
# ---------------------------------------------------------------------------

def create_tag(name: str):
    from geocaches.models import SouvenirTag
    name = (name or "").strip()
    if name:
        SouvenirTag.objects.get_or_create(name=name)


def rename_tag(tag_id, name: str):
    from geocaches.models import SouvenirTag
    name = (name or "").strip()
    if not name:
        return
    SouvenirTag.objects.filter(pk=tag_id).update(name=name)


def delete_tag(tag_id):
    from geocaches.models import SouvenirTag
    SouvenirTag.objects.filter(pk=tag_id).delete()


def set_tags(souvenir_id, tag_ids, new_tag_name: str = ""):
    """Replace a souvenir's tags with *tag_ids*, optionally creating + adding a
    new tag from *new_tag_name* first."""
    from geocaches.models import Souvenir, SouvenirTag
    souvenir = Souvenir.objects.filter(pk=souvenir_id).first()
    if souvenir is None:
        return
    ids = set(int(t) for t in tag_ids)
    name = (new_tag_name or "").strip()
    if name:
        tag, _ = SouvenirTag.objects.get_or_create(name=name)
        ids.add(tag.id)
    souvenir.tags.set(SouvenirTag.objects.filter(pk__in=ids))


def dashboard_context() -> dict:
    """Context for the Souvenirs tab shell (tags, total, GC availability).

    Available whenever *either* backend can refresh: an official API token,
    or (since 2026-08-15) just a stored GC account for the website path.
    """
    from accounts.gc_client import has_api_tokens
    from geocaches.models import Souvenir
    return {
        "souvenir_tags": tag_summary(),
        "souvenir_total": Souvenir.objects.count(),
        "souvenir_gc_available": has_api_tokens() or gc_account() is not None,
    }
