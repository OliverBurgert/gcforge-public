"""Tag management.

``manage_tags()`` is the single entry point behind the tag views: rename (with
fuse-into-existing), delete, set a tag's default reference point, and bulk
add/remove over a cache queryset.
"""


def manage_tags(action, tag_name=None, queryset=None, new_name=None, tag_id=None, rp_id=None, propagate_alc=False):
    from geocaches.models import Tag, Geocache

    if action == "rename":
        if tag_id and new_name:
            old_tag = Tag.objects.filter(id=tag_id).first()
            if old_tag:
                target = Tag.objects.filter(name=new_name).first()
                if target is None:
                    # Simple rename: keep caches and center point in place.
                    old_tag.name = new_name
                    old_tag.save(update_fields=["name"])
                elif target.id != old_tag.id:
                    # Fuse into the existing target tag: migrate caches over.
                    Through = Geocache.tags.through
                    cache_ids = list(old_tag.geocaches.values_list("id", flat=True))
                    Through.objects.bulk_create(
                        [Through(geocache_id=cid, tag_id=target.id) for cid in cache_ids],
                        ignore_conflicts=True,
                    )
                    # Keep the target's center point; only inherit the old tag's
                    # when the target has none.
                    if target.default_ref_point_id is None and old_tag.default_ref_point_id is not None:
                        target.default_ref_point_id = old_tag.default_ref_point_id
                        target.save(update_fields=["default_ref_point"])
                    old_tag.geocaches.clear()
                    old_tag.delete()
        return 0

    elif action == "delete":
        if tag_id:
            Tag.objects.filter(id=tag_id).delete()
        return 0

    elif action == "set_tag_refpoint":
        if tag_id:
            tag = Tag.objects.filter(id=tag_id).first()
            if tag:
                tag.default_ref_point_id = int(rp_id) if rp_id else None
                tag.save(update_fields=["default_ref_point"])
        return 0

    elif action == "bulk_add":
        if tag_name and queryset is not None:
            tag, _ = Tag.objects.get_or_create(name=tag_name)
            cache_ids = set(queryset.values_list("id", flat=True))
            if propagate_alc:
                adv_ids = list(
                    queryset.filter(is_al_parent=True)
                    .values_list("adventure_id", flat=True)
                )
                if adv_ids:
                    child_ids = Geocache.objects.filter(
                        adventure_id__in=adv_ids, al_detail__isnull=False
                    ).values_list("id", flat=True)
                    cache_ids |= set(child_ids)
            Through = Geocache.tags.through
            Through.objects.bulk_create(
                [Through(geocache_id=cid, tag_id=tag.id) for cid in cache_ids],
                ignore_conflicts=True,
            )
            return len(cache_ids)
        return 0

    elif action == "bulk_remove":
        if tag_id and queryset is not None:
            if propagate_alc:
                adv_ids = list(
                    queryset.filter(is_al_parent=True)
                    .values_list("adventure_id", flat=True)
                )
                if adv_ids:
                    child_ids = set(
                        Geocache.objects.filter(
                            adventure_id__in=adv_ids, al_detail__isnull=False
                        ).values_list("id", flat=True)
                    )
                    all_ids = set(queryset.values_list("id", flat=True)) | child_ids
                    count = Geocache.tags.through.objects.filter(
                        geocache_id__in=all_ids, tag_id=tag_id
                    ).delete()[0]
                    return count
            count = Geocache.tags.through.objects.filter(
                geocache__in=queryset, tag_id=tag_id
            ).delete()[0]
            return count
        return 0

    return 0
