"""
Shared ALC (Adventure Lab Cache) database helpers.

Used by:
  - geocaches/importers/gsak.py    (GSAK database import)
  - geocaches/importers/lab2gpx.py (lab2gpx GPX import)
  - geocaches/sync/al_client.py    (Adventure Lab API sync)

Public API:
    save_alc_stage(al_code, model_fields, tags, stats)
    save_adventure_from_api(data, tags=None) -> (Adventure, stats)
"""

import logging
from datetime import datetime, timezone

from geocaches.db_lock import db_write_atomic

_alc_log = logging.getLogger("geocaches.sync.alc_match")


def _fix_geocache_al_codes(adv, old_code: str, new_code: str) -> None:
    """Rename al_code on all Geocaches belonging to *adv* after a code change.

    Called inside _get_or_create_adventure when the canonical code derived from
    the GUID differs from the stored code (e.g. after a lab2gpx import used a
    non-canonical code).
    """
    from geocaches.models import Geocache

    # Parent geocache
    Geocache.objects.filter(adventure=adv, al_code=old_code).update(al_code=new_code)

    # Stage geocaches — rename LC{old}-N → LC{new}-N
    for gc in (
        Geocache.objects.filter(adventure=adv, al_detail__isnull=False)
        .select_related("al_detail")
    ):
        sn = gc.al_detail.stage_number if gc.al_detail else None
        if sn is not None and gc.al_code == f"{old_code}-{sn}":
            gc.al_code = f"{new_code}-{sn}"
            gc.save(update_fields=["al_code"])


def merge_duplicate_adventure(old_adv, canonical_adv) -> None:
    """Transfer user data from *old_adv* stages into the matching *canonical_adv*
    stages (found status, tags), then delete *old_adv* and all its Geocaches.

    Called when a no-GUID adventure is matched to an already-existing canonical
    adventure (i.e. both a GSAK import and an API import are in the DB for the
    same real adventure).  Stage matching is by stage number.
    """
    # Build canonical stage map: stage_number -> Geocache
    canonical_stages = {}
    for gc in (
        canonical_adv.stages.filter(al_detail__isnull=False)
        .select_related("al_detail")
    ):
        if gc.al_detail and gc.al_detail.stage_number is not None:
            canonical_stages[gc.al_detail.stage_number] = gc

    for old_stage in (
        old_adv.stages.filter(al_detail__isnull=False)
        .select_related("al_detail")
        .prefetch_related("tags")
    ):
        sn = old_stage.al_detail.stage_number if old_stage.al_detail else None
        canonical = canonical_stages.get(sn)
        if canonical is None:
            _alc_log.warning(
                "merge %s → %s: old stage %s (stage %s) has no canonical match — discarded",
                old_adv.code, canonical_adv.code, old_stage.al_code, sn,
            )
            continue
        changed = False
        if old_stage.found and not canonical.found:
            canonical.found = True
            canonical.found_date = old_stage.found_date
            changed = True
        if changed:
            canonical.save(update_fields=["found", "found_date"])
        tags = list(old_stage.tags.all())
        if tags:
            canonical.tags.add(*tags)
        # Transfer user answer and journal entry
        old_detail = getattr(old_stage, "al_detail", None)
        if old_detail and old_detail.user_answer:
            from geocaches.models import ALStageDetail
            can_detail, _ = ALStageDetail.objects.get_or_create(geocache=canonical)
            if not can_detail.user_answer:
                can_detail.user_answer = old_detail.user_answer
                can_detail.answer_is_correct = old_detail.answer_is_correct
                can_detail.save(update_fields=["user_answer", "answer_is_correct"])
        old_journal = getattr(old_stage, "al_journal", None)
        if old_journal and (old_journal.journal_message or old_journal.journal_image_url):
            from geocaches.models import ALJournalEntry
            ALJournalEntry.objects.update_or_create(
                geocache=canonical,
                defaults={
                    "journal_message": old_journal.journal_message,
                    "journal_image_url": old_journal.journal_image_url,
                },
            )

    # Transfer tags from old parent to canonical parent
    old_parent = old_adv.stages.filter(al_detail__isnull=True).prefetch_related("tags").first()
    canonical_parent = canonical_adv.stages.filter(al_detail__isnull=True).first()
    if old_parent and canonical_parent:
        tags = list(old_parent.tags.all())
        if tags:
            canonical_parent.tags.add(*tags)

    # Delete old adventure tree — cascade_al_parent_to_stages signal handles stages,
    # cleanup_orphan_adventure signal handles the Adventure record.
    if old_parent:
        old_parent.delete()
    else:
        old_adv.stages.filter(al_detail__isnull=False).delete()


def _get_or_create_adventure(
    code: str, title: str, owner: str, lat, lon, now,
    status: str = "", description: str = "", stage_count=None,
    adventure_guid: str = "",
):
    """Get or create an Adventure record, updating mutable fields if it exists.

    Lookup order:
      1. adventure_guid (stable UUID from lab2gpx/AL API)
      2. code (LC{base} — fallback when no GUID, e.g. GSAK imports)

    When a GUID is available the canonical LC code is derived deterministically
    via uuid_to_lc_code(), making it identical across all tools and installations.
    GSAK-imported adventures (no GUID) keep whatever code the export provided.
    """
    from geocaches.models import Adventure
    from geocaches.lc_code import uuid_to_lc_code

    canonical_code = uuid_to_lc_code(adventure_guid) if adventure_guid else code

    adv = None
    if adventure_guid:
        adv = Adventure.objects.filter(adventure_guid=adventure_guid).first()

    mutable = {
        "title": title,
        "owner": owner,
        "latitude": lat,
        "longitude": lon,
        "status": status,
        "description": description,
    }

    if adv is not None:
        changed = False
        if adv.code != canonical_code:
            old_code = adv.code
            adv.code = canonical_code
            changed = True
            _fix_geocache_al_codes(adv, old_code, canonical_code)
            _alc_log.info(
                "adventure %s: corrected code %s → %s",
                adv.adventure_guid, old_code, canonical_code,
            )
        for field_name, value in mutable.items():
            if value and getattr(adv, field_name) != value:
                setattr(adv, field_name, value)
                changed = True
        if stage_count is not None and adv.stage_count != stage_count:
            adv.stage_count = stage_count
            changed = True
        if changed:
            adv.save()
        return adv

    defaults = dict(mutable)
    if stage_count is not None:
        defaults["stage_count"] = stage_count
    if adventure_guid:
        defaults["adventure_guid"] = adventure_guid

    adv, created = Adventure.objects.get_or_create(code=canonical_code, defaults=defaults)
    if not created:
        changed = False
        for field_name, value in mutable.items():
            if value and getattr(adv, field_name) != value:
                setattr(adv, field_name, value)
                changed = True
        if stage_count is not None and adv.stage_count != stage_count:
            adv.stage_count = stage_count
            changed = True
        if adventure_guid and not adv.adventure_guid:
            adv.adventure_guid = adventure_guid
            changed = True
        if changed:
            adv.save()
    return adv


def _upsert_parent_geocache(
    adv, owner, placed_by, status, hidden_date,
    country, state, county, long_description, hint,
    now, tags,
):
    """Create or update the parent Geocache (LC{base}) representing the whole adventure."""
    from geocaches.geo.countries import name_to_iso as _country_to_iso
    from geocaches.models import CacheType, Geocache

    fields = {
        "name":             adv.title or adv.code,
        "owner":            owner,
        "placed_by":        placed_by,
        "cache_type":       CacheType.LAB,
        "size":             "Virtual",
        "status":           status,
        "latitude":         adv.latitude,
        "longitude":        adv.longitude,
        "hidden_date":      hidden_date,
        "country":          country,
        "iso_country_code": _country_to_iso(country),
        "state":            state,
        "county":           county,
        "last_gpx_date":    now,
        "long_description": long_description,
        "hint":             hint,
        "adventure":        adv,
    }
    with db_write_atomic():
        Geocache.all_objects.filter(al_code=adv.code, deleted_at__isnull=False).delete()
        gc, created = Geocache.objects.get_or_create(al_code=adv.code, defaults=fields)
        _PRESERVE_IF_EMPTY = {"country", "state", "county", "iso_country_code"}
        if not created and not gc.import_locked:
            for k, v in fields.items():
                if v is None and k == "hidden_date" and gc.hidden_date is not None:
                    continue  # preserve existing date when API provides none
                if k in _PRESERVE_IF_EMPTY and not v and getattr(gc, k):
                    continue  # preserve location fields the AL API doesn't supply
                setattr(gc, k, v)
            gc.save()
        if tags:
            gc.tags.add(*tags)
    return gc


_AL_DETAIL_KEYS = {
    "question_text",
    "al_answer_hash",
    "al_answer_choices",
    "al_answer_code_hashes",
    "al_key_image_url",
    "al_geofencing_radius",
    "al_challenge_type",
    "al_is_final",
    "al_journal_text",  # legacy GSAK field — ignored (ALJournalEntry handles journals)
    "stage_number",
    "al_stage_uuid",
}

_AL_DETAIL_FIELD_MAP = {
    "question_text":          "question_text",
    "al_answer_hash":         "answer_hash",
    "al_answer_choices":      "answer_choices",
    "al_answer_code_hashes":  "answer_code_hashes",
    "al_key_image_url":       "key_image_url",
    "al_geofencing_radius":   "geofencing_radius",
    "al_challenge_type":      "challenge_type",
    "al_is_final":            "is_final",
    "stage_number":           "stage_number",
    "al_stage_uuid":          "al_stage_uuid",
}


def save_alc_stage(al_code: str, model_fields: dict, tags: list, stats):
    """Save a single ALC stage via save_geocache(). Updates stats in place. Returns SaveResult."""
    from geocaches.models import ALStageDetail
    from geocaches.services import save_geocache as _save

    stage_uuid = model_fields.get("al_stage_uuid", "")

    # Split out AL-detail fields so save_geocache only sees Geocache fields.
    fields = {}
    detail_data = {}
    for k, v in model_fields.items():
        if k in _AL_DETAIL_KEYS:
            mapped = _AL_DETAIL_FIELD_MAP.get(k)
            if mapped and v not in (None, "", []):
                detail_data[mapped] = v
        else:
            fields[k] = v
    fields["al_code"] = al_code
    # Don't let a None hidden_date from the API clear a date that was set by an
    # earlier GSAK import — pop it so save_geocache leaves the existing value alone.
    if fields.get("hidden_date") is None:
        fields.pop("hidden_date", None)

    result = _save(
        al_code=al_code,
        al_stage_uuid=stage_uuid,
        fields=fields,
        tags=tags or None,
    )

    if result.locked:
        stats.locked += 1
    elif result.created:
        stats.created += 1
    else:
        stats.updated += 1

    # Persist stage details
    if result.geocache and detail_data:
        detail, _ = ALStageDetail.objects.get_or_create(geocache=result.geocache)
        changed = False
        new_hash = detail_data.get("answer_hash")
        if new_hash is not None and new_hash != detail.answer_hash:
            # Hash changed → stale answer_is_correct; force re-verification.
            detail.answer_is_correct = None
            changed = True
        for k, v in detail_data.items():
            if getattr(detail, k) != v:
                setattr(detail, k, v)
                changed = True
        if changed:
            detail.save()

    return result


def save_adventure_from_api(data: dict, tags=None):
    """
    Upsert an adventure and all its stages from AL API normalized data.

    Returns (Adventure, ImportStats).
    """
    from geocaches.importers.gpx_gc import ImportStats
    from geocaches.models import ALJournalEntry, CacheType
    from geocaches.services.adventures import recompute_adventure_completed

    now = datetime.now(timezone.utc)
    stats = ImportStats()
    tag_list = list(tags) if tags else []

    adv = _get_or_create_adventure(
        code="",
        title=data.get("title", ""),
        owner=data.get("owner", ""),
        lat=data.get("lat"),
        lon=data.get("lon"),
        now=now,
        status=data.get("status", "Active"),
        description=data.get("description", ""),
        stage_count=data.get("stage_count"),
        adventure_guid=data["adventure_guid"],
    )

    # Update extended adventure fields
    adv_changed = False
    if data.get("themes") and adv.themes != data["themes"]:
        adv.themes = data["themes"]
        adv_changed = True
    for field in ("key_image_url", "adventure_type", "smart_link", "owner_public_guid"):
        val = data.get(field, "")
        if val and getattr(adv, field) != val:
            setattr(adv, field, val)
            adv_changed = True
    if adv.key_image_url:
        _prefetch_alc_image(adv.key_image_url, parent_geocache_for_adventure(adv), adventure=adv)
    for field in ("ratings_average", "ratings_total_count", "is_highly_recommended"):
        val = data.get(field)
        if val is not None and getattr(adv, field) != val:
            setattr(adv, field, val)
            adv_changed = True
    if data.get("completion_date") and not adv.completion_date:
        from datetime import datetime as _dt
        try:
            adv.completion_date = _dt.fromisoformat(data["completion_date"].replace("Z", "+00:00"))
            adv_changed = True
        except (ValueError, AttributeError):
            pass
    if data.get("median_time_to_complete") and adv.median_time_to_complete != data["median_time_to_complete"]:
        adv.median_time_to_complete = data["median_time_to_complete"]
        adv_changed = True
    if data.get("published_utc") and not adv.published_utc:
        from datetime import datetime as _dt
        try:
            adv.published_utc = _dt.fromisoformat(data["published_utc"].replace("Z", "+00:00"))
            adv_changed = True
        except (ValueError, AttributeError):
            pass
    if adv_changed:
        adv.save()

    hidden_date = adv.published_utc.date() if adv.published_utc else None

    parent_gc = _upsert_parent_geocache(
        adv=adv,
        owner=data.get("owner", ""),
        placed_by=data.get("owner", ""),
        status=data.get("status", "Active"),
        hidden_date=hidden_date,
        country="", state="", county="",
        long_description=data.get("description", ""),
        hint="",
        now=now,
        tags=tag_list,
    )

    # Stages inherit all tags the parent already has (union with any new tags)
    seen_pks = {t.pk for t in tag_list}
    stage_tags = list(tag_list)
    for t in parent_gc.tags.all():
        if t.pk not in seen_pks:
            stage_tags.append(t)
            seen_pks.add(t.pk)

    for stage in data.get("stages", []):
        n = stage["stage_number"]
        canonical_code = f"{adv.code}-{n}"

        model_fields = {
            "name":                 stage.get("name") or canonical_code,
            "owner":                data.get("owner", ""),
            "placed_by":            data.get("owner", ""),
            "cache_type":           CacheType.LAB,
            "size":                 "Virtual",
            "status":               data.get("status", "Active"),
            "latitude":             stage["lat"],
            "longitude":            stage["lon"],
            "hidden_date":          hidden_date,
            "last_gpx_date":        now,
            "long_description":     stage.get("description", ""),
            "adventure":            adv,
            "stage_number":         n,
            # AL detail fields — extracted by save_alc_stage into ALStageDetail
            "question_text":        stage.get("question", ""),
            "al_stage_uuid":        stage.get("stage_uuid", ""),
            "al_answer_hash":         stage.get("answer_hash", ""),
            "al_answer_choices":      stage.get("choices", []),
            "al_answer_code_hashes":  stage.get("answer_code_hashes", []),
            "al_key_image_url":     stage.get("key_image_url", ""),
            "al_geofencing_radius": stage.get("geofencing_radius"),
            "al_challenge_type":    stage.get("challenge_type", ""),
            "al_is_final":          stage.get("is_final"),
        }

        try:
            result = save_alc_stage(canonical_code, model_fields, stage_tags, stats)
            # Update found status from AL API completion flag
            gc = result.geocache if result else None
            if gc and stage.get("is_complete") and not gc.found:
                gc.found = True
                gc.save(update_fields=["found"])
            # Save journal entry if present
            if gc:
                msg = stage.get("journal_message", "")
                img = stage.get("journal_image_url", "")
                if msg or img:
                    ALJournalEntry.objects.update_or_create(
                        geocache=gc,
                        defaults={"journal_message": msg, "journal_image_url": img},
                    )
                    if img:
                        _prefetch_alc_image(img, gc)
            stage_img = stage.get("key_image_url", "")
            if stage_img and gc:
                _prefetch_alc_image(stage_img, gc)
        except Exception as exc:
            stats.errors.append(f"{canonical_code}: {exc}")

    recompute_adventure_completed(adv)
    return adv, stats


def parent_geocache_for_adventure(adv):
    """Return the parent Geocache row for an Adventure (used for state lookup)."""
    if adv is None:
        return None
    return adv.stages.filter(al_detail__isnull=True).first()


def _prefetch_alc_image(url: str, cache, *, adventure=None) -> None:
    if not url:
        return
    from geocaches.services.image_cache import prefetch, state_for_cache
    linked = {"geocache": cache}
    if adventure is not None:
        linked["adventure"] = adventure
    prefetch(url, category="alc", state=state_for_cache(cache), linked=linked)
