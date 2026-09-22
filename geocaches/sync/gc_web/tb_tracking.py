"""Scrape the private tracking number from a TB's website detail page.

The GC API only returns ``trackingNumber`` to the *current holder* of a
trackable. Owners (who can also see the code on the website's track/details
page) get ``null`` from the API — so for owned-but-not-held TBs we have to
fall back to a web-session GET against the public detail page.

Used by the 'Sync missing Tracking Codes for my TBs' button on the
trackables list page.

Relocated from ``gcprivate/tb_tracking_scrape.py`` (2026-08-13) — public build,
needs only the user's own GC password. See ``docs/reference/geocaching-com-web.md``.
"""
from __future__ import annotations

import logging
import re

from bs4 import BeautifulSoup

from geocaches.sync.gc_web.session import get_session, reset_session

logger = logging.getLogger(__name__)

_DETAILS_URL = "https://www.geocaching.com/track/details.aspx?tracker="
_LABEL_RE = re.compile(r"tracking\s*number\s*:?\s*", re.IGNORECASE)


def fetch_tracking_code(ref: str) -> str | None:
    """Return the private tracking code for ``ref`` (TB#######), or None.

    Returns ``None`` when the page doesn't expose the code (e.g. the
    authenticated user isn't the TB's owner). Raises on auth/network errors
    so the caller can surface them.
    """
    ref = (ref or "").strip().upper()
    if not ref:
        return None

    session = get_session()
    r = session.get(_DETAILS_URL + ref, timeout=20)
    r.raise_for_status()
    if "account/signin" in r.url or "/login" in r.url.lower():
        reset_session()
        raise RuntimeError("Web session expired while loading TB page")

    soup = BeautifulSoup(r.text, "html.parser")

    # The page renders "Tracking Number: <strong>SBJT2R</strong>" in the
    # right-side panel for owners. Find any element whose text starts with
    # "Tracking Number", then read the next non-whitespace sibling text.
    for node in soup.find_all(string=_LABEL_RE):
        # The code is typically right after the label inside the same parent,
        # often wrapped in <strong>/<b>/<span>.
        parent = node.parent
        if parent is None:
            continue
        # Look for a sibling element containing the code
        for sib in parent.find_all(["strong", "b", "span"]):
            txt = (sib.get_text() or "").strip()
            if re.fullmatch(r"[A-Z0-9]{4,12}", txt):
                return txt
        # Otherwise scan the parent's full text for an alnum token after the label
        text = parent.get_text(" ", strip=True)
        m = re.search(r"tracking\s*number\s*:?\s*([A-Z0-9]{4,12})", text, re.IGNORECASE)
        if m:
            return m.group(1)
    return None
