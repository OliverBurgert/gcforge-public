"""
Authenticated web session for geocaching.com.

Uses the GC account credentials stored in GCForge's keyring (via keyring_util)
to create a requests.Session that can access authenticated pages like the
Pocket Query management page.

The session is a process-level singleton — login happens once per server
lifetime.  Call reset_session() to force re-login on the next use.

Relocated from ``gcprivate/gc_web_session.py`` (2026-08-13): unlike the official
partner-API client, this only ever needs the user's own GC username/password, so
it ships in the public build. See ``docs/reference/geocaching-com-web.md``.
"""

import logging
import threading

import requests
from bs4 import BeautifulSoup

from accounts import keyring_util

logger = logging.getLogger(__name__)

_GC_LOGIN_URL = "https://www.geocaching.com/account/signin"
# JSON endpoint that issues the bearer token for the /api/proxy/web/* endpoints
# (same source c:geo uses). Returns {token_type, access_token, expires_in}.
_GC_API_TOKEN_URL = "https://www.geocaching.com/account/oauth/token"

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

_session: requests.Session | None = None
_session_lock = threading.Lock()

# Bearer token for the website API proxy (/api/proxy/web/*). Scraped from a
# logged-in page's `window.accessToken` (same source the Treasures scraper uses).
_api_token: str | None = None


def _get_gc_credentials() -> tuple[str, str]:
    """Return (username, password) from GCForge's keyring."""
    from accounts.models import UserAccount

    acct = UserAccount.objects.filter(platform="gc").first()
    if not acct:
        raise RuntimeError("No GC account configured — add one in Settings > Accounts.")

    password = keyring_util.get_password("gc", acct.username)
    if not password:
        raise RuntimeError(
            f"No password stored for GC account '{acct.username}'. "
            "Go to Settings > Accounts and re-enter the password."
        )
    return acct.username, password


def _login() -> requests.Session:
    """Create a new requests.Session logged in to geocaching.com."""
    username, password = _get_gc_credentials()

    session = requests.Session()
    session.headers["User-Agent"] = _USER_AGENT

    r = session.get(_GC_LOGIN_URL, timeout=15)
    r.raise_for_status()

    soup = BeautifulSoup(r.text, "html.parser")
    csrf_input = soup.find("input", {"name": "__RequestVerificationToken"})
    if not csrf_input:
        raise RuntimeError("Login page did not contain a CSRF token — site may have changed.")

    r = session.post(
        _GC_LOGIN_URL,
        data={
            "__RequestVerificationToken": csrf_input["value"],
            "ReturnUrl": "/play",
            "UsernameOrEmail": username,
            "Password": password,
        },
        timeout=15,
        allow_redirects=True,
    )
    r.raise_for_status()

    if "account/signin" in r.url:
        raise RuntimeError(
            "Website login failed — check your GC credentials in Settings > Accounts."
        )

    logger.info("Logged in to geocaching.com as %s", username)
    return session


def get_session() -> requests.Session:
    """Return the authenticated session singleton, creating it if needed."""
    global _session
    with _session_lock:
        if _session is None:
            _session = _login()
        return _session


def get_api_token(force: bool = False) -> str:
    """Return the bearer token for the /api/proxy/web/* endpoints.

    Fetched as JSON from the authenticated ``/account/oauth/token`` endpoint
    (``{"token_type","access_token","expires_in"}``) and cached for the session.
    Pass ``force=True`` to refetch after a 401/403. Returns '' on failure.

    Note: not guarded by ``_session_lock`` (which ``get_session`` already holds);
    a concurrent double-fetch just rewrites the same value.
    """
    global _api_token
    if _api_token and not force:
        return _api_token
    session = get_session()
    try:
        resp = session.get(_GC_API_TOKEN_URL, headers={"Accept": "application/json"}, timeout=20)
        resp.raise_for_status()
        _api_token = (resp.json() or {}).get("access_token") or ""
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to fetch GC web API token from %s: %s", _GC_API_TOKEN_URL, exc)
        _api_token = ""
    if not _api_token:
        logger.warning("GC web API token endpoint returned no access_token — web search will fail.")
    return _api_token


def reset_session() -> None:
    """Force a fresh login + token refetch on the next call."""
    global _session, _api_token
    with _session_lock:
        _session = None
        _api_token = None
