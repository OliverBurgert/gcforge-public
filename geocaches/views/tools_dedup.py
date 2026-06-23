import logging

from django.contrib import messages
from django.db.models import Count, Q
from django.shortcuts import redirect, render
from django.utils.translation import gettext as _, ngettext

from ..models import Geocache, Log
from ..query import mine_q, mine_finder_q
from .list import _filtered_qs

logger = logging.getLogger(__name__)


def tools_duped_my_logs(request):
    """Find caches where the user has duplicate 'Found it' logs per source."""
    qs, __ = _filtered_qs(request)
    query_string = request.GET.urlencode()

    finder_q, has_accounts = mine_finder_q()
    if not has_accounts:
        return render(request, "geocaches/tools/tools_result.html", {
            "title": "Duplicate found logs (mine)",
            "message": "No accounts configured. Add your accounts in Settings > Accounts first.",
        })

    if not qs.exists():
        return render(request, "geocaches/tools/tools_result.html", {
            "title": "Duplicate found logs (mine)",
            "message": "No caches in the current filter.",
        })

    found_type = "Found it"
    cache_filter = Q(geocache__in=qs)

    base_q = Q(log_type=found_type) & cache_filter & finder_q
    dupes = (
        Log.objects.filter(base_q)
        .values("geocache_id", "source")
        .annotate(cnt=Count("id"))
        .filter(cnt__gt=1)
    )

    cache_ids = {row["geocache_id"] for row in dupes}
    cache_map = Geocache.objects.in_bulk(cache_ids)

    results = []
    for row in dupes:
        cache = cache_map[row["geocache_id"]]
        logs = list(
            Log.objects.filter(
                base_q, geocache_id=row["geocache_id"], source=row["source"]
            ).order_by("logged_date")
        )
        source_label = row["source"].upper().replace("_", " ")
        results.append({
            "cache": cache,
            "source": source_label,
            "count": row["cnt"],
            "logs": logs,
        })

    return render(request, "geocaches/tools/tools_duped_logs.html", {
        "title": "Duplicate found logs (mine)",
        "description": "Caches where you have more than one 'Found it' log on the same platform.",
        "results": results,
        "total": len(results),
        "query_string": query_string,
    })


def tools_duped_cache_logs(request):
    """Find owned caches where any single user has duplicate 'Found it' logs."""
    qs, __ = _filtered_qs(request)
    query_string = request.GET.urlencode()

    # Filter to owned caches only
    owned_qs = qs.filter(mine_q())
    owned_count = owned_qs.count()
    if not owned_count:
        return render(request, "geocaches/tools/tools_result.html", {
            "title": "Duplicate found logs (my caches)",
            "message": "No owned caches found in the current filter. Check that your accounts are configured in Settings > Accounts.",
        })

    found_type = "Found it"

    # Find (cache, user_name) pairs with >1 Found it log (any source)
    # Use subquery to avoid SQLite variable limit.
    # Exclude "opted-out user" — API placeholder for privacy-opted-out accounts.
    dupes = (
        Log.objects.filter(
            geocache__in=owned_qs,
            log_type=found_type,
        )
        .exclude(user_name="opted-out user")
        .values("geocache_id", "user_name")
        .annotate(cnt=Count("id"))
        .filter(cnt__gt=1)
        .order_by("geocache_id", "user_name")
    )

    cache_ids = {row["geocache_id"] for row in dupes}
    cache_map = Geocache.objects.in_bulk(cache_ids)

    results = []
    for row in dupes:
        cid = row["geocache_id"]
        logs = list(
            Log.objects.filter(
                geocache_id=cid,
                log_type=found_type,
                user_name=row["user_name"],
            ).order_by("logged_date")
        )
        results.append({
            "cache": cache_map[cid],
            "finder": row["user_name"],
            "count": row["cnt"],
            "logs": logs,
        })

    return render(request, "geocaches/tools/tools_duped_logs.html", {
        "title": "Duplicate found logs (my caches)",
        "description": f"Checked {owned_count} owned cache(s) for finders with more than one 'Found it' log.",
        "results": results,
        "total": len(results),
        "query_string": query_string,
        "show_finder": True,
    })


def tools_misplaced_codes(request):
    """Detect caches where an OC code is stored in the gc_code field."""
    from geocaches.importers.lookups import OC_PREFIXES
    from geocaches.services.dedup import _merge_into

    # Find all caches where gc_code starts with a known OC prefix
    q = Q()
    for pfx in OC_PREFIXES:
        q |= Q(gc_code__startswith=pfx)
    misplaced = list(Geocache.objects.filter(q).order_by("gc_code"))

    # For each misplaced cache, check if a correct record already exists
    items = []
    for cache in misplaced:
        oc_code = cache.gc_code
        correct = Geocache.objects.filter(oc_code=oc_code).first()
        items.append({
            "cache": cache,
            "oc_code": oc_code,
            "correct_record": correct,
            "strategy": "merge" if correct else "move",
        })

    if request.method == "POST":
        action = request.POST.get("action", "")

        if action == "fix_all":
            moved = merged = 0
            for item in items:
                c = item["cache"]
                code = c.gc_code
                if item["correct_record"]:
                    c.gc_code = ""
                    c.save(update_fields=["gc_code"])
                    _merge_into(source=c, dest=item["correct_record"], oc_code=code)
                    merged += 1
                else:
                    c.oc_code = code
                    c.gc_code = ""
                    c.save(update_fields=["gc_code", "oc_code"])
                    moved += 1
            messages.success(request, ngettext(
                "Fixed %(total)d cache: %(moved)d moved, %(merged)d merged.",
                "Fixed %(total)d caches: %(moved)d moved, %(merged)d merged.",
                moved + merged,
            ) % {"total": moved + merged, "moved": moved, "merged": merged})
            return redirect("geocaches:tools_misplaced_codes")

        pk = request.POST.get("pk", "")
        try:
            target = Geocache.objects.get(pk=pk)
        except (Geocache.DoesNotExist, ValueError):
            return redirect("geocaches:tools_misplaced_codes")

        oc_code = target.gc_code
        if action == "move":
            target.oc_code = oc_code
            target.gc_code = ""
            target.save(update_fields=["gc_code", "oc_code"])
            messages.success(request, _("Moved %(code)s from gc_code to oc_code.") % {"code": oc_code})
        elif action == "merge":
            correct = Geocache.objects.filter(oc_code=oc_code).first()
            if correct:
                target.gc_code = ""
                target.save(update_fields=["gc_code"])
                _merge_into(source=target, dest=correct, oc_code=oc_code)
                messages.success(request, _("Merged misplaced record into %(code)s and deleted duplicate.") % {"code": correct.display_code})
            else:
                target.oc_code = oc_code
                target.gc_code = ""
                target.save(update_fields=["gc_code", "oc_code"])
                messages.success(request, _("Correct record gone; moved %(code)s to oc_code instead.") % {"code": oc_code})

        return redirect("geocaches:tools_misplaced_codes")

    return render(request, "geocaches/tools/tools_misplaced_codes.html", {
        "items": items,
        "total": len(items),
    })


def tools_duplicate_caches(request):
    """Find and merge duplicate GC/OC entries for the same physical cache."""
    from geocaches.services import find_potential_duplicates, merge_duplicate, set_fusion_decision

    if request.method == "POST":
        action = request.POST.get("action", "")

        if action == "merge_all":
            dupes = find_potential_duplicates()
            merged = 0
            for d in dupes:
                try:
                    merge_duplicate(d["gc_pk"], d["oc_pk"])
                    merged += 1
                except Exception as exc:
                    logger.warning("Merge failed for gc=%s oc=%s: %s",
                                   d.get("gc_pk"), d.get("oc_pk"), exc)
            messages.success(request, ngettext(
                "Merged %(n)d duplicate. Details in the log.",
                "Merged %(n)d duplicates. Details in the log.",
                merged,
            ) % {"n": merged})
            return redirect("geocaches:tools_duplicate_caches")

        if action == "merge":
            gc_pk = request.POST.get("gc_pk", "")
            oc_pk = request.POST.get("oc_pk", "")
            try:
                desc = merge_duplicate(int(gc_pk), int(oc_pk))
                messages.success(request, desc)
            except Exception as exc:
                messages.error(request, _("Merge failed: %(error)s") % {"error": exc})
            return redirect("geocaches:tools_duplicate_caches")

        if action == "dont_fuse":
            gc_code = request.POST.get("gc_code", "")
            oc_code = request.POST.get("oc_code", "")
            if gc_code and oc_code:
                set_fusion_decision(gc_code, oc_code, "dont_fuse")
                messages.success(request, _("Marked %(gc)s/%(oc)s as 'don't fuse'. It will no longer appear here.") % {"gc": gc_code, "oc": oc_code})
            return redirect("geocaches:tools_duplicate_caches")

        if action == "postpone":
            gc_code = request.POST.get("gc_code", "")
            oc_code = request.POST.get("oc_code", "")
            if gc_code and oc_code:
                set_fusion_decision(gc_code, oc_code, "postpone")
                messages.success(request, _("Postponed %(gc)s/%(oc)s.") % {"gc": gc_code, "oc": oc_code})
            return redirect("geocaches:tools_duplicate_caches")

    duplicates = find_potential_duplicates()
    return render(request, "geocaches/tools/tools_duplicate_caches.html", {
        "duplicates": duplicates,
        "total": len(duplicates),
    })
