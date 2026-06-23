"""Adventure Lab completion tracking."""


def is_al_parent(geocache) -> bool:
    """True if this cache is an Adventure Lab parent (adventure set, no ALStageDetail)."""
    if geocache.adventure_id is None:
        return False
    return not hasattr(geocache, "al_detail") or geocache.al_detail is None


def ensure_not_al_parent_found(geocache) -> None:
    """Reject attempts to directly mark an Adventure Lab parent as found.

    AL parents track completion via the `completed` flag (set automatically when
    all stages are found).  `found` and `found_date` are mirrored onto the parent
    by recompute_adventure_completed() once completed=True; those are the only
    values that may produce found=True here without raising.
    """
    if geocache.found and is_al_parent(geocache) and not geocache.completed:
        raise ValueError(
            f"Cannot set found=True on Adventure Lab parent {geocache.al_code!r}. "
            "Mark individual stages as found; completed is set automatically."
        )


def recompute_adventure_completed(adventure) -> bool:
    """
    Check whether all stages of an Adventure are found and update the parent's
    `completed`, `found`, and `found_date` fields accordingly.

    When all stages are found:
      - `completed` is set to True
      - `found` is set to True (mirrors completed for list/filter/GPX compatibility)
      - `found_date` is set from `adventure.completion_date` if available

    Safe to call from importers and signals — only writes when values change.
    Returns True if the adventure is now complete.
    """
    stages = adventure.stages.filter(al_detail__isnull=False)
    if not stages.exists():
        return False
    all_found = not stages.filter(found=False).exists()
    parent = adventure.stages.filter(al_detail__isnull=True).first()
    if parent is None:
        return all_found

    update_fields = []

    if parent.completed != all_found:
        parent.completed = all_found
        update_fields.append("completed")

    if all_found:
        if not parent.found:
            parent.found = True
            update_fields.append("found")
        completion_date = getattr(adventure, "completion_date", None)
        if completion_date:
            target_date = completion_date.date()
            if parent.found_date != target_date:
                parent.found_date = target_date
                update_fields.append("found_date")

    if update_fields:
        parent.save(update_fields=update_fields)

    return all_found
