"""Map visibility service — tri-state hide/show toggle for caches.

The tri-state is split across two stores enforced mutually-exclusive at write
time by ``set_state``/``bulk_set``:

* ``Geocache.map_hidden_always`` (DB bool) — encodes "Hide always"
* ``request.session[SESSION_KEY]`` (list) — encodes "Hide for this session"

Visible is the absence of both. Read sites that care about effective state
(map endpoint, list-row indicator, cache-detail action bar) combine the two
stores; the filter chain in ``geocaches/filters.py`` MUST NOT touch either.

Adventure Lab parents cascade downstream to all their stages (parent → stage,
never stage → parent). See ``docs/map-visibility.md`` §14.
"""

from __future__ import annotations

SESSION_KEY = "map_hidden_session"


class MapVisibility:
    VISIBLE = "visible"
    SESSION = "session"
    ALWAYS = "always"

    CHOICES = (VISIBLE, SESSION, ALWAYS)


def _session_list(session) -> list[str]:
    """Return a fresh list of session-hidden codes (never the underlying ref)."""
    return list(session.get(SESSION_KEY, []))


def hidden_codes_in_session(session) -> set[str]:
    """Set of display codes currently hidden for this session."""
    return set(session.get(SESSION_KEY, []))


def get_state(cache, session) -> str:
    """Return the effective tri-state for the given cache + session.

    "Always" wins over "Session" if both stores happen to disagree (which
    set_state's invariant prevents, but defensive).
    """
    if cache.map_hidden_always:
        return MapVisibility.ALWAYS
    if cache.display_code in hidden_codes_in_session(session):
        return MapVisibility.SESSION
    return MapVisibility.VISIBLE


def _cascade_targets(cache):
    """Return [cache] plus its AL stages if cache is an AL parent."""
    from geocaches.services.adventures import is_al_parent

    targets = [cache]
    if is_al_parent(cache) and cache.adventure_id is not None:
        from geocaches.models import Geocache

        stages = list(
            Geocache.objects.filter(
                adventure_id=cache.adventure_id, al_detail__isnull=False,
            )
        )
        targets.extend(stages)
    return targets


def set_state(cache, state: str, session) -> None:
    """Apply state to cache (and cascade to AL stages if cache is a parent).

    Maintains the mutual-exclusivity invariant: writing "Always" clears the
    session entry; writing "Session" sets the DB bool to False and adds to the
    session list; writing "Visible" clears both stores.
    """
    if state not in MapVisibility.CHOICES:
        raise ValueError(f"Unknown map-visibility state: {state!r}")

    targets = _cascade_targets(cache)
    _apply_state(targets, state, session)


def _apply_state(targets, state: str, session) -> None:
    """Apply state to every cache in ``targets`` and update the session list."""
    from geocaches.models import Geocache

    target_codes = {c.display_code for c in targets if c.display_code}
    session_list = _session_list(session)
    session_set = set(session_list)

    if state == MapVisibility.VISIBLE:
        # Clear both stores for these targets.
        Geocache.objects.filter(pk__in=[t.pk for t in targets]).update(
            map_hidden_always=False,
        )
        for c in targets:
            c.map_hidden_always = False
        if session_set & target_codes:
            new_list = [c for c in session_list if c not in target_codes]
            session[SESSION_KEY] = new_list
            session.modified = True
    elif state == MapVisibility.SESSION:
        Geocache.objects.filter(pk__in=[t.pk for t in targets]).update(
            map_hidden_always=False,
        )
        for c in targets:
            c.map_hidden_always = False
        to_add = [code for code in target_codes if code and code not in session_set]
        if to_add:
            session[SESSION_KEY] = session_list + to_add
            session.modified = True
    elif state == MapVisibility.ALWAYS:
        Geocache.objects.filter(pk__in=[t.pk for t in targets]).update(
            map_hidden_always=True,
        )
        for c in targets:
            c.map_hidden_always = True
        if session_set & target_codes:
            new_list = [c for c in session_list if c not in target_codes]
            session[SESSION_KEY] = new_list
            session.modified = True


def bulk_set(qs, state: str, session) -> dict:
    """Apply ``state`` to every cache in ``qs`` with O(1) queries (no per-row loop).

    AL parents in the queryset cascade to their stages (which may or may not
    themselves be in the queryset — duplicate writes are harmless).

    Counts are reported against the *direct* queryset only — cascaded stages
    that weren't in ``qs`` don't contribute to ``changed`` / ``unchanged``.
    Returns ``{'changed': N, 'unchanged': M}``.
    """
    from geocaches.models import Geocache

    if state not in MapVisibility.CHOICES:
        raise ValueError(f"Unknown map-visibility state: {state!r}")

    # Single query — fetch everything we need from the qs at once. ``_qsh`` is
    # short for "queryset hidden always".
    qs_rows = list(qs.values_list(
        "pk", "gc_code", "oc_code", "al_code", "map_hidden_always",
    ))
    if not qs_rows:
        return {"changed": 0, "unchanged": 0}

    qs_pks = {r[0] for r in qs_rows}
    qs_codes = {(r[1] or r[2] or r[3]) for r in qs_rows if (r[1] or r[2] or r[3])}

    # Cascade extension: find every stage of every AL parent in the qs.
    # AL parent ⇔ adventure_id set + no al_detail. Stages have al_detail.
    parent_adv_ids = list(
        qs.filter(adventure_id__isnull=False, al_detail__isnull=True)
          .values_list("adventure_id", flat=True)
    )
    extra_pks: set = set()
    extra_codes: set = set()
    if parent_adv_ids:
        stage_rows = list(
            Geocache.objects.filter(
                adventure_id__in=parent_adv_ids, al_detail__isnull=False,
            ).values_list("pk", "gc_code", "oc_code", "al_code")
        )
        extra_pks = {r[0] for r in stage_rows} - qs_pks
        for _, gc, oc, al in stage_rows:
            code = gc or oc or al
            if code:
                extra_codes.add(code)
        extra_codes -= qs_codes

    all_pks = qs_pks | extra_pks
    all_codes = qs_codes | extra_codes

    # Counts (qs only — stages cascade silently).
    session_set = set(session.get(SESSION_KEY, []))
    if state == MapVisibility.ALWAYS:
        unchanged = sum(1 for r in qs_rows if r[4])
    elif state == MapVisibility.SESSION:
        unchanged = sum(
            1 for r in qs_rows
            if not r[4] and (r[1] or r[2] or r[3]) in session_set
        )
    else:  # VISIBLE
        unchanged = sum(
            1 for r in qs_rows
            if not r[4] and (r[1] or r[2] or r[3]) not in session_set
        )
    changed = len(qs_rows) - unchanged

    # Apply DB and session changes in one shot each.
    if state == MapVisibility.VISIBLE:
        Geocache.objects.filter(pk__in=all_pks).update(map_hidden_always=False)
        if all_codes & session_set:
            session[SESSION_KEY] = [c for c in session.get(SESSION_KEY, []) if c not in all_codes]
            session.modified = True
    elif state == MapVisibility.SESSION:
        Geocache.objects.filter(pk__in=all_pks).update(map_hidden_always=False)
        to_add = all_codes - session_set
        if to_add:
            session[SESSION_KEY] = list(session.get(SESSION_KEY, [])) + list(to_add)
            session.modified = True
    else:  # ALWAYS
        Geocache.objects.filter(pk__in=all_pks).update(map_hidden_always=True)
        if all_codes & session_set:
            session[SESSION_KEY] = [c for c in session.get(SESSION_KEY, []) if c not in all_codes]
            session.modified = True

    return {"changed": changed, "unchanged": unchanged}


def reset_all_session(session) -> int:
    """Clear the entire session-hide list. Returns count of codes cleared."""
    existing = session.get(SESSION_KEY, [])
    n = len(existing)
    if n:
        session[SESSION_KEY] = []
        session.modified = True
    return n
