from django.contrib import messages
from django.shortcuts import redirect, render
from django.utils.translation import gettext as _, ngettext
from django.views.decorators.http import require_POST

from geocaches.models import Geocache


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
    conflict = (
        (cache.gc_code and Geocache.objects.filter(gc_code=cache.gc_code).exists())
        or (not cache.gc_code and cache.al_code and Geocache.objects.filter(al_code=cache.al_code).exists())
        or (not cache.gc_code and not cache.al_code and cache.oc_code and Geocache.objects.filter(oc_code=cache.oc_code).exists())
    )
    if conflict:
        messages.error(
            request,
            _("%(code)s cannot be restored — a fresh record already exists (re-imported). Delete it from Trash instead.")
            % {"code": code},
        )
        return redirect("geocaches:trash")

    from geocaches.services.trash import restore_cache
    restore_cache(cache)
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
        messages.success(request, _("%(code)s permanently deleted.") % {"code": code})
    return redirect("geocaches:trash")


@require_POST
def trash_empty(request):
    count = Geocache.all_objects.filter(deleted_at__isnull=False).count()
    if count:
        Geocache.all_objects.filter(deleted_at__isnull=False).delete()
        messages.success(
            request,
            ngettext(
                "Permanently deleted %(n)d cache from Trash.",
                "Permanently deleted %(n)d caches from Trash.",
                count,
            ) % {"n": count},
        )
    return redirect("geocaches:trash")
