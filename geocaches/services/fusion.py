"""Fusion / dual-listing services.

Functions for building the Manage Fused view's row list and for recording
fusion decisions (auto-linked, user "dont fuse" / "postpone", cleared).
"""


def _record_auto_link(gc_code: str, oc_code: str) -> None:
    """Ensure a CacheFusionRecord exists marking this pair as owner-confirmed."""
    from geocaches.models import CacheFusionRecord
    CacheFusionRecord.objects.update_or_create(
        gc_code=gc_code,
        oc_code=oc_code,
        defaults={"auto_linked": True},
    )


def set_fusion_decision(gc_code: str, oc_code: str, decision) -> None:
    """Create or update the user decision for a GC/OC pair.

    decision: one of "fuse", "dont_fuse", "postpone", or None to clear.
    """
    from geocaches.models import CacheFusionRecord
    CacheFusionRecord.objects.update_or_create(
        gc_code=gc_code,
        oc_code=oc_code,
        defaults={"user_decision": decision},
    )


def build_manage_fused_rows(tab):
    """Build the row list for tools_manage_fused for the given tab.

    tab is one of "all", "auto", "dont_fuse", "fused".
    """
    from geocaches.models import Geocache, CacheFusionRecord

    fused_qs = list(
        Geocache.objects
        .filter(gc_code__startswith="GC", oc_code__gt="")
        .values("gc_code", "oc_code", "name", "owner", "primary_source")
        .order_by("gc_code")
    )
    fused_gc_codes = [c["gc_code"] for c in fused_qs]
    fusion_map = {
        (r.gc_code, r.oc_code): r
        for r in CacheFusionRecord.objects.filter(gc_code__in=fused_gc_codes)
    } if fused_qs else {}

    rows = []
    for c in fused_qs:
        rec = fusion_map.get((c["gc_code"], c["oc_code"]))
        rows.append({
            "gc_code": c["gc_code"],
            "oc_code": c["oc_code"],
            "name": c["name"],
            "owner": c["owner"],
            "auto_linked": rec.auto_linked if rec else False,
            "user_decision": rec.user_decision if rec else None,
            "is_fused": True,
        })

    # dont_fuse decisions for pairs no longer fused in DB
    for rec in CacheFusionRecord.objects.filter(user_decision="dont_fuse").exclude(
        gc_code__in=fused_gc_codes
    ):
        rows.append({
            "gc_code": rec.gc_code,
            "oc_code": rec.oc_code,
            "name": "",
            "owner": "",
            "auto_linked": rec.auto_linked,
            "user_decision": rec.user_decision,
            "is_fused": False,
        })

    if tab == "auto":
        rows = [r for r in rows if r["auto_linked"] and r["is_fused"]]
    elif tab == "dont_fuse":
        rows = [r for r in rows if r["user_decision"] == "dont_fuse"]
    elif tab == "fused":
        rows = [r for r in rows if r["is_fused"] and not r["auto_linked"]]
    # "all" → everything

    return rows
