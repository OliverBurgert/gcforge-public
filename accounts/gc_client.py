"""Public GC account facade.

Build-agnostic surface over the geocaching.com website-login / API-token bridge.
The real implementation lives in ``gcprivate.gc_account`` and is removed from the
public/packaged build. Here every call checks
``geocaches.feature_flags.gc_api_available()`` first:

* In the full/dev build it delegates to ``gcprivate.gc_account``.
* In the public build the status queries return "not connected" defaults and the
  network calls degrade (``test_credentials`` reports failure, ``get_api_client``
  raises ``FeatureUnavailable``), so callers never hit an ``ImportError``.

Keeping this module at its historical import path (``accounts.gc_client``) means
the many read-path callers don't need to know whether GC is bundled.
"""
from geocaches.feature_flags import FeatureUnavailable, gc_api_available


def test_credentials(username: str, password: str) -> tuple[bool, str]:
    if not gc_api_available():
        return False, "GC integration is not available in this build"
    from gcprivate.gc_account import test_credentials as _impl
    return _impl(username, password)


def has_api_tokens() -> bool:
    if not gc_api_available():
        return False
    from gcprivate.gc_account import has_api_tokens as _impl
    return _impl()


def get_api_token_info() -> dict | None:
    if not gc_api_available():
        return None
    from gcprivate.gc_account import get_api_token_info as _impl
    return _impl()


def get_api_client():
    if not gc_api_available():
        raise FeatureUnavailable("GC integration is not available in this build")
    from gcprivate.gc_account import get_api_client as _impl
    return _impl()


def is_gc_api_verified() -> bool:
    if not gc_api_available():
        return False
    from gcprivate.gc_account import is_gc_api_verified as _impl
    return _impl()


def set_gc_api_verified(verified: bool = True) -> None:
    if not gc_api_available():
        return
    from gcprivate.gc_account import set_gc_api_verified as _impl
    _impl(verified)


def ensure_gc_checked() -> None:
    if not gc_api_available():
        return
    from gcprivate.gc_account import ensure_gc_checked as _impl
    _impl()
