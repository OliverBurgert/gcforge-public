"""
Souvenir service — fetch, persist and tag geocaching.com souvenirs.

The GC API (``/users/me/souvenirs``) returns only id/title/description/image/
foundDate/url with no category metadata (see ``docs/reference/geocaching-com.md``
§4), so categorisation is done with user-managed :class:`SouvenirTag`s.  A
seeded "Countries" tag is auto-applied to country-named souvenirs on first
import.
"""

from __future__ import annotations

import functools
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# The only fields the souvenir endpoint accepts (validated server-side).
_FIELDS = "id,title,description,imagePath,thumbImagePath,foundDateUtc,url"

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


def refresh_all() -> dict:
    """Page through every souvenir and upsert.  Returns ``{added, updated, total}``."""
    from gcprivate.gc_client import GCClient
    account = gc_account()
    client = GCClient()
    added = total = 0
    skip, take = 0, 50
    while True:
        page = client._api.get("/users/me/souvenirs", fields=_FIELDS, skip=skip, take=take)
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


def refresh_latest(page_size: int = 10) -> dict:
    """Fetch newest-first in small pages, stopping once a page yields no new
    souvenirs (we've reached previously-synced territory)."""
    from gcprivate.gc_client import GCClient
    account = gc_account()
    client = GCClient()
    added = seen = 0
    skip = 0
    while True:
        page = client._api.get("/users/me/souvenirs", fields=_FIELDS, skip=skip, take=page_size)
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
    """Context for the Souvenirs tab shell (tags, total, GC availability)."""
    from accounts.gc_client import has_api_tokens
    from geocaches.models import Souvenir
    return {
        "souvenir_tags": tag_summary(),
        "souvenir_total": Souvenir.objects.count(),
        "souvenir_gc_available": has_api_tokens(),
    }
