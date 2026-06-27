from django.shortcuts import redirect, render
from django.utils.translation import gettext as _, ngettext

from ..services.fusion import build_manage_fused_rows


def tools_manage_fused(request):
    """View and manage all fused GC+OC caches and user decisions."""
    from django.contrib import messages
    from geocaches.models import Geocache
    from geocaches.services import set_fusion_decision

    if request.method == "POST":
        action = request.POST.get("action", "")
        gc_code = request.POST.get("gc_code", "")
        oc_code = request.POST.get("oc_code", "")
        tab = request.POST.get("tab", "fused")

        if action == "mark_dont_fuse" and gc_code and oc_code:
            set_fusion_decision(gc_code, oc_code, "dont_fuse")
            messages.success(request, _("Marked %(gc)s/%(oc)s as 'don't fuse'.") % {"gc": gc_code, "oc": oc_code})

        elif action == "remove_decision" and gc_code and oc_code:
            set_fusion_decision(gc_code, oc_code, None)
            messages.success(request, _("Cleared decision for %(gc)s/%(oc)s.") % {"gc": gc_code, "oc": oc_code})

        elif action == "refresh_all":
            rows = build_manage_fused_rows(tab)
            oc_codes = [r["oc_code"] for r in rows if r["oc_code"]]
            if oc_codes:
                # Synchronous pass: promote existing OCExtension.related_gc_code data
                # into CacheFusionRecord.auto_linked without requiring an API call.
                # This covers caches fused from GPX imports where oc:other_code was set.
                from geocaches.models import CacheFusionRecord
                promoted = 0
                for item in (
                    Geocache.objects
                    .filter(gc_code__startswith="GC", oc_code__in=oc_codes)
                    .exclude(oc_extension__related_gc_code="")
                    .values("gc_code", "oc_code", "oc_extension__related_gc_code")
                ):
                    if item["oc_extension__related_gc_code"] == item["gc_code"]:
                        __, created = CacheFusionRecord.objects.update_or_create(
                            gc_code=item["gc_code"],
                            oc_code=item["oc_code"],
                            defaults={"auto_linked": True},
                        )
                        promoted += 1

                # Background pass: re-fetch OC API data; OKAPI's gc_code field will
                # set auto_linked for any caches where the owner explicitly linked them.
                # Use oc_link_refresh (OC-only, deduplicated) to avoid sending fused
                # caches to the GC API and to prevent OKAPI duplicate-code 400 errors.
                qs = Geocache.objects.filter(oc_code__in=oc_codes)
                from geocaches.tasks.update import start_update
                started = start_update(qs, "oc_link_refresh")
                if started:
                    if promoted:
                        msg = ngettext(
                            "Refreshing OC data for %(n)d cache in background; %(promoted)d already updated from local data.",
                            "Refreshing OC data for %(n)d caches in background; %(promoted)d already updated from local data.",
                            len(oc_codes),
                        ) % {"n": len(oc_codes), "promoted": promoted}
                    else:
                        msg = ngettext(
                            "Refreshing OC data for %(n)d cache in background.",
                            "Refreshing OC data for %(n)d caches in background.",
                            len(oc_codes),
                        ) % {"n": len(oc_codes)}
                    messages.success(request, msg)
                else:
                    if promoted:
                        messages.success(request, ngettext(
                            "Updated %(n)d record from local data. An API refresh is already running.",
                            "Updated %(n)d records from local data. An API refresh is already running.",
                            promoted,
                        ) % {"n": promoted})
                    else:
                        messages.warning(request, _("An update is already running."))
            else:
                messages.info(request, _("No OC caches to refresh in this tab."))

        from django.urls import reverse
        return redirect(reverse("geocaches:tools_manage_fused") + f"?tab={tab}")

    tab = request.GET.get("tab", "fused")
    rows = build_manage_fused_rows(tab)
    return render(request, "geocaches/tools/tools_manage_fused.html", {
        "rows": rows,
        "tab": tab,
        "total": len(rows),
    })


def tools_unlinked_oc(request):
    """OC caches that reference a GC code but only have OC data (GC not yet imported)."""
    from django.contrib import messages
    from geocaches.models import Geocache

    if request.method == "POST":
        action = request.POST.get("action", "")
        gc_code = request.POST.get("gc_code", "")

        if action == "import_gc" and gc_code:
            from accounts.gc_client import has_api_tokens
            if not has_api_tokens():
                messages.error(request, _("No GC API tokens — cannot import."))
                return redirect("geocaches:tools_unlinked_oc")
            try:
                from gcprivate.gc_client import GCClient
                from geocaches.sync.base import SyncMode
                from geocaches.services import save_geocache
                client = GCClient()
                data = client.get_cache(gc_code, SyncMode.FULL, log_count=5)
                kwargs = dict(data)
                kwargs["fields"] = dict(data["fields"])
                save_geocache(**kwargs)
                messages.success(request, _("GC data imported for %(code)s.") % {"code": gc_code})
            except Exception as exc:
                messages.error(request, _("Import failed for %(code)s: %(error)s") % {"code": gc_code, "error": exc})

        return redirect("geocaches:tools_unlinked_oc")

    # Case A: fused records where primary_source is OC (GC data not yet fetched from GC API)
    case_a = list(
        Geocache.objects
        .filter(gc_code__startswith="GC", oc_code__gt="", primary_source__startswith="oc")
        .values("pk", "gc_code", "oc_code", "name", "owner", "latitude", "longitude")
    )

    # Case B: standalone OC caches (no gc_code) whose OCExtension states a related GC code
    case_b_qs = (
        Geocache.objects
        .filter(gc_code="", oc_code__gt="", oc_extension__related_gc_code__startswith="GC")
        .values("pk", "gc_code", "oc_code", "name", "owner", "latitude", "longitude",
                "oc_extension__related_gc_code")
    )
    case_a_pks = {c["pk"] for c in case_a}

    caches = list(case_a)
    for c in case_b_qs:
        if c["pk"] not in case_a_pks:
            caches.append({
                "pk": c["pk"],
                "gc_code": c["oc_extension__related_gc_code"],  # the GC to import
                "oc_code": c["oc_code"],
                "name": c["name"],
                "owner": c["owner"],
                "latitude": c["latitude"],
                "longitude": c["longitude"],
            })

    caches.sort(key=lambda c: c["gc_code"])

    return render(request, "geocaches/tools/tools_unlinked_oc.html", {
        "caches": caches,
        "total": len(caches),
    })
