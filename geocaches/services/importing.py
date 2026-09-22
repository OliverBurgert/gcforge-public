"""File-import orchestration and export entry points.

``import_and_enrich()`` dispatches to the parser in ``geocaches.importers`` that
matches *source_type* and then schedules the follow-up work (auto-enrichment,
distance-cache fill, cache invalidation) the import made necessary.
"""
from geocaches.runtime_cache import invalidate_filter_dialog_options, invalidate_platform_availability


def _start_auto_enrich(since):
    from preferences.models import UserPreference
    if not UserPreference.get("enrich_auto", True):
        return
    fields = set()
    if UserPreference.get("enrich_elevation", True):
        fields.add("elevation")
    if UserPreference.get("enrich_location", True):
        fields.add("location")
    if not fields:
        return

    from geocaches.models import Geocache
    ids = list(
        Geocache.objects.filter(last_gpx_date__gte=since).values_list("id", flat=True)
    )
    if not ids:
        return

    from geocaches.tasks.enrich import start_enrichment
    start_enrichment(Geocache.objects.filter(id__in=ids), fields)


def import_and_enrich(source_type, path, tag_names, auto_enrich=True, wpts_path=None):
    from datetime import datetime, timezone
    since = datetime.now(timezone.utc)

    if source_type == "unified_gpx":
        from geocaches.importers import import_gpx
        result = import_gpx(path, wpts_path=wpts_path, tag_names=tag_names)
    elif source_type == "gpx":
        from geocaches.importers import import_gc_gpx
        result = import_gc_gpx(path, wpts_path=wpts_path, tag_names=tag_names)
    elif source_type == "oc_gpx":
        from geocaches.importers import import_oc_gpx
        result = import_oc_gpx(path, tag_names=tag_names)
    elif source_type == "gsak":
        from geocaches.importers.gsak import import_gsak_db
        result = import_gsak_db(path, tag_names=tag_names)
    elif source_type == "lab2gpx":
        from geocaches.importers.lab2gpx import import_lab2gpx
        result = import_lab2gpx(path, tag_names=tag_names)
    else:
        raise ValueError(f"Unknown source_type: {source_type!r}")

    if auto_enrich and result:
        _start_auto_enrich(since)

    # Fill in the distance rows the newly imported caches don't have yet.
    # Runs in the background (existing rows stay valid, and moved caches were
    # already refreshed one by one by services.coords), so the import response
    # isn't held up by it.
    if result:
        from geocaches.geo.distance_cache import schedule_recompute
        schedule_recompute()
        invalidate_platform_availability()
        invalidate_filter_dialog_options()

    return result


def export_caches(queryset, format="gpx", username="", opts=None):
    from geocaches.exporters.gpx_gc import export_gpx
    return export_gpx(queryset, gc_username=username, opts=opts)
