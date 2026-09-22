"""Pocket Query date-range split proposal.

Computes where a group of "same search, different date range" PQs (see
``geocaches.pq.criteria``) should have their date boundaries, from
locally-known caches tagged for that PQ group.

``compute_split_proposal`` is read-only. ``apply_split`` writes a confirmed
proposal back into each PQ on geocaching.com. See ``docs/pq-date-split.md``
for the design rationale.
"""

import logging
from datetime import date

from django.utils.translation import gettext as _

from geocaches.filtering.filters import apply_area_filter
from geocaches.models import Geocache, Tag
from geocaches.pq.criteria import criteria_to_filter, diff_pq_criteria, get_pq_criteria

logger = logging.getLogger("geocaches.pq")

_DAILY_CAP = 1000


def _bucket_by_date(caches: list[tuple[str, date]], cap: int) -> list[list[tuple[str, date]]]:
    """Greedy-fill ``caches`` (already sorted by hidden_date ascending) into
    buckets no larger than ``cap``, never splitting a single calendar date
    across two buckets — GC's Placed/Between filter is day-granularity, so a
    date split there would either duplicate or drop caches placed that day."""
    buckets: list[list] = []
    current: list = []
    for item in caches:
        _, d = item
        if current and len(current) >= cap and d != current[-1][1]:
            buckets.append(current)
            current = []
        current.append(item)
    if current:
        buckets.append(current)
    return buckets


def compute_split_proposal(selected: list[dict], pq_tag_map: dict, margin: int = 10) -> dict:
    """
    selected: [{"reference_code":, "name":, "guid":}, ...] for the checked rows.
    pq_tag_map: the existing UserPreference["pq_tag_map"] dict.

    Returns a dict with warnings + a per-bucket table mapping proposed
    hidden_date boundaries onto the selected PQs (sorted by each PQ's own
    current start date, not row order — verified live to not always match
    date order).
    """
    criteria_by_ref = {pq["reference_code"]: get_pq_criteria(pq["guid"]) for pq in selected}

    first = selected[0]
    first_criteria = criteria_by_ref[first["reference_code"]]

    criteria_warnings = []
    for pq in selected[1:]:
        for d in diff_pq_criteria(first_criteria, criteria_by_ref[pq["reference_code"]]):
            criteria_warnings.append(f"{first['name']} vs {pq['name']}: {d}")

    shared_q, geo_param, warnings = criteria_to_filter(first_criteria)

    tag_names = set()
    for pq in selected:
        tag_names.update(pq_tag_map.get(pq["reference_code"], []))

    if not tag_names:
        warnings.insert(
            0,
            _(
                "No tags are configured for the selected PQs — set tags in the "
                "Tags column first so GCForge knows which local caches belong "
                "to this group."
            ),
        )

    tags = Tag.objects.filter(name__in=tag_names)
    qs = Geocache.objects.filter(tags__in=tags).filter(shared_q).distinct()
    if geo_param:
        qs = apply_area_filter(qs, {"geo": geo_param})

    no_date_count = qs.filter(hidden_date__isnull=True).count()
    dated = list(
        qs.filter(hidden_date__isnull=False)
        .order_by("hidden_date")
        .values_list("gc_code", "hidden_date")
    )

    cap = max(1, _DAILY_CAP - margin)
    buckets = _bucket_by_date(dated, cap)

    # Sort PQs by their own current start date (open/no-start sorts first) —
    # NOT by checkbox/row order, which was verified not to reliably match
    # date order on the real PQ list page.
    def _start_date(pq):
        return criteria_by_ref[pq["reference_code"]]["placed"]["date_from"] or date.min

    ordered_selected = sorted(selected, key=_start_date)

    bucket_rows = []
    unmapped_extra_buckets = []
    unmapped_extra_pqs = []
    prev_date_to = None
    for i in range(max(len(buckets), len(ordered_selected))):
        bucket = buckets[i] if i < len(buckets) else None
        pq = ordered_selected[i] if i < len(ordered_selected) else None

        date_from = date_to = count = None
        if bucket is not None:
            date_from = prev_date_to
            is_last = i == len(buckets) - 1
            date_to = None if is_last else bucket[-1][1]
            prev_date_to = date_to
            count = len(bucket)

        if bucket is not None and pq is not None:
            bucket_rows.append({
                "pq_name": pq["name"],
                "reference_code": pq["reference_code"],
                "guid": pq["guid"],
                "date_from": date_from.isoformat() if date_from else None,
                "date_to": date_to.isoformat() if date_to else None,
                "count": count,
            })
        elif bucket is not None:
            unmapped_extra_buckets.append({
                "date_from": date_from.isoformat() if date_from else None,
                "date_to": date_to.isoformat() if date_to else None,
                "count": count,
            })
        elif pq is not None:
            unmapped_extra_pqs.append(pq["name"])

    return {
        "criteria_warnings": criteria_warnings,
        "unsupported_warnings": warnings,
        "no_date_count": no_date_count,
        "total_count": len(dated),
        "buckets": bucket_rows,
        "unmapped_extra_buckets": unmapped_extra_buckets,
        "unmapped_extra_pqs": unmapped_extra_pqs,
    }


def apply_split(items: list[dict]) -> list[dict]:
    """Write a confirmed split proposal back into each PQ on geocaching.com.

    ``items`` is the ``buckets`` list ``compute_split_proposal`` returned
    (``reference_code``/``guid``/``pq_name``/``date_from``/``date_to``,
    round-tripped by the client as-is after the user reviewed it), optionally
    with a client-added ``new_name`` per item for the sequential-numbering
    rename convention ("Kusterdingen 1", "Kusterdingen 2", ...) — preferred
    over trying to regenerate a date-encoded name, which isn't robust across
    locales/date formats. This does not recompute or re-verify the
    proposal, it applies exactly what was shown.

    One PQ failing doesn't stop the rest (matches bulk_trigger/bulk_delete's
    existing per-item pattern) — returns a result dict per item.
    """
    from geocaches.pq.criteria import apply_pq_date_range

    results = []
    for item in items:
        date_from = date.fromisoformat(item["date_from"]) if item.get("date_from") else None
        date_to = date.fromisoformat(item["date_to"]) if item.get("date_to") else None
        new_name = item.get("new_name") or None
        try:
            resolved_from, resolved_to, resolved_name = apply_pq_date_range(
                item["guid"], date_from, date_to, new_name,
            )
            results.append({
                "reference_code": item["reference_code"],
                "pq_name": resolved_name,
                "status": "applied",
                "date_from": resolved_from.isoformat(),
                "date_to": resolved_to.isoformat(),
            })
        except Exception as exc:
            results.append({
                "reference_code": item["reference_code"],
                "pq_name": item["pq_name"],
                "status": "error",
                "error": str(exc),
            })
    return results
