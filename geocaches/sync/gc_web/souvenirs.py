"""
Souvenir listing + detail fetch via the website — no partner-API token needed.

**Confirmed live 2026-08-15** (Oliver's real account, 252 souvenirs, via his
own HAR capture — read-only, no writes made): the classic
``/my/souvenirs.aspx`` page server-renders the **entire** souvenir list in
one page load. No pagination, no separate XHR/API call at all — the whole
list is a plain ``<ul>`` of::

    <li id="souvenir_{id}" data-earned="MM/DD/YYYY HH:MM:SS" data-name="{title}">
      <span class="souvenir-content-container">
        <a href="/souvenir/?id={id}">
          <img src="{thumb image url}" ...>
        ...

252 ``<li id="souvenir_...">`` elements matched the page's own "Acquired
(252)" count exactly, confirming nothing is missing.

That page has no ``description`` or full-size image, though — those live on
the per-souvenir detail page, ``/souvenir/?id={id}`` (also classic
server-rendered HTML, confirmed via a second HAR capture of Oliver visiting
a souvenir's detail page)::

    <span id="...uxInformation">{description text}</span>
    <img id="...uxLargeImage" class="img-souvenir-full-size" src="{full image url}">
    <img id="...uxThumbImage" src="{thumb image url}">

Output dicts are shaped to match the official API's own
``/users/me/souvenirs`` response fields exactly (``id``/``title``/
``description``/``imagePath``/``thumbImagePath``/``foundDateUtc``/``url``)
so ``geocaches.services.souvenirs._upsert()`` needs no changes to accept
either source — see ``geocaches.services.souvenirs``'s resolver.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone

from bs4 import BeautifulSoup

from geocaches.sync.gc_web.session import get_session, reset_session
from geocaches.sync.rate_limiter import polite_delay

logger = logging.getLogger(__name__)

_LIST_URL = "https://www.geocaching.com/my/souvenirs.aspx"
_DETAIL_URL = "https://www.geocaching.com/souvenir/"

_LIST_ID_RE = re.compile(r"^souvenir_(\d+)$")
_INFO_ID_RE = re.compile(r"uxInformation$")
_LARGE_IMG_ID_RE = re.compile(r"uxLargeImage$")
_THUMB_IMG_ID_RE = re.compile(r"uxThumbImage$")

# ``data-earned`` is server-rendered via ASP.NET's thread-culture-dependent
# DateTime.ToString() with no explicit format — confirmed to vary by request
# even for the same account/souvenir: a HAR capture showed zero-padded 24h
# ("02/17/2026 07:07:35"), a live fetch (same souvenir) showed non-padded
# 12h+AM/PM ("2/17/2026 7:07:35 AM"). Try both rather than trusting either.
_EARNED_FORMATS = ("%m/%d/%Y %I:%M:%S %p", "%m/%d/%Y %H:%M:%S")


def _parse_earned(earned: str) -> datetime | None:
    for fmt in _EARNED_FORMATS:
        try:
            return datetime.strptime(earned, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _check_session(resp, what: str) -> None:
    if "account/signin" in resp.url:
        reset_session()
        raise RuntimeError(f"Web session expired while {what}")


def list_souvenirs_web(*, session=None) -> list[dict]:
    """Return every souvenir on the account, scraped from ``/my/souvenirs.aspx``
    in a single request — confirmed live: no pagination, all souvenirs come
    back in one page load regardless of how many there are.

    Each dict uses the official API's field names (``id``/``title``/
    ``imagePath``/``foundDateUtc``/``url``) so it can be handed to
    ``geocaches.services.souvenirs._upsert()`` directly. ``description`` and
    ``thumbImagePath`` are deliberately absent here — only the detail page
    (``fetch_souvenir_detail_web()``) has them; merge that in for souvenirs
    that need it (new ones — see the resolver in ``services/souvenirs.py``).
    """
    session = session or get_session()
    resp = session.get(_LIST_URL, timeout=30)
    resp.raise_for_status()
    _check_session(resp, "loading the souvenirs list")

    soup = BeautifulSoup(resp.text, "html.parser")
    results = []
    for li in soup.find_all("li", id=_LIST_ID_RE):
        m = _LIST_ID_RE.match(li.get("id", ""))
        gid = int(m.group(1))
        title = li.get("data-name", "")
        earned = li.get("data-earned", "")
        found_iso = None
        if earned:
            dt = _parse_earned(earned)
            if dt is not None:
                found_iso = dt.isoformat()
            else:
                logger.debug("Souvenir %s: unparseable data-earned %r", gid, earned)
        img = li.find("img")
        results.append({
            "id": gid,
            "title": title,
            "imagePath": img.get("src", "") if img else "",
            "foundDateUtc": found_iso,
            "url": f"{_DETAIL_URL}?id={gid}",
        })
    return results


def fetch_souvenir_detail_web(souvenir_id: int, *, session=None) -> dict:
    """Fetch one souvenir's description + full-size image from its detail page.

    Returns ``{"description", "imagePath", "thumbImagePath"}`` — merge into
    the list-page dict (which has everything else) for souvenirs that need
    it, same shape convention as the rest of this module.
    """
    session = session or get_session()
    polite_delay()
    resp = session.get(_DETAIL_URL, params={"id": souvenir_id}, timeout=30)
    resp.raise_for_status()
    _check_session(resp, f"loading souvenir {souvenir_id} detail")

    soup = BeautifulSoup(resp.text, "html.parser")
    info_el = soup.find(id=_INFO_ID_RE)
    large_img = soup.find(id=_LARGE_IMG_ID_RE)
    thumb_img = soup.find(id=_THUMB_IMG_ID_RE)
    return {
        "description": info_el.get_text("\n", strip=True) if info_el else "",
        "imagePath": large_img.get("src", "") if large_img else "",
        "thumbImagePath": thumb_img.get("src", "") if thumb_img else "",
    }
