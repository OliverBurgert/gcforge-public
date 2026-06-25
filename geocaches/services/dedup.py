"""Deduplication / merge services.

Houses the canonical `_merge_into` function used when two Geocache rows
represent the same physical cache (typically a GC and an OC record) and
must be consolidated into one.
"""


def _merge_into(*, source, dest, oc_code):
    """Merge a newly created OC cache record into an existing GC cache.

    Sets oc_code on dest, moves related objects, then deletes source.
    """
    from geocaches.db_lock import db_write_atomic
    from geocaches.models import OCExtension
    with db_write_atomic():
        dest.oc_code = oc_code
        update_fields = ["oc_code"]

        fill_fields = [
            "country", "iso_country_code", "state", "county", "elevation",
        ]
        for f in fill_fields:
            src_val = getattr(source, f, None)
            dst_val = getattr(dest, f, None)
            if src_val and not dst_val:
                setattr(dest, f, src_val)
                update_fields.append(f)

        dest.save(update_fields=update_fields)

        # Move logs (dedup by date/user/type)
        for log in source.logs.all():
            exists = dest.logs.filter(
                logged_date=log.logged_date, user_name=log.user_name, log_type=log.log_type,
            ).exists()
            if not exists:
                log.geocache = dest
                log.save(update_fields=["geocache"])

        # Move waypoints (dedup by lookup code)
        for wp in source.waypoints.all():
            if not dest.waypoints.filter(lookup=wp.lookup).exists():
                wp.geocache = dest
                wp.save(update_fields=["geocache"])

        # Move notes (no dedup — both sides kept)
        for note in source.notes.all():
            note.geocache = dest
            note.save(update_fields=["geocache"])

        # Move images (dedup by url)
        for img in source.images.all():
            if not dest.images.filter(url=img.url).exists():
                img.geocache = dest
                img.save(update_fields=["geocache"])

        # Merge tags
        for tag in source.tags.all():
            dest.tags.add(tag)

        # Move OC extension if the source carries one; delete dest's first if needed
        try:
            oc_ext = source.oc_extension
            try:
                dest.oc_extension.delete()
            except OCExtension.DoesNotExist:
                pass
            oc_ext.geocache = dest
            oc_ext.save(update_fields=["geocache"])
        except OCExtension.DoesNotExist:
            pass

        source.delete()
