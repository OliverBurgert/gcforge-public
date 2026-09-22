"""
Generic client helper for geocaching.com's internal tRPC API
(``/api/live/v1/trpc``), used by the Next.js-era ``/live/*`` pages (log
create/edit/delete, trackable actions, and more — see the procedure catalog
in ``docs/reference/geocaching-com-web.md`` §7).

Envelope shape — **live-verified 2026-08-13** via a real
``web.logs.createGeocacheLog`` call (HAR capture), correcting the earlier
guess (which wrongly assumed a superjson-style ``{"json": ...}`` wrapper):

    request:  POST {BASE_URL}/{procedure}?batch=1
              body: {"0": <procedure input>}
    response: [{"result": {"data": <procedure output>}}]

The batch link is used even for a single call — GC's client always appends
``?batch=1`` and wraps the response in a one-element array. No "json"
sub-key anywhere in the real capture. ``data`` itself is optional — a
confirmed ``deleteGeocacheLog`` response is bare ``{"result": {}}`` with no
``data`` key at all, decoded here as ``None``. The GET/query shape
(URL-encoded ``input=``) is **also live-verified 2026-08-13**, via a captured
``messages.getSummary`` call — same ``batch=1``/no-json-wrapper envelope.

CSRF — **live-verified 2026-08-13**: mutation POSTs carry the token in a
``csrf-token`` request header (confirmed via HAR captures of real, successful
``createGeocacheLog``/``updateGeocacheLog``/``deleteGeocacheLog`` calls, all
status 200). The value is a 64-char token; the captures' ``Cookie`` request
header was redacted by the browser's HAR export, so whether it must also
match a cookie (double-submit pattern) is unconfirmed but doesn't block
usage — a ``requests.Session`` already carries the auth cookies
automatically. ``fetch_csrf_token()`` below sources the value from a
``/live/*`` page's embedded ``__NEXT_DATA__`` — confirmed 2026-08-13 to match
the header value exactly, via the ``updateGeocacheLog`` capture's page data.
"""

import json
import logging
import re
from typing import Any
from urllib.parse import quote

import requests

from geocaches.sync.rate_limiter import polite_delay

logger = logging.getLogger(__name__)

BASE_URL = "https://www.geocaching.com/api/live/v1/trpc"

# Live-verified 2026-08-13 (HAR capture of a real createGeocacheLog POST).
CSRF_HEADER = "csrf-token"


class TRPCError(RuntimeError):
    """Raised when a tRPC response doesn't match the expected envelope shape."""


def _encode_input(data: dict | None) -> dict:
    """Wrap a procedure's input in tRPC's single-call batch envelope."""
    return {"0": data if data is not None else {}}


def _decode_response(payload: Any) -> Any:
    """Unwrap a tRPC batch response, returning the single call's ``data``.

    ``data`` itself is optional — a confirmed ``deleteGeocacheLog`` response
    is bare ``{"result": {}}`` with no ``data`` key at all (unlike
    create/update, which return the mutated record); this returns ``None``
    for that shape rather than raising.

    No error-envelope handling yet — no failing tRPC response has been
    captured live (real tRPC errors likely use a top-level ``error`` key
    instead of ``result``); add that once one has actually been seen.
    """
    try:
        return payload[0]["result"].get("data")
    except (KeyError, TypeError, IndexError, AttributeError) as exc:
        raise TRPCError(f"Unexpected tRPC response shape: {payload!r}") from exc


def fetch_csrf_token(session: requests.Session, page_url: str) -> str:
    """Scrape the ``csrfToken`` embedded in a ``/live/*`` page's ``__NEXT_DATA__``.

    Every ``/live/*`` Next.js SSR page embeds the token needed for
    ``CSRF_HEADER`` in ``__NEXT_DATA__.props.pageProps`` (per §2's recon —
    not independently re-verified this round; see module docstring).
    """
    resp = session.get(page_url, timeout=20)
    resp.raise_for_status()
    m = re.search(r'"csrfToken"\s*:\s*"([^"]+)"', resp.text)
    if not m:
        raise TRPCError(f"No csrfToken found embedded in page: {page_url}")
    return m.group(1)


def query(
    session: requests.Session,
    procedure: str,
    input_data: dict | None = None,
    *,
    timeout: int = 30,
) -> Any:
    """Issue a read-only tRPC ``query`` call via GET.

    ``GET {BASE_URL}/{procedure}?batch=1&input=<url-encoded envelope JSON>``
    — live-verified 2026-08-13 (see module docstring).
    """
    encoded = quote(json.dumps(_encode_input(input_data)), safe="")
    url = f"{BASE_URL}/{procedure}?batch=1&input={encoded}"
    polite_delay()
    resp = session.get(url, headers={"Accept": "application/json"}, timeout=timeout)
    resp.raise_for_status()
    return _decode_response(resp.json())


def mutate(
    session: requests.Session,
    procedure: str,
    input_data: dict | None = None,
    *,
    csrf_token: str | None = None,
    extra_headers: dict[str, str] | None = None,
    timeout: int = 30,
) -> Any:
    """Issue a state-changing tRPC ``mutation`` call via POST.

    ``POST {BASE_URL}/{procedure}?batch=1``, body = the envelope JSON.

    Pass ``csrf_token`` (see ``fetch_csrf_token()``) to set the confirmed
    ``CSRF_HEADER``; ``extra_headers`` remains available for anything else
    a specific procedure turns out to need.
    """
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if csrf_token:
        headers[CSRF_HEADER] = csrf_token
    if extra_headers:
        headers.update(extra_headers)
    polite_delay()
    resp = session.post(
        f"{BASE_URL}/{procedure}?batch=1",
        json=_encode_input(input_data),
        headers=headers,
        timeout=timeout,
    )
    resp.raise_for_status()
    return _decode_response(resp.json())
