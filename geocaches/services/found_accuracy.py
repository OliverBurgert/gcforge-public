"""Bulk-set helper for Geocache.found_accuracy (Adventure Lab find confidence).

Single-cache sets go straight through ``cache.save(update_fields=[...])`` at
the call site (see views.cache_actions.cache_set_found_accuracy) — this module
only covers the filtered-subset case driven from the Enrich menu.
"""


def bulk_set_found_accuracy(qs, value: str) -> int:
    """Set found_accuracy to *value* on every found AL stage in *qs*.

    Restricted to AL stages (al_detail set) that are actually found — matches
    the badge the value is shown next to. Returns the number of rows changed.
    """
    from geocaches.models import Geocache

    target_pks = list(
        qs.filter(al_detail__isnull=False, found=True)
        .exclude(found_accuracy=value)
        .values_list("pk", flat=True)
    )
    if not target_pks:
        return 0
    Geocache.objects.filter(pk__in=target_pks).update(found_accuracy=value)
    return len(target_pks)
