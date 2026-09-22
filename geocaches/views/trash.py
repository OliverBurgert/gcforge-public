from django.contrib import messages
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.html import format_html
from django.utils.translation import gettext as _, ngettext
from django.views.decorators.http import require_POST

from geocaches.models import Geocache
from geocaches.runtime_cache import invalidate_filter_dialog_options, invalidate_platform_availability


def trash_list(request):
    qs = Geocache.all_objects.filter(deleted_at__isnull=False).order_by("-deleted_at")
    return render(request, "geocaches/trash.html", {"caches": qs})


@require_POST
def trash_restore(request, pk):
    cache = Geocache.all_objects.filter(pk=pk, deleted_at__isnull=False).first()
    if cache is None:
        messages.error(request, _("Cache not found in Trash."))
        return redirect("geocaches:trash")

    code = cache.display_code
    from geocaches.services.trash import RestoreConflict, restore_cache
    try:
        restore_cache(cache)
    except RestoreConflict as conflict:
        messages.error(
            request,
            format_html(
                _('{code} cannot be restored: {codes} already belongs to a cache outside the '
                  'Trash. Merge or delete that record first — see '
                  '<a href="{url}">Tools → Find duplicates</a>.'),
                code=code,
                codes=", ".join(dup for dup, _pk in conflict.conflicts),
                url=reverse("geocaches:tools_duplicate_caches"),
            ),
        )
        return redirect("geocaches:trash")

    messages.success(request, _("%(code)s restored from Trash.") % {"code": code})
    return redirect("geocaches:trash")


@require_POST
def trash_purge(request, pk):
    cache = Geocache.all_objects.filter(pk=pk, deleted_at__isnull=False).first()
    if cache is None:
        messages.error(request, _("Cache not found in Trash."))
    else:
        code = cache.display_code
        cache.delete()
        invalidate_platform_availability()
        invalidate_filter_dialog_options()
        messages.success(request, _("%(code)s permanently deleted.") % {"code": code})
    return redirect("geocaches:trash")


@require_POST
def trash_empty(request):
    count = Geocache.all_objects.filter(deleted_at__isnull=False).count()
    if count:
        Geocache.all_objects.filter(deleted_at__isnull=False).delete()
        invalidate_platform_availability()
        invalidate_filter_dialog_options()
        messages.success(
            request,
            ngettext(
                "Permanently deleted %(n)d cache from Trash.",
                "Permanently deleted %(n)d caches from Trash.",
                count,
            ) % {"n": count},
        )
    return redirect("geocaches:trash")
