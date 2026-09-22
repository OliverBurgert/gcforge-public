"""Authenticated web session for opencaching.* nodes.

OKAPI doesn't expose notification or profile-edit services, so we drive the
PHP pages directly via ``requests`` with stored credentials.

Multi-platform: each OC node (``oc_de``, ``oc_pl``, …) gets its own singleton
session keyed by platform.
"""
import logging
import threading

import requests
from bs4 import BeautifulSoup, Tag

from accounts import keyring_util

logger = logging.getLogger(__name__)

_BASE_URLS: dict[str, str] = {
    "oc_de": "https://www.opencaching.de",
    "oc_pl": "https://opencaching.pl",
    "oc_uk": "https://opencache.uk",
    "oc_nl": "https://www.opencaching.nl",
    "oc_us": "https://www.opencaching.us",
}

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

_sessions: dict[str, requests.Session] = {}
_lock = threading.Lock()


def base_url(platform: str) -> str:
    if platform not in _BASE_URLS:
        raise ValueError(f"Unknown OC platform: {platform!r}")
    return _BASE_URLS[platform]


def _get_credentials(platform: str) -> tuple[str, str]:
    from accounts.models import UserAccount
    acct = UserAccount.objects.filter(platform=platform).first()
    if not acct:
        raise RuntimeError(
            f"No {platform} account configured — add one in Settings > Accounts."
        )
    password = keyring_util.get_password(platform, acct.username)
    if not password:
        raise RuntimeError(
            f"No password stored for {platform} account '{acct.username}'. "
            "Open Settings > Accounts and enter the password."
        )
    return acct.username, password


def _login(platform: str) -> requests.Session:
    """Log in to one OC node and return the authenticated session.

    opencaching.de posts to ``/login.php`` with ``action=login`` and
    ``LogMeIn`` fields; the .nl / .pl / .uk / .us nodes (which share a
    different codebase) instead point ``/login.php``'s form action at
    ``/UserAuthorization/login`` with just email/password/target.  We let
    the actual form on the login page tell us which.
    """
    username, password = _get_credentials(platform)
    base = base_url(platform)
    login_url = f"{base}/login.php"

    session = requests.Session()
    session.headers["User-Agent"] = _USER_AGENT

    r = session.get(login_url, timeout=15)
    r.raise_for_status()

    soup = BeautifulSoup(r.text, "html.parser")
    form = None
    for f in soup.find_all("form"):
        if not isinstance(f, Tag):
            continue
        action = (f.get("action") or "").lower()
        if "login" not in action:
            continue
        names = {i.get("name", "") for i in f.find_all("input") if isinstance(i, Tag)}
        if "email" in names and "password" in names:
            form = f
            break
    if form is None:
        raise RuntimeError(
            f"Login page on {platform} did not contain the expected form — site may have changed."
        )

    data: dict[str, str] = {}
    for inp in form.find_all("input"):
        if not isinstance(inp, Tag):
            continue
        name = inp.get("name", "")
        if name:
            data[name] = inp.get("value", "") or ""
    data["email"] = username
    data["password"] = password
    # The .de-family form needs action=login; .us doesn't have such a field.
    if "action" in data:
        data["action"] = "login"

    # Resolve the form action (may be absolute or relative).
    from urllib.parse import urljoin
    action_url = urljoin(login_url, form.get("action") or login_url)

    r = session.post(action_url, data=data, timeout=15, allow_redirects=True)
    r.raise_for_status()

    if r.url.endswith("login.php") or "/UserAuthorization/login" in r.url:
        raise RuntimeError(
            f"{platform} login failed — check credentials in Settings > Accounts."
        )

    logger.info("Logged in to %s as %s", platform, username)
    return session


def get_session(platform: str) -> requests.Session:
    """Return the authenticated session singleton for ``platform``."""
    with _lock:
        sess = _sessions.get(platform)
        if sess is None:
            sess = _login(platform)
            _sessions[platform] = sess
        return sess


def reset_session(platform: str) -> None:
    """Force a fresh login on next ``get_session(platform)``."""
    with _lock:
        _sessions.pop(platform, None)
