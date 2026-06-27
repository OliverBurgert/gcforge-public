from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.utils.translation import gettext as _, ngettext

from ..models import Geocache


def tools_check_ftf(request):
    """Start FTF candidate verification as a background task."""
    from django.contrib import messages
    from geocaches.tasks.update import start_update
    from geocaches.filters import EVENT_TYPES, FOUND_LOG_TYPES
    from geocaches.query import mine_q

    qs = (
        Geocache.objects.filter(found=False, completed=False, status="Active")
        .exclude(cache_type__in=EVENT_TYPES)
        .exclude(cache_type="Adventure Lab")
        .exclude(logs__log_type__in=FOUND_LOG_TYPES)
        .exclude(mine_q())
    )
    count = qs.count()

    if request.method == "POST":
        started = start_update(qs, "verify_ftf")
        if started:
            messages.success(request, ngettext(
                "FTF check started for %(n)d candidate.",
                "FTF check started for %(n)d candidates.",
                count,
            ) % {"n": count})
        else:
            messages.warning(request, _("An update task is already running."))
        return redirect("geocaches:list")

    return render(request, "geocaches/tools/tools_confirm.html", {
        "title": "Check FTF candidates",
        "description": f"This will fetch recent logs for {count} cache(s) that have no found-type logs, "
                       f"are not events, adventure labs, or owned by you. "
                       f"This runs in the background and may take a while.",
        "action_url": request.path,
        "submit_label": "Start check",
    })


def tools_ftf_markers(request):
    from django.contrib import messages
    from geocaches.services.ftf import detect_ftf_candidates, _build_finder_q

    finder_q, has_account = _build_finder_q()
    if not has_account:
        return render(request, "geocaches/tools/tools_ftf_markers.html", {
            "items": [], "total": 0,
            "no_accounts": True,
        })

    if request.method == "POST":
        action = request.POST.get("action", "")

        if action == "apply_all":
            pks_set = request.POST.get("set_pks", "")
            pks_unset = request.POST.get("unset_pks", "")
            set_count = unset_count = 0
            if pks_set:
                pk_list = [int(p) for p in pks_set.split(",") if p.strip()]
                set_count = Geocache.objects.filter(pk__in=pk_list).update(ftf=True)
            if pks_unset:
                pk_list = [int(p) for p in pks_unset.split(",") if p.strip()]
                unset_count = Geocache.objects.filter(pk__in=pk_list).update(ftf=False)
            messages.success(request, _("Applied all: %(set)d set, %(unset)d unset.") % {"set": set_count, "unset": unset_count})
            return redirect("geocaches:tools_ftf_markers")

        pk = request.POST.get("pk", "")
        try:
            cache = Geocache.objects.get(pk=pk)
        except (Geocache.DoesNotExist, ValueError):
            return redirect("geocaches:tools_ftf_markers")

        if action == "set":
            cache.ftf = True
            cache.save(update_fields=["ftf"])
            messages.success(request, _("FTF set for %(code)s.") % {"code": cache.display_code})
        elif action == "unset":
            cache.ftf = False
            cache.save(update_fields=["ftf"])
            messages.success(request, _("FTF unset for %(code)s.") % {"code": cache.display_code})

        return redirect("geocaches:tools_ftf_markers")

    found_caches = Geocache.objects.filter(found=True)
    candidates = detect_ftf_candidates(found_caches, finder_q)
    items = [c.as_dict() for c in candidates]
    verify_count = sum(1 for i in items if i.get("needs_verify"))

    return render(request, "geocaches/tools/tools_ftf_markers.html", {
        "items": items,
        "total": len(items),
        "verify_count": verify_count,
    })


def ftf_verify_row(request, pk):
    """Fetch earlier logs for a single cache, re-check FTF, return updated row partial."""
    from geocaches.services.ftf import (
        _build_finder_q,
        fetch_logs_for_verification,
        reverify_ftf_for_cache,
    )

    cache = Geocache.objects.filter(pk=pk).first()
    if not cache:
        return HttpResponse("")

    saved = fetch_logs_for_verification(cache)

    # Update local log count to reflect newly fetched logs
    actual_count = cache.logs.count()
    if actual_count != cache.platform_log_count:
        cache.platform_log_count = actual_count
        cache.save(update_fields=["platform_log_count"])

    finder_q, __ = _build_finder_q()
    candidate = reverify_ftf_for_cache(cache, finder_q, saved)

    return render(request, "geocaches/partials/_ftf_row.html", {
        "item": candidate.as_dict(),
    })
