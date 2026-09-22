"""Build-capability flags.

The public/packaged build ships without the GC + Adventure-Lab integration
(see ``docs/deployment-and-publishing.md``): ``settings.HAS_GC_API`` is False and
the ``gcprivate`` package is removed entirely. Any code path that would reach a
GC/ALC network client must check ``gc_api_available()`` first and degrade
cleanly instead of importing ``gcprivate`` unconditionally.
"""
from django.conf import settings


class FeatureUnavailable(RuntimeError):
    """Raised when a GC / Adventure-Lab feature is invoked in a build without it."""


def gc_api_available() -> bool:
    """True if this build bundles the GC / Adventure-Lab integration (gcprivate)."""
    return bool(getattr(settings, "HAS_GC_API", False))


def require_gc_api() -> None:
    """Raise ``FeatureUnavailable`` if the GC / Adventure-Lab integration is absent.

    Call this at the entry of any view/task/command that would construct a GC or
    Adventure-Lab client. The exception is turned into a clean user-facing message
    by ``geocaches.middleware.FeatureGateMiddleware`` (for requests) or reported as
    a failed task/command otherwise — never an uncaught ``ImportError``.
    """
    if not gc_api_available():
        raise FeatureUnavailable
