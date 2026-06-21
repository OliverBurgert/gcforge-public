from django.contrib import messages
from django.shortcuts import redirect
from django.utils.translation import gettext as _, ngettext
from django.views.decorators.http import require_GET


@require_GET
def cached_image_proxy(request):
    from geocaches.services.image_cache import serve_proxy
    return serve_proxy(request)


def download_missing_images(request):
    from geocaches.tasks.image_cache import build_plan
    from geocaches.views.list import _filtered_qs

    qs, __ = _filtered_qs(request)
    cache_ids = list(qs.values_list("id", flat=True))
    if not cache_ids:
        messages.warning(request, _("No caches match the current filter — nothing to download."))
        return redirect(request.META.get("HTTP_REFERER") or "/")

    plan = build_plan(cache_ids=cache_ids)
    scope_label = ngettext("%(n)d cache", "%(n)d caches", len(cache_ids)) % {"n": len(cache_ids)}
    return _submit_fill(request, plan, scope_label=scope_label)


def download_missing_trackable_images(request):
    from geocaches.models import Trackable
    from geocaches.tasks.image_cache import build_plan
    from geocaches.views.trackable_list import (
        _apply_filters, _read_filters, _resolve_gc_username,
    )

    fv = _read_filters(request)
    qs = _apply_filters(Trackable.objects.all(), fv, _resolve_gc_username())
    tb_ids = list(qs.values_list("id", flat=True))
    if not tb_ids:
        messages.warning(request, _("No trackables match the current filter — nothing to download."))
        return redirect(request.META.get("HTTP_REFERER") or "/")

    plan = build_plan(trackable_ids=tb_ids)
    scope_label = ngettext("%(n)d trackable", "%(n)d trackables", len(tb_ids)) % {"n": len(tb_ids)}
    return _submit_fill(request, plan, scope_label=scope_label)


def _submit_fill(request, plan, *, scope_label):
    from geocaches.tasks import submit_task
    from geocaches.tasks.image_cache import download_missing_images as run

    if not plan:
        messages.warning(request, _(
            "%(scope)s selected, but no image URLs match the enabled categories. "
            "Visit Settings → Images to turn on the categories you want to cache."
        ) % {"scope": scope_label})
        return redirect(request.META.get("HTTP_REFERER") or "/")

    submit_task(
        f"Download missing offline images ({scope_label}, {len(plan)} URLs)",
        run, plan=plan,
    )
    messages.info(request, ngettext(
        "Queued %(n)d URL for %(scope)s — progress in the task dock below.",
        "Queued %(n)d URLs for %(scope)s — progress in the task dock below.",
        len(plan),
    ) % {"n": len(plan), "scope": scope_label})
    return redirect(request.META.get("HTTP_REFERER") or "/")
