import logging

from django.http import HttpResponse, HttpResponseBadRequest, HttpResponseNotAllowed, JsonResponse
from django.shortcuts import redirect, render
from django.utils.translation import gettext as _, ngettext

from ..models import CacheMapState, Note
from .detail import _get_cache
from .list import _filtered_qs

logger = logging.getLogger(__name__)


def cache_toggle_lock(request, gc_code):
    """Toggle import_locked on a single cache. POST only."""
    from django.contrib import messages

    if request.method != "POST":
        return redirect("geocaches:detail", gc_code=gc_code)

    cache = _get_cache(gc_code)
    cache.import_locked = not cache.import_locked
    cache.save(update_fields=["import_locked"])
    if cache.import_locked:
        messages.success(request, _("%(code)s is now import-locked.") % {"code": cache.display_code})
    else:
        messages.success(request, _("%(code)s is now unlocked.") % {"code": cache.display_code})
    return redirect("geocaches:detail", gc_code=cache.display_code)


def cache_fetch_logs(request, gc_code):
    """Fetch logs for a single cache from GC or OC. POST only."""
    from django.contrib import messages

    if request.method != "POST":
        return redirect("geocaches:detail", gc_code=gc_code)

    cache = _get_cache(gc_code)

    if cache.import_locked:
        messages.warning(request, _("%(code)s is import-locked. Unlock it first before fetching logs.") % {"code": cache.display_code})
        return redirect("geocaches:detail", gc_code=cache.display_code)

    source = request.POST.get("source", "")
    action = request.POST.get("action", "fetch_recent")
    saved = 0
    api_count = 0
    skip_used = 0
    batch_size = 50

    try:
        if source == "gc" and cache.gc_code:
            from gcprivate.gc_client import GCClient
            from geocaches.sync.log_fetch import (
                fetch_recent_gc_logs, fetch_more_gc_logs, fetch_all_gc_logs,
            )
            client = GCClient()
            if action == "fetch_recent":
                saved, api_count = fetch_recent_gc_logs(client, cache.gc_code, count=batch_size)
                skip_used = batch_size
            elif action == "fetch_more":
                skip_used = int(request.POST.get("skip", 0))
                saved, api_count = fetch_more_gc_logs(client, cache.gc_code, skip=skip_used, count=batch_size)
                skip_used += batch_size
            elif action == "fetch_all":
                saved = fetch_all_gc_logs(client, cache.gc_code)
                api_count = 0  # exhausted
        elif source.startswith("oc") and cache.oc_code:
            from geocaches.sync.oc_client import OCClient
            from geocaches.sync.log_fetch import fetch_oc_logs
            from accounts.models import UserAccount
            count = int(request.POST.get("count", 50))
            acct = UserAccount.objects.filter(platform=source).first()
            user_id = acct.user_id if acct else ""
            client = OCClient(platform=source, user_id=user_id)
            saved = fetch_oc_logs(client, cache.oc_code, count=count)
        else:
            messages.error(request, _("Unknown source: %(source)s") % {"source": source})
            return redirect("geocaches:detail", gc_code=cache.display_code)
    except Exception as exc:
        messages.error(request, _("Log fetch failed: %(error)s") % {"error": exc})
        return redirect("geocaches:detail", gc_code=cache.display_code)

    # Update local log count after fetching
    if saved:
        actual_count = cache.logs.count()
        if actual_count != cache.platform_log_count:
            cache.platform_log_count = actual_count
            cache.save(update_fields=["platform_log_count"])
        messages.success(request, ngettext(
            "Fetched %(n)d new log from %(source)s.",
            "Fetched %(n)d new logs from %(source)s.",
            saved,
        ) % {"n": saved, "source": source.upper()})
    else:
        messages.info(request, _("No new logs found."))

    if source == "gc" and action in ("fetch_recent", "fetch_more") and api_count >= batch_size:
        request.session[f"log_skip_{cache.pk}"] = skip_used
        request.session[f"log_has_more_{cache.pk}"] = True
    elif source == "gc" and action in ("fetch_recent", "fetch_more", "fetch_all"):
        request.session.pop(f"log_skip_{cache.pk}", None)
        request.session[f"log_has_more_{cache.pk}"] = False

    return redirect("geocaches:detail", gc_code=cache.display_code)


def al_fetch_logs(request, gc_code):
    """Fetch reviews for an ALC parent from the Adventure Lab API. POST only."""
    from django.contrib import messages

    if request.method != "POST":
        return redirect("geocaches:detail", gc_code=gc_code)

    cache = _get_cache(gc_code)

    if not getattr(cache, "adventure", None):
        messages.error(request, _("This cache has no Adventure Lab data."))
        return redirect("geocaches:detail", gc_code=cache.display_code)

    adventure_guid = cache.adventure.adventure_guid
    if not adventure_guid:
        messages.error(request, _("No adventure GUID found for this cache."))
        return redirect("geocaches:detail", gc_code=cache.display_code)

    batch_size = 20
    action = request.POST.get("action", "fetch_recent")
    skip = int(request.POST.get("skip", 0)) if action == "fetch_more" else 0

    # Clean up any stale Log objects saved by the previous implementation
    from geocaches.models import Log as _Log
    _Log.objects.filter(geocache=cache, source="al").delete()

    from gcprivate.al_client import ALClient

    try:
        client = ALClient()
        reviews_data = client.fetch_reviews(adventure_guid, skip=skip, take=batch_size)
    except Exception as exc:
        messages.error(request, _("AL review fetch failed: %(error)s") % {"error": exc})
        return redirect("geocaches:detail", gc_code=cache.display_code)

    items = reviews_data.get("items") or [] if isinstance(reviews_data, dict) else reviews_data or []
    total_count = reviews_data.get("totalCount", 0) if isinstance(reviews_data, dict) else 0

    saved = _save_al_reviews_to_model(cache.adventure, items)
    next_skip = skip + len(items)
    stored_count = cache.adventure.al_reviews.count()
    has_more = stored_count < total_count

    request.session[f"al_review_total_{cache.pk}"] = total_count
    if has_more:
        request.session[f"al_review_skip_{cache.pk}"] = next_skip
        request.session[f"al_review_has_more_{cache.pk}"] = True
    else:
        request.session.pop(f"al_review_skip_{cache.pk}", None)
        request.session[f"al_review_has_more_{cache.pk}"] = False

    if saved:
        messages.success(request, ngettext(
            "Fetched %(n)d new review. %(stored)d of %(total)d stored.",
            "Fetched %(n)d new reviews. %(stored)d of %(total)d stored.",
            saved,
        ) % {"n": saved, "stored": stored_count, "total": total_count})
    else:
        messages.info(request, _("No new reviews. %(stored)d of %(total)d stored.") % {
            "stored": stored_count, "total": total_count,
        })

    return redirect("geocaches:detail", gc_code=cache.display_code)


def _save_al_reviews_to_model(adventure, items: list[dict]) -> int:
    from datetime import datetime, timezone
    from geocaches.models import ALReview

    existing_ids = set(
        ALReview.objects.filter(adventure=adventure).values_list("review_id", flat=True)
    )

    def _parse_dt(s):
        if not s:
            return None
        try:
            s = s.rstrip("Z")
            if "+" in s:
                s = s[:s.index("+")]
            return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)
        except (ValueError, AttributeError):
            return None

    new_reviews = []
    for item in items:
        review_id = item.get("id")
        if review_id is None or review_id in existing_ids:
            continue
        new_reviews.append(ALReview(
            adventure=adventure,
            review_id=review_id,
            player_username=item.get("playerUsername", ""),
            player_public_guid=item.get("playerPublicGuid", ""),
            player_avatar_url=item.get("playerAvatarUrl", ""),
            player_geocache_find_count=item.get("playerGeocacheFindCount"),
            player_completed_adventure_count=item.get("playerCompletedAdventureCount"),
            rating=item.get("rating"),
            review_text=item.get("reviewText", ""),
            recommended=bool(item.get("recommended", False)),
            is_creator=bool(item.get("isCreator", False)),
            adventure_completed_utc=_parse_dt(item.get("adventureCompletedDateUtc")),
            created_utc=_parse_dt(item.get("createdUtc")) or datetime.now(timezone.utc),
            modified_utc=_parse_dt(item.get("modifiedUtc")),
            images=item.get("images") or [],
        ))

    if not new_reviews:
        return 0
    ALReview.objects.bulk_create(new_reviews)
    return len(new_reviews)


def cache_refresh(request, gc_code):
    """Re-fetch a single cache from its API source (GC or OC). POST only."""
    from django.contrib import messages
    from geocaches.services import save_geocache

    if request.method != "POST":
        return redirect("geocaches:detail", gc_code=gc_code)

    cache = _get_cache(gc_code)

    if cache.import_locked:
        messages.warning(request, _("%(code)s is import-locked. Unlock it first before refreshing.") % {"code": cache.display_code})
        return redirect("geocaches:detail", gc_code=cache.display_code)

    source = request.POST.get("source", "")  # "gc" or "oc_de", etc.
    errors = []
    redirect_code = cache.display_code  # updated below if ALC code changes

    if source == "al" and cache.al_code and cache.adventure_id:
        adv = cache.adventure
        if not adv:
            errors.append(_("No adventure linked to this ALC."))
        elif adv.adventure_guid:
            # Run in the background so the task dock can show progress: the
            # refresh fetches the adventure, then backfills per-stage found
            # dates from the labs log history (a GC website login + log fetch
            # that takes a few seconds).
            from geocaches.tasks import submit_task
            from geocaches.tasks.update import refresh_single_alc
            submit_task(
                _("Refresh %(code)s") % {"code": cache.display_code},
                refresh_single_alc,
                adv.id,
            )
            messages.info(
                request,
                _("Refreshing %(code)s — stage found-dates will appear when it finishes.")
                % {"code": cache.display_code},
            )
            return redirect("geocaches:detail", gc_code=cache.display_code)
        else:
            # No GUID: a 10 m location search to find and attach the GUID may
            # change the canonical code, so this path stays synchronous and
            # redirects to the new code.
            try:
                from gcprivate.al_client import ALClient
                from geocaches.sync.service import (
                    sync_al_no_guid_adventure,
                    _backfill_al_stage_dates,
                )
                client = ALClient()
                result = sync_al_no_guid_adventure(adv, client)
                if result is None:
                    errors.append(_(
                        "No Adventure Lab match found near this location. "
                        "Check the sync log (geocaches.sync.alc_match) for details."
                    ))
                else:
                    match_stats, canonical_code = result
                    if match_stats.errors:
                        errors.extend(match_stats.errors[:3])
                    adv.refresh_from_db()
                    if adv.adventure_guid:
                        _backfill_al_stage_dates([adv.adventure_guid])
                    # Preserve stage suffix so stage viewers don't land on the parent.
                    parts = (cache.al_code or "").rsplit("-", 1)
                    if len(parts) == 2 and parts[1].isdigit():
                        redirect_code = f"{canonical_code}-{parts[1]}"
                    else:
                        redirect_code = canonical_code
            except Exception as exc:
                errors.append(_("Adventure Lab refresh failed: %(error)s") % {"error": exc})
    elif source == "gc" and cache.gc_code and not cache.al_code:
        try:
            from gcprivate.gc_client import GCClient
            from geocaches.sync.base import SyncMode
            client = GCClient()
            data = client.get_cache(cache.gc_code, SyncMode.FULL, log_count=5)
            kwargs = dict(data)
            kwargs["fields"] = dict(data["fields"])
            save_geocache(**kwargs)
            # Ensure user's own logs are present (pages through if needed)
            from geocaches.sync.log_fetch import ensure_my_gc_logs
            ensure_my_gc_logs(client, cache.gc_code)
        except Exception as exc:
            errors.append(_("GC refresh failed: %(error)s") % {"error": exc})
    elif source.startswith("oc") and cache.oc_code:
        try:
            from geocaches.sync.oc_client import OCClient
            from geocaches.sync.base import SyncMode
            from accounts.models import UserAccount
            acct = UserAccount.objects.filter(platform=source).first()
            user_id = acct.user_id if acct else ""
            client = OCClient(platform=source, user_id=user_id)
            data = client.get_cache(cache.oc_code, SyncMode.FULL)
            kwargs = dict(data)
            kwargs["fields"] = dict(data["fields"])
            save_geocache(**kwargs)
        except Exception as exc:
            errors.append(_("OC refresh failed: %(error)s") % {"error": exc})
    else:
        errors.append(_("Unknown source: %(source)s") % {"source": source})

    if errors:
        messages.error(request, errors[0])
    else:
        messages.success(request, _("Refreshed %(code)s from %(source)s.") % {"code": redirect_code, "source": source.upper()})

    return redirect("geocaches:detail", gc_code=redirect_code)


def cache_defuse(request, gc_code):
    """De-fuse a fused GC+OC cache back into two independent records. POST only."""
    from django.contrib import messages

    if request.method != "POST":
        return redirect("geocaches:detail", gc_code=gc_code)

    cache = _get_cache(gc_code)

    if not cache.gc_code or not cache.oc_code:
        messages.error(request, _("Cache is not fused (needs both GC and OC codes)."))
        return redirect("geocaches:detail", gc_code=gc_code)

    oc_platform = cache.oc_platform or "oc_de"

    # Auth checks — both platforms must be accessible before we start mutating.
    from accounts.gc_client import has_api_tokens
    from accounts.keyring_util import get_oauth_token
    from accounts.models import UserAccount

    if not has_api_tokens():
        messages.error(request, _("No GC API tokens — cannot de-fuse without GC API access."))
        return redirect("geocaches:detail", gc_code=gc_code)

    oc_acc = UserAccount.objects.filter(platform=oc_platform).first()
    oc_tokens = get_oauth_token(oc_platform, oc_acc.user_id) if oc_acc else None
    if not oc_tokens:
        messages.error(request, _("No OC OAuth tokens for %(platform)s — cannot de-fuse without OC API access.") % {"platform": oc_platform})
        return redirect("geocaches:detail", gc_code=gc_code)

    from gcprivate.gc_client import GCClient
    from geocaches.sync.oc_client import OCClient
    from geocaches.services.defuse import defuse_cache
    from geocaches.services.fusion import set_fusion_decision

    gc_client = GCClient()
    oc_client = OCClient(platform=oc_platform, user_id=oc_acc.user_id if oc_acc else "")

    result = defuse_cache(cache, gc_client, oc_client)

    # Write a note so the user can evaluate both records
    note_parts = [f"De-fused: {result.gc_code} and {result.oc_code} separated into independent records."]
    if result.oc_import_error:
        note_parts.append(f"OC re-import failed: {result.oc_import_error}")
    if result.gc_refresh_error:
        note_parts.append(f"GC refresh failed: {result.gc_refresh_error}")

    Note.objects.create(
        geocache=cache,
        note_type="note",
        format="plain",
        body="\n".join(note_parts),
    )

    # Record the user's decision so the pair won't be auto-suggested for fusion again
    set_fusion_decision(result.gc_code, result.oc_code, "dont_fuse")

    if result.has_errors:
        messages.warning(request, _("De-fused %(gc)s / %(oc)s with errors — check notes.") % {"gc": result.gc_code, "oc": result.oc_code})
    else:
        messages.success(request, _("De-fused: %(gc)s and %(oc)s are now separate records.") % {"gc": result.gc_code, "oc": result.oc_code})

    return redirect("geocaches:detail", gc_code=cache.display_code)


def cache_delete(request, gc_code):
    """Soft-delete a single cache (move to Trash) — POST only, redirects to list on success."""
    from django.contrib import messages
    from django.utils.http import url_has_allowed_host_and_scheme

    from geocaches.services.trash import trash_cache
    cache = _get_cache(gc_code)
    if request.method == "POST":
        code = cache.display_code
        trash_cache(cache)
        messages.success(request, _("%(code)s moved to Trash.") % {"code": code})
        next_url = request.POST.get("next", "")
        if next_url and url_has_allowed_host_and_scheme(
            next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()
        ):
            return redirect(next_url)
        return redirect("geocaches:list")
    return redirect("geocaches:detail", gc_code=gc_code)


def cache_delete_filtered(request):
    qs, _ = _filtered_qs(request)
    count = qs.count()

    if request.method == "POST":
        from geocaches.tasks.delete import start_deletion
        pk_list = list(qs.values_list("pk", flat=True))
        start_deletion(pk_list)
        return redirect("geocaches:delete_progress")

    return render(request, "geocaches/delete_filtered.html", {
        "count": count,
        "query_string": request.GET.urlencode(),
    })


def cache_delete_progress(request):
    from geocaches.tasks.delete import get_status
    status = get_status()
    auto_refresh = status["running"]
    return render(request, "geocaches/delete_progress.html", {
        "status": status,
        "auto_refresh": auto_refresh,
    })


def cache_enrich(request):
    """Start background enrichment for the filtered cache set and redirect back."""
    from urllib.parse import parse_qs, urlencode as _urlencode
    from django.urls import reverse
    from geocaches.tasks.enrich import start_enrichment

    qs, _ = _filtered_qs(request)

    fields_param = request.GET.get("fields", "all")

    # Offline polygon-based location enrichment goes through its own task —
    # no network, fast, uses the already-downloaded boundary files.
    if fields_param in ("location_offline", "location_offline_update"):
        from geocaches.services import offline_enrich
        from geocaches.tasks import submit_task
        override = fields_param.endswith("_update")

        def _task(*, task_info):
            def report(done, total):
                task_info.total = total
                task_info.completed = done
            return offline_enrich.enrich_all(
                queryset=qs, override=override, progress=report,
            )

        submit_task("Offline location enrichment", _task)
        # Fall through to the redirect at the bottom of the view.
        started = True
    else:
        if fields_param == "elevation":
            fields, overwrite = {"elevation"}, set()
        elif fields_param == "elevation_update":
            fields, overwrite = {"elevation"}, {"elevation"}
        elif fields_param == "elevation_hires":
            fields, overwrite = {"elevation"}, {"elevation_hires"}
        elif fields_param == "location":
            fields, overwrite = {"location"}, set()
        elif fields_param == "location_update":
            fields, overwrite = {"location"}, {"location"}
        else:
            fields, overwrite = {"elevation", "location"}, set()
        started = start_enrichment(qs, fields, overwrite)

    # Redirect back to list, stripping the 'fields' trigger param
    params = parse_qs(request.GET.urlencode(), keep_blank_values=True)
    params.pop("fields", None)
    qs_str = _urlencode(params, doseq=True)
    list_url = reverse("geocaches:list")
    list_target = f"{list_url}?{qs_str}" if qs_str else list_url

    if not started:
        from django.contrib import messages
        messages.warning(request, _("Enrichment is already running."))
    return redirect(list_target)


def _world_country_options():
    """[{iso, name}] for the country picker, taken from the bundled world geojson.
    Cached at module level — file is ~250 KB but parsed once is enough."""
    import json
    from django.conf import settings as dj_settings
    from pathlib import Path
    global _COUNTRY_OPTIONS_CACHE
    if _COUNTRY_OPTIONS_CACHE is None:
        path = Path(dj_settings.BASE_DIR) / "static" / "geo" / "world-countries.geojson"
        gj = json.loads(path.read_text(encoding="utf-8"))
        seen = {}
        for f in gj.get("features", []):
            iso = (f["properties"].get("iso_a2") or "").upper()
            name = f["properties"].get("name") or ""
            if iso and name and iso not in seen:
                seen[iso] = name
        _COUNTRY_OPTIONS_CACHE = sorted(
            [{"iso": iso, "name": name} for iso, name in seen.items()],
            key=lambda x: x["name"].lower(),
        )
    return _COUNTRY_OPTIONS_CACHE


_COUNTRY_OPTIONS_CACHE = None


def cache_location_options(request, gc_code):
    """JSON: dropdown options for the cache-detail edit-location dialog.

    Always returns the country list (from the bundled world geojson).  When
    a ``country`` query param is set, also returns that country's states
    (from the downloaded region boundary, empty list if not on disk).  When
    ``state`` is set too, returns counties for that state.
    """
    import json
    from preferences.services import boundaries

    iso2 = (request.GET.get("country") or "").upper().strip()
    state = (request.GET.get("state") or "").strip()
    result = {"countries": _world_country_options(), "states": [], "counties": []}

    if iso2 and len(iso2) == 2 and iso2.isalpha():
        # States
        region_path = boundaries.boundary_path(iso2)
        if region_path and region_path.exists():
            gj = json.loads(region_path.read_text(encoding="utf-8"))
            names = sorted(
                {f["properties"].get("name", "") for f in gj.get("features", [])
                 if f["properties"].get("name")},
                key=str.lower,
            )
            result["states"] = names
        # Counties (filtered to selected state via parent_state)
        county_path = boundaries.boundary_path(
            iso2, boundaries.effective_county_level(iso2),
        )
        if county_path and county_path.exists() and state:
            gj = json.loads(county_path.read_text(encoding="utf-8"))
            names = sorted(
                {f["properties"].get("name", "") for f in gj.get("features", [])
                 if f["properties"].get("name")
                 and f["properties"].get("parent_state", "") == state},
                key=str.lower,
            )
            result["counties"] = names
    # Make sure the cache exists (404 otherwise).
    _get_cache(gc_code)
    return JsonResponse(result)


def cache_save_location(request, gc_code):
    """POST country/state/county/elevation_user — set manual_location=True.

    Empty ``elevation_user`` clears the override (system value re-appears).
    Empty country/state/county fields are accepted and stored as empty.
    """
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    from django.contrib import messages
    from geocaches.geo.countries import iso_to_name

    cache = _get_cache(gc_code)
    iso = (request.POST.get("country") or "").upper().strip()
    state = (request.POST.get("state") or "").strip()
    county = (request.POST.get("county") or "").strip()
    elev_raw = (request.POST.get("elevation_user") or "").strip()

    if iso and (len(iso) != 2 or not iso.isalpha()):
        return HttpResponseBadRequest("invalid country code")
    if elev_raw:
        try:
            elev_val = float(elev_raw.replace(",", "."))
        except ValueError:
            return HttpResponseBadRequest("invalid elevation")
    else:
        elev_val = None

    update_fields: list[str] = []
    loc_changed = (
        (iso, state, county)
        != (cache.iso_country_code, cache.state, cache.county)
    )
    if loc_changed:
        cache.iso_country_code = iso
        cache.country = iso_to_name(iso) if iso else ""
        cache.state = state
        cache.county = county
        # Flag stays True as long as the user keeps at least one field set.
        # Clearing every location field counts as "give me back enrichment".
        cache.manual_location = bool(iso or state or county)
        update_fields += [
            "iso_country_code", "country", "state", "county", "manual_location",
        ]
    if elev_val != cache.elevation_user:
        cache.elevation_user = elev_val
        update_fields.append("elevation_user")
    if update_fields:
        cache.save(update_fields=update_fields)
    messages.success(request, _("Saved manual location / elevation for %(code)s.") % {
        "code": cache.display_code,
    })
    return redirect("geocaches:detail", gc_code=gc_code)


def enrich_status(request):
    """Return current enrichment status — HTML partial for HTMX, JSON otherwise."""
    from geocaches.tasks.enrich import get_status
    status = get_status()
    if request.headers.get("HX-Request"):
        return render(request, "geocaches/partials/_enrich_progress.html", status)
    return JsonResponse(status)


def enrich_cancel(request):
    """Cancel a running enrichment and redirect back."""
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    from geocaches.tasks.enrich import cancel_enrichment
    cancel_enrichment()
    return redirect(request.META.get("HTTP_REFERER", "/"))


def cache_update(request):
    """Start a bulk API update for the filtered cache set and redirect back."""
    from urllib.parse import parse_qs, urlencode as _urlencode
    from django.urls import reverse
    from django.contrib import messages
    from geocaches.tasks.update import start_update

    qs, _ = _filtered_qs(request)

    action = request.GET.get("action", "")
    kwargs = {}
    if action == "oc_logs":
        kwargs["count"] = int(request.GET.get("count", 50))

    started = start_update(qs, action, **kwargs)

    params = parse_qs(request.GET.urlencode(), keep_blank_values=True)
    params.pop("action", None)
    params.pop("count", None)
    qs_str = _urlencode(params, doseq=True)
    list_url = reverse("geocaches:list")
    list_target = f"{list_url}?{qs_str}" if qs_str else list_url

    if not started:
        messages.warning(request, _("An update task is already running."))
    return redirect(list_target)


def update_status(request):
    """Return current update task status — HTML partial for HTMX, JSON otherwise."""
    from geocaches.tasks.update import get_status
    status = get_status()
    if request.headers.get("HX-Request"):
        return render(request, "geocaches/partials/_update_progress.html", status)
    return JsonResponse(status)


def update_cancel(request):
    """Cancel a running update task and redirect back."""
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    from geocaches.tasks.update import cancel_update
    cancel_update()
    return redirect(request.META.get("HTTP_REFERER", "/"))


def save_map_state(request, gc_code):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    cache = _get_cache(gc_code)
    try:
        # MapLibre reports a fractional zoom (e.g. 14.53); CacheMapState.zoom is
        # an integer field, so round rather than int() (which rejects "14.53").
        zoom = round(float(request.POST["zoom"]))
        lat = float(request.POST["lat"])
        lon = float(request.POST["lon"])
    except (KeyError, ValueError):
        return HttpResponseBadRequest()
    CacheMapState.objects.update_or_create(
        geocache=cache,
        defaults={"zoom": zoom, "lat": lat, "lon": lon},
    )
    return HttpResponse(status=204)


def reset_map_state(request, gc_code):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    cache = _get_cache(gc_code)
    CacheMapState.objects.filter(geocache=cache).delete()
    return HttpResponse(status=204)


def set_map_visibility(request, code):
    """POST a tri-state map visibility (visible|session|always) for one cache.

    Cascades to AL stages when the target is a parent — see
    ``geocaches/services/map_visibility.py``.

    HTMX requests get an inline swap of the ``#map-visibility-control``
    partial; non-HTMX POSTs return JSON for the map context-menu JS.
    """
    from django.contrib import messages

    from ..services.map_visibility import MapVisibility, get_state, set_state

    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    cache = _get_cache(code)
    state = request.POST.get("state", "")
    if state not in MapVisibility.CHOICES:
        return HttpResponseBadRequest(f"Invalid state: {state!r}")

    set_state(cache, state, request.session)
    new_state = get_state(cache, request.session)

    if request.headers.get("HX-Request"):
        # Re-fetch stages so the cascade hint count stays accurate.
        from ..services.adventures import is_al_parent
        stages_count = 0
        if is_al_parent(cache) and cache.adventure_id is not None:
            from ..models import Geocache
            stages_count = Geocache.objects.filter(
                adventure_id=cache.adventure_id, al_detail__isnull=False,
            ).count()
        response = render(request, "geocaches/partials/_map_visibility_control.html", {
            "cache": cache,
            "map_visibility_state": new_state,
            "map_visibility_stages_count": stages_count,
        })
        # Tell any list-view container in the same page to re-fetch its rows
        # so the per-row eye badge reflects the new state (incl. AL stages
        # that the cascade just hid).
        response["HX-Trigger"] = "gcf-map-visibility-changed"
        return response

    if state == MapVisibility.VISIBLE:
        messages.success(request, _("%(code)s is visible on the map.") % {"code": cache.display_code})
    elif state == MapVisibility.SESSION:
        messages.success(request, _("%(code)s hidden on map for this session.") % {"code": cache.display_code})
    else:
        messages.success(request, _("%(code)s hidden on map (always).") % {"code": cache.display_code})
    return JsonResponse({"state": new_state})


def set_as_reference_point(request, gc_code):
    """Create a ReferencePoint from this cache's (corrected) coordinates."""
    from preferences.models import ReferencePoint

    cache = _get_cache(gc_code)
    if request.method == "POST":
        name = request.POST.get("name", "").strip() or gc_code
        use_corrected = request.POST.get("use_corrected") == "1"

        if use_corrected and hasattr(cache, "corrected_coordinates") and cache.corrected_coordinates:
            lat = cache.corrected_coordinates.latitude
            lon = cache.corrected_coordinates.longitude
        else:
            lat = cache.latitude
            lon = cache.longitude

        ReferencePoint.objects.create(
            name=name,
            latitude=lat,
            longitude=lon,
            note=f"From cache {gc_code}",
        )
    return redirect("geocaches:detail", gc_code=gc_code)
