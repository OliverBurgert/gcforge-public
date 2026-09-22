"""
Single entry point for everything that reaches ``gcprivate`` — the
official-partner-API GC / Adventure-Lab clients. Outside ``gcprivate/`` only
this module and the ``accounts.gc_client`` facade may import that package;
every other caller asks for a client here.

Two layers live side by side:

* The ``get_*_client()`` getters construct one official-API client each.
  They call ``require_gc_api()`` first, so a build without ``gcprivate``
  raises ``FeatureUnavailable`` rather than ``ModuleNotFoundError`` — views
  get the banner from ``FeatureGateMiddleware``, background tasks and
  management commands get a readable message instead of a traceback.
* The mode-aware resolvers below (log submit, log fetch, trackable client)
  pick the official partner-API client (``gcprivate.gc_client.GCClient`` /
  ``geocaches.sync.log_fetch``) or the public web fallback
  (``geocaches.sync.gc_web.log_client``/``geocaches.sync.gc_web.log_fetch``)
  per the ``gc_access_mode`` preference.

``submit_gc_log()`` picks between the two via ``geocaches.sync.base.GCLogClient``,
a structural ``Protocol`` (neither backend declares inheritance from it), so
it only ever needs to hold one behind a single ``GCLogClient``-typed
variable and call ``.submit_log(...)`` without knowing which backend it got.
The four ``fetch_*``/``ensure_my_gc_logs`` functions below follow the same
``api_preferred``/``web_preferred`` shape but call plain module functions
instead (the two backends' fetch functions take a bare ``code: str`` rather
than sharing a client-object protocol — the official versions take a
``GCClient`` positionally, the web versions need no client at all, so there's
nothing to protocol-ize).

Architecture decided 2026-08-12 (submit), extended 2026-08-14 (fetch) — see
``docs/reference/geocaching-com-web.md`` §6/§10.
"""

import logging

from geocaches.sync.base import GCLogClient

logger = logging.getLogger(__name__)

_MODE_API_PREFERRED = "api_preferred"
_MODE_WEB_PREFERRED = "web_preferred"


def gc_access_mode() -> str:
    from preferences.models import UserPreference
    return UserPreference.get("gc_access_mode", _MODE_API_PREFERRED)


# ---------------------------------------------------------------------------
# Official-partner-API client getters
#
# The lazy ``gcprivate`` import lives *inside* each function on purpose: the
# package is removed from the public build, so importing it at module level
# would break startup. ``require_gc_api()`` runs first so that build raises a
# clean ``FeatureUnavailable`` instead. Constructor kwargs are passed through
# (none of the four clients takes any today) and the class is looked up at
# call time, which is what keeps ``patch("gcprivate.gc_client.GCClient")``
# and friends working in the test suite.
# ---------------------------------------------------------------------------

def get_gc_client(**kwargs):
    """Return an official partner-API ``GCClient`` (geocaching.com)."""
    from geocaches.feature_flags import require_gc_api
    require_gc_api()
    from gcprivate.gc_client import GCClient
    return GCClient(**kwargs)


def get_al_client(**kwargs):
    """Return an official ``ALClient`` (Adventure Lab adventures/stages)."""
    from geocaches.feature_flags import require_gc_api
    require_gc_api()
    from gcprivate.al_client import ALClient
    return ALClient(**kwargs)


def get_labs_client(**kwargs):
    """Return an official ``LabsClient`` (labs.geocaching.com log history)."""
    from geocaches.feature_flags import require_gc_api
    require_gc_api()
    from gcprivate.labs_client import LabsClient
    return LabsClient(**kwargs)


def get_trackable_api_client(**kwargs):
    """Return an official partner-API ``TrackableClient``.

    Always the official client — for callers that need partner-API-only
    surface (detail/log-history pages, inventory/discovered/moved ref lists).
    Use ``get_trackable_client()`` instead where a web fallback is wanted.
    """
    from geocaches.feature_flags import require_gc_api
    require_gc_api()
    from gcprivate.trackable_client import TrackableClient
    return TrackableClient(**kwargs)


def _web_log_client() -> GCLogClient:
    from geocaches.sync.gc_web.log_client import GCWebLogClient
    return GCWebLogClient()


def submit_gc_log(
    gc_code: str,
    log_type: str,
    logged_at_iso: str,
    text: str,
    *,
    use_favourite_point: bool = False,
) -> tuple[str, dict]:
    """Submit a geocache log via the active GC backend.

    Returns ``(backend, response)`` — ``backend`` is ``"gc_api"`` or
    ``"gc_web"``, ``response`` has a ``"referenceCode"`` key.

    - ``"api_preferred"`` (default): tries the official partner API first (if
      this build has it). On failure, automatically retries via the public
      web client instead of raising — a missing/broken token degrades
      gracefully rather than losing the log entirely.
    - ``"web_preferred"``: always uses the web client, even with a working
      API token present — for deliberately testing the fallback path. Does
      **not** retry via the API on failure, so testing stays honest.
    """
    if gc_access_mode() == _MODE_WEB_PREFERRED:
        client: GCLogClient = _web_log_client()
        resp = client.submit_log(
            gc_code, log_type, logged_at_iso, text,
            use_favourite_point=use_favourite_point,
        )
        return "gc_web", resp

    # api_preferred (default)
    from geocaches.feature_flags import gc_api_available
    if gc_api_available():
        try:
            client: GCLogClient = get_gc_client()
            resp = client.submit_log(
                gc_code, log_type, logged_at_iso, text,
                use_favourite_point=use_favourite_point,
            )
            return "gc_api", resp
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "GC partner-API log submission failed for %s, falling back to web: %s",
                gc_code, exc,
            )

    client = _web_log_client()
    resp = client.submit_log(
        gc_code, log_type, logged_at_iso, text,
        use_favourite_point=use_favourite_point,
    )
    return "gc_web", resp


def get_trackable_client():
    """Return the active GC trackable client (official API or web fallback).

    Same ``api_preferred``/``web_preferred`` convention as ``submit_gc_log()``,
    but with no live-failure retry: callers hold onto the returned client and
    call multiple methods on it (``verify_tracking_code()`` then
    ``submit_trackable_log()``), so this only chooses once, up front — same
    static-selection precedent as ``views/map.py``'s ``_make_preview_client()``.
    """
    if gc_access_mode() == _MODE_WEB_PREFERRED:
        from geocaches.sync.gc_web.trackables import GCWebTrackableClient
        return GCWebTrackableClient()

    from geocaches.feature_flags import gc_api_available
    if gc_api_available():
        return get_trackable_api_client()

    from geocaches.sync.gc_web.trackables import GCWebTrackableClient
    return GCWebTrackableClient()


def fetch_recent_gc_logs(gc_code: str, count: int = 50) -> tuple[int, int]:
    """Fetch the N most recent GC logs for ``gc_code`` via the active backend.

    Returns ``(new_logs_saved, count_returned)``. Same ``api_preferred``/
    ``web_preferred`` behavior as ``submit_gc_log()``.
    """
    if gc_access_mode() == _MODE_WEB_PREFERRED:
        from geocaches.sync.gc_web.log_fetch import fetch_recent_gc_logs as _web
        return _web(gc_code, count=count)

    from geocaches.feature_flags import gc_api_available
    if gc_api_available():
        try:
            from geocaches.sync.log_fetch import fetch_recent_gc_logs as _api
            return _api(get_gc_client(), gc_code, count=count)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "GC partner-API recent-log fetch failed for %s, falling back to web: %s",
                gc_code, exc,
            )

    from geocaches.sync.gc_web.log_fetch import fetch_recent_gc_logs as _web
    return _web(gc_code, count=count)


def fetch_more_gc_logs(gc_code: str, skip: int = 0, count: int = 50) -> tuple[int, int]:
    """Fetch ``count`` GC logs starting at ``skip`` via the active backend.

    Returns ``(new_logs_saved, count_returned)``. Same ``api_preferred``/
    ``web_preferred`` behavior as ``submit_gc_log()``.
    """
    if gc_access_mode() == _MODE_WEB_PREFERRED:
        from geocaches.sync.gc_web.log_fetch import fetch_more_gc_logs as _web
        return _web(gc_code, skip=skip, count=count)

    from geocaches.feature_flags import gc_api_available
    if gc_api_available():
        try:
            from geocaches.sync.log_fetch import fetch_more_gc_logs as _api
            return _api(get_gc_client(), gc_code, skip=skip, count=count)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "GC partner-API more-log fetch failed for %s, falling back to web: %s",
                gc_code, exc,
            )

    from geocaches.sync.gc_web.log_fetch import fetch_more_gc_logs as _web
    return _web(gc_code, skip=skip, count=count)


def fetch_all_gc_logs(gc_code: str) -> int:
    """Page through ALL GC logs for ``gc_code`` via the active backend.

    Returns total new logs saved. Same ``api_preferred``/``web_preferred``
    behavior as ``submit_gc_log()``.
    """
    if gc_access_mode() == _MODE_WEB_PREFERRED:
        from geocaches.sync.gc_web.log_fetch import fetch_all_gc_logs as _web
        return _web(gc_code)

    from geocaches.feature_flags import gc_api_available
    if gc_api_available():
        try:
            from geocaches.sync.log_fetch import fetch_all_gc_logs as _api
            return _api(get_gc_client(), gc_code)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "GC partner-API all-logs fetch failed for %s, falling back to web: %s",
                gc_code, exc,
            )

    from geocaches.sync.gc_web.log_fetch import fetch_all_gc_logs as _web
    return _web(gc_code)


def ensure_my_gc_logs(gc_code: str) -> int:
    """Ensure the caller's own GC logs for ``gc_code`` are saved locally, via the active backend.

    Returns total new logs saved. Same ``api_preferred``/``web_preferred``
    behavior as ``submit_gc_log()`` — note the web backend uses
    ``showOwnerOnly=true`` to ask directly for the caller's own logs rather
    than paging through the whole history looking for a match (see
    ``geocaches.sync.gc_web.log_fetch.ensure_my_gc_logs``'s docstring).
    """
    if gc_access_mode() == _MODE_WEB_PREFERRED:
        from geocaches.sync.gc_web.log_fetch import ensure_my_gc_logs as _web
        return _web(gc_code)

    from geocaches.feature_flags import gc_api_available
    if gc_api_available():
        try:
            from geocaches.sync.log_fetch import ensure_my_gc_logs as _api
            return _api(get_gc_client(), gc_code)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "GC partner-API own-log fetch failed for %s, falling back to web: %s",
                gc_code, exc,
            )

    from geocaches.sync.gc_web.log_fetch import ensure_my_gc_logs as _web
    return _web(gc_code)
