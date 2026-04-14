"""
Minimal OKAPI (OpenCaching API) client — stdlib only, no external HTTP library.

Default consumer key/secret pairs are bundled for known OC nodes.
Users may override per platform via UserPreference:
  okapi_consumer_key_<platform>  /  okapi_consumer_secret_<platform>

OAuth 1.0a Level 3 flow (three-legged):
  1. get_request_token()   → request_token, request_token_secret
  2. redirect user to get_authorize_url()
  3. get_access_token()    → oauth_token, oauth_token_secret  (store on UserAccount)
"""

import base64
import hashlib
import hmac
import json
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

_OC_NODES: dict[str, str] = {
    "oc_de": "https://www.opencaching.de",
    "oc_pl": "https://opencaching.pl",
    "oc_uk": "https://opencaching.uk",
    "oc_nl": "https://opencaching.nl",
    "oc_us": "https://www.opencaching.us",
}

# Bundled application keys — registered for GCForge.
# Users may override these per platform in settings.
_BUNDLED_KEYS: dict[str, tuple[str, str]] = {
    #              consumer_key            consumer_secret
    "oc_de": ("99LQ2Q6pU3cVLRv8nDGH", "UUQ6N5XbLBVKNY2fZHhfkY4cATE396NkPX9j5NDY"),
    "oc_us": ("bnjQaH7ZLk3cuk2PVVwM", "FvgHrZbd3EKSJkWvmeJPLEUJHVR6n5jLdFC5texP"),
    "oc_pl": ("p5uwv3BZKsrDah2EYSqf", "YfTqdZyDSy6VCF5mJW2WsqvuVyxk5scarr4up5FY"),
    "oc_nl": ("JqCamMPFcxAwXXgVjyjL", "2RnRaWQMqFgyLTcJEkNuNdPWH9JPyfsLd856SDaK"),
    "oc_uk": ("vzfBWvdXrDZDxmdrhcQj", "hRdgEgB3cgAAntghEvkqpVgtbb8vdff3H8egewv2"),
}

PLATFORMS_WITH_DEFAULT = frozenset(_BUNDLED_KEYS)


def get_node_url(platform: str) -> str | None:
    return _OC_NODES.get(platform)


def get_consumer_credentials(platform: str, custom_key: str = "", custom_secret: str = "") -> tuple[str, str] | None:
    """
    Return (consumer_key, consumer_secret) for a platform.
    Priority: custom_key arg > bundled default.
    Returns None if no key is available.
    """
    if custom_key:
        return (custom_key, custom_secret)
    return _BUNDLED_KEYS.get(platform)


# ---------------------------------------------------------------------------
# OAuth 1.0a helpers
# ---------------------------------------------------------------------------

def _oauth_nonce() -> str:
    return uuid.uuid4().hex


def _oauth_timestamp() -> str:
    return str(int(time.time()))


def _pct_encode(s: str) -> str:
    return urllib.parse.quote(s, safe="")


def _oauth_sign(method: str, url: str, params: dict, consumer_secret: str, token_secret: str = "") -> str:
    """Return the HMAC-SHA1 signature for an OAuth 1.0a request.

    RFC 5849 §3.4.1: each param key and value is percent-encoded individually,
    then pairs are sorted and joined, then the result is percent-encoded again
    as part of the base string.  urllib.parse.urlencode uses quote_plus which
    is wrong here — we must use quote(s, safe="") throughout.
    """
    # Step 1: percent-encode each name and value individually, sort, join
    encoded_pairs = sorted(
        (_pct_encode(k), _pct_encode(v))
        for k, v in params.items()
    )
    normalized_params = "&".join(f"{k}={v}" for k, v in encoded_pairs)

    # Step 2: build base string
    base_string = "&".join([
        _pct_encode(method.upper()),
        _pct_encode(url),
        _pct_encode(normalized_params),
    ])

    # Step 3: sign
    signing_key = f"{_pct_encode(consumer_secret)}&{_pct_encode(token_secret)}"
    digest = hmac.new(signing_key.encode(), base_string.encode(), hashlib.sha1).digest()
    return base64.b64encode(digest).decode()


def _oauth_header(params: dict) -> str:
    """Build an Authorization: OAuth header from a dict of oauth_* params."""
    parts = ", ".join(
        f'{k}="{_pct_encode(v)}"'
        for k, v in sorted(params.items())
    )
    return f"OAuth {parts}"


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Abort on any redirect so OAuth signatures are never silently invalidated."""
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError(
            f"Unexpected HTTP {code} redirect to {newurl!r}. "
            f"Check the node URL in _OC_NODES for this platform."
        )


_no_redirect_opener = urllib.request.build_opener(_NoRedirect())


def _post_oauth(url: str, oauth_params: dict, consumer_secret: str, token_secret: str = "") -> str:
    """Sign, send a POST with OAuth Authorization header, return response body."""
    oauth_params["oauth_signature"] = _oauth_sign(
        "POST", url, oauth_params, consumer_secret, token_secret
    )
    req = urllib.request.Request(
        url, method="POST",
        headers={"Authorization": _oauth_header(oauth_params)},
    )
    try:
        with _no_redirect_opener.open(req, timeout=10) as resp:
            return resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise ValueError(f"HTTP {exc.code}: {body[:300]}") from exc
    except OSError as exc:
        raise ValueError(f"Network error: {exc}") from exc


# ---------------------------------------------------------------------------
# OKAPI Level 1 & Level 3 calls
# ---------------------------------------------------------------------------

def _post_level3(url: str, query_params: dict, consumer_key: str, consumer_secret: str,
                  oauth_token: str, oauth_token_secret: str) -> dict:
    """
    Make a signed Level 3 POST request to an OKAPI endpoint.
    query_params are sent as form-encoded body AND included in the signature.
    """
    oauth_params = {
        "oauth_consumer_key":     consumer_key,
        "oauth_nonce":            _oauth_nonce(),
        "oauth_signature_method": "HMAC-SHA1",
        "oauth_timestamp":        _oauth_timestamp(),
        "oauth_token":            oauth_token,
        "oauth_version":          "1.0",
    }
    all_params_for_sig = {**oauth_params, **query_params}
    oauth_params["oauth_signature"] = _oauth_sign(
        "POST", url, all_params_for_sig, consumer_secret, oauth_token_secret
    )
    body = urllib.parse.urlencode(query_params).encode("utf-8")
    req = urllib.request.Request(
        url, data=body, method="POST",
        headers={
            "Authorization": _oauth_header(oauth_params),
            "Content-Type": "application/x-www-form-urlencoded",
        },
    )
    try:
        with _no_redirect_opener.open(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body_text = exc.read().decode("utf-8", errors="replace")
        try:
            err = json.loads(body_text)
            msg = err.get("developer_message") or err.get("reason") or body_text[:200] or f"HTTP {exc.code}"
        except (ValueError, AttributeError):
            msg = body_text[:200] if body_text else f"HTTP {exc.code}"
        raise ValueError(f"HTTP {exc.code}: {msg}") from exc
    except OSError as exc:
        raise ValueError(f"Network error: {exc}") from exc


def _get_level1(url: str, query_params: dict, consumer_key: str) -> dict:
    """
    Make a Level 1 GET request to an OKAPI endpoint (consumer key only, no OAuth signing).
    """
    params = {"consumer_key": consumer_key, **query_params}
    full_url = url + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(full_url)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        try:
            err = json.loads(body)
            msg = err.get("developer_message") or err.get("reason") or body[:200] or f"HTTP {exc.code}"
        except (ValueError, AttributeError):
            msg = body[:200] if body else f"HTTP {exc.code}"
        raise ValueError(f"HTTP {exc.code}: {msg}") from exc
    except OSError as exc:
        raise ValueError(f"Network error: {exc}") from exc


def _get_level3(url: str, query_params: dict, consumer_key: str, consumer_secret: str,
                oauth_token: str, oauth_token_secret: str) -> dict:
    """
    Make a signed Level 3 GET request to an OKAPI endpoint.
    query_params are included in both the signature base string and the URL.
    """
    oauth_params = {
        "oauth_consumer_key":     consumer_key,
        "oauth_nonce":            _oauth_nonce(),
        "oauth_signature_method": "HMAC-SHA1",
        "oauth_timestamp":        _oauth_timestamp(),
        "oauth_token":            oauth_token,
        "oauth_version":          "1.0",
    }
    # Signature covers both OAuth params and query params
    all_params_for_sig = {**oauth_params, **query_params}
    oauth_params["oauth_signature"] = _oauth_sign(
        "GET", url, all_params_for_sig, consumer_secret, oauth_token_secret
    )
    full_url = url + "?" + urllib.parse.urlencode(query_params)
    req = urllib.request.Request(full_url, headers={"Authorization": _oauth_header(oauth_params)})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        try:
            err = json.loads(body)
            msg = err.get("developer_message") or err.get("reason") or body[:200] or f"HTTP {exc.code}"
        except (ValueError, AttributeError):
            msg = body[:200] if body else f"HTTP {exc.code}"
        raise ValueError(f"HTTP {exc.code}: {msg}") from exc
    except OSError as exc:
        raise ValueError(f"Network error: {exc}") from exc


def validate_oauth_token(platform: str, consumer_key: str, consumer_secret: str,
                         oauth_token: str, oauth_token_secret: str) -> dict:
    """
    Validate a stored OAuth token by calling services/users/user (Level 3).
    Returns the user dict on success; raises ValueError if the token is invalid/expired.
    """
    base = get_node_url(platform)
    if not base:
        raise ValueError(f"Unknown platform: {platform!r}")
    return _get_level3(
        f"{base}/okapi/services/users/user",
        {"fields": "uuid|username|profile_url"},
        consumer_key, consumer_secret, oauth_token, oauth_token_secret,
    )


# POST URL overrides for platforms whose login endpoint differs from /login.php
_LOGIN_POST_PATHS: dict[str, str] = {
    "oc_us": "/UserAuthorization/login",
}


def test_credentials(platform: str, username: str, password: str) -> tuple[bool, str]:
    """
    Test username/password by submitting the site's login form.

    Strategy:
    1. GET the home page '/' — it contains the login form with a sensible post-login
       redirect target (index.php / '/'), whereas /login.php targets itself, making
       success and failure indistinguishable by redirect URL alone.
    2. POST credentials to the platform-specific login endpoint, following redirects.
    3. Inspect the *final* page:
       - Still contains a password <input>  →  login form shown again  →  failure
       - Contains a logout/abmelden link   →  logged in               →  success
    """
    import http.cookiejar
    import re

    base = get_node_url(platform)
    if not base:
        return False, f"Unknown platform: {platform!r}"

    get_url  = f"{base}/"
    post_url = base + _LOGIN_POST_PATHS.get(platform, "/login.php")

    cookie_jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cookie_jar))

    # Step 1: GET home page — harvest hidden fields and establish a session cookie
    try:
        req = urllib.request.Request(get_url, headers={"User-Agent": "GCForge/1.0"})
        with opener.open(req, timeout=10) as resp:
            html = resp.read().decode("utf-8", errors="replace")
    except Exception as exc:
        return False, f"Could not load site: {exc}"

    hidden: dict[str, str] = {}
    for tag in re.findall(r'<input\b[^>]+>', html, re.IGNORECASE):
        if 'hidden' not in tag.lower():
            continue
        nm = re.search(r'\bname=["\']([^"\']+)["\']', tag, re.IGNORECASE)
        vl = re.search(r'\bvalue=["\']([^"\']*)["\']', tag, re.IGNORECASE)
        if nm:
            hidden[nm.group(1)] = vl.group(1) if vl else ""

    post_data = {**hidden, "email": username, "password": password}
    encoded = urllib.parse.urlencode(post_data).encode()

    # Step 2: POST credentials, following redirects so we land on the final page
    req2 = urllib.request.Request(
        post_url, data=encoded,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": "GCForge/1.0",
            "Referer": get_url,
        },
    )
    try:
        with opener.open(req2, timeout=15) as resp:
            body = resp.read().decode("utf-8", errors="replace")
    except Exception as exc:
        return False, f"Login request failed: {exc}"

    # Step 3: inspect the landing page
    # Login form still present → server rejected the credentials
    if re.search(r'<input\b[^>]+name=["\']password["\']', body, re.IGNORECASE):
        return False, "Invalid credentials"

    # Logout link present → we are logged in
    if re.search(r'(logout|abmelden|sign.?out)', body, re.IGNORECASE):
        return True, "Credentials valid"

    return False, "Could not verify (unexpected server response)"


def lookup_user_by_username(platform: str, consumer_key: str, username: str) -> dict:
    """
    Call OKAPI services/users/by_username (Level 1: consumer key only).
    Returns the parsed JSON dict.  Raises ValueError on error.
    """
    base = get_node_url(platform)
    if not base:
        raise ValueError(f"Platform {platform!r} does not support OKAPI")

    url = f"{base}/okapi/services/users/by_username?" + urllib.parse.urlencode({
        "consumer_key": consumer_key,
        "username": username,
        "fields": "uuid|username|profile_url",
    })
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        try:
            err = json.loads(body)
            msg = err.get("developer_message") or err.get("reason") or "API error"
        except (ValueError, AttributeError):
            msg = f"HTTP {exc.code}: {body[:200]}"
        raise ValueError(msg) from exc
    except OSError as exc:
        raise ValueError(f"Network error: {exc}") from exc
    return data


# ---------------------------------------------------------------------------
# OKAPI Level 3 — OAuth 1.0a three-legged flow
# ---------------------------------------------------------------------------

def get_request_token(platform: str, consumer_key: str, consumer_secret: str, callback_url: str) -> tuple[str, str]:
    """
    Step 1: obtain a temporary request token.
    Returns (request_token, request_token_secret).
    """
    base = get_node_url(platform)
    url = f"{base}/okapi/services/oauth/request_token"
    oauth_params = {
        "oauth_callback":         callback_url,
        "oauth_consumer_key":     consumer_key,
        "oauth_nonce":            _oauth_nonce(),
        "oauth_signature_method": "HMAC-SHA1",
        "oauth_timestamp":        _oauth_timestamp(),
        "oauth_version":          "1.0",
    }
    body = _post_oauth(url, oauth_params, consumer_secret)
    parsed = dict(urllib.parse.parse_qsl(body))
    if "oauth_token" not in parsed:
        raise ValueError(f"Unexpected response from request_token: {body[:200]}")
    return parsed["oauth_token"], parsed["oauth_token_secret"]


def get_authorize_url(platform: str, request_token: str) -> str:
    """Step 2: URL to redirect the user to for authorization."""
    base = get_node_url(platform)
    return f"{base}/okapi/services/oauth/authorize?oauth_token={_pct_encode(request_token)}"


def get_access_token(
    platform: str,
    consumer_key: str,
    consumer_secret: str,
    request_token: str,
    request_token_secret: str,
    verifier: str,
) -> tuple[str, str]:
    """
    Step 3: exchange the authorized request token for a permanent access token.
    Returns (oauth_token, oauth_token_secret).
    """
    base = get_node_url(platform)
    url = f"{base}/okapi/services/oauth/access_token"
    oauth_params = {
        "oauth_consumer_key":     consumer_key,
        "oauth_nonce":            _oauth_nonce(),
        "oauth_signature_method": "HMAC-SHA1",
        "oauth_timestamp":        _oauth_timestamp(),
        "oauth_token":            request_token,
        "oauth_verifier":         verifier,
        "oauth_version":          "1.0",
    }
    body = _post_oauth(url, oauth_params, consumer_secret, request_token_secret)
    parsed = dict(urllib.parse.parse_qsl(body))
    if "oauth_token" not in parsed:
        raise ValueError(f"Unexpected response from access_token: {body[:200]}")
    return parsed["oauth_token"], parsed["oauth_token_secret"]
