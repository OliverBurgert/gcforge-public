"""Request middleware for the GC / Adventure-Lab feature gate.

In the public/packaged build the ``gcprivate`` package is absent and
``settings.HAS_GC_API`` is False. Any view that nonetheless reaches a GC/ALC
client raises ``FeatureUnavailable`` (via ``require_gc_api()``) or, as a safety
net, a ``ModuleNotFoundError`` for ``gcprivate``. This middleware converts either
into a clean, translated response instead of a 500 — so the build "ships
disabled" rather than crashing.
"""
from django.contrib import messages
from django.http import HttpResponse
from django.shortcuts import redirect
from django.utils.html import escape
from django.utils.translation import gettext as _

from geocaches.feature_flags import FeatureUnavailable


def _is_gcprivate_import_error(exc: Exception) -> bool:
    return isinstance(exc, ModuleNotFoundError) and (exc.name or "").startswith("gcprivate")


class FeatureGateMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)

    def process_exception(self, request, exc):
        if not (isinstance(exc, FeatureUnavailable) or _is_gcprivate_import_error(exc)):
            return None
        msg = _("This feature requires the geocaching.com / Adventure Lab "
                "integration, which is not included in this build.")
        if request.headers.get("HX-Request"):
            return HttpResponse(
                f'<div class="alert alert-warning mb-0">{escape(msg)}</div>',
                status=200,
            )
        messages.warning(request, msg)
        return redirect(request.META.get("HTTP_REFERER") or "/")
