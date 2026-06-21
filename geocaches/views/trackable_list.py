"""Trackable list, map-page, and map-pins views — Phase 2 §3.3."""
from __future__ import annotations

import logging

from django.core.paginator import Paginator
from django.db.models import F, Q
from django.http import JsonResponse
from django.shortcuts import render
from django.urls import reverse
from django.views.decorators.http import require_POST

from geocaches.models import Trackable, TrackableHolderState, TrackableKind

logger = logging.getLogger(__name__)

PAGE_SIZE = 50

# (field_name, label, default_order_when_not_active)
TB_COLUMNS = [
    ("reference_code",    "Ref Code",   "asc"),
    ("name",              "Name",       "asc"),
    ("kind",              "Kind",       "asc"),
    ("series",            "Series",     "asc"),
    ("",                  "Tags",       ""),
    ("holder_state",      "State",      "asc"),
    ("owner_name",        "Owner",      "asc"),
    ("",                  "Location",   ""),
    ("last_log_date",     "Last Log",   "desc"),
    ("total_visits",      "Visits",     "desc"),
    ("total_distance_km", "Distance",   "desc"),
]

SORT_FIELDS = {
    "reference_code",
    "name",
    "kind",
    "series",
    "holder_state",
    "owner_name",
    "last_log_date",
    "total_visits",
    "total_distance_km",
}

_DATE_FIELDS = {"last_log_date"}
_NUM_FIELDS  = {"total_visits", "total_distance_km"}


def _resolve_gc_username() -> str:
    try:
        from accounts.models import UserAccount
        acct = UserAccount.objects.filter(platform="gc").first()
        if acct and acct.username:
            return acct.username
    except Exception:  # noqa: BLE001
        pass
    try:
        from preferences.models import UserPreference
        return UserPreference.get("gc_username", "")
    except Exception:  # noqa: BLE001
        return ""


def _read_filters(request) -> dict:
    return {
        "q":      request.GET.get("q", "").strip(),
        "state":  request.GET.get("state", ""),
        "kind":   request.GET.get("kind", ""),
        "series": request.GET.get("series", ""),
        "tag":    request.GET.get("tag", ""),
        "mine":   request.GET.get("mine", ""),
        "held":   request.GET.get("held", ""),
    }


def _apply_filters(qs, fv: dict, gc_username: str):
    if fv["q"]:
        qs = qs.filter(
            Q(reference_code__icontains=fv["q"])
            | Q(name__icontains=fv["q"])
            | Q(owner_name__icontains=fv["q"])
        )
    if fv["state"]:
        qs = qs.filter(holder_state=fv["state"])
    if fv["kind"]:
        qs = qs.filter(kind=fv["kind"])
    if fv["series"]:
        qs = qs.filter(series=fv["series"])
    if fv["tag"]:
        qs = qs.filter(tags__name=fv["tag"])
    if fv["mine"] == "1" and gc_username:
        qs = qs.filter(owner_name__iexact=gc_username)
    if fv["held"] == "1":
        qs = qs.filter(holder_state=TrackableHolderState.HELD_BY_USER)
    return qs


def _build_list_context(request):
    qs = Trackable.objects.all().prefetch_related("tags")

    fv       = _read_filters(request)
    f_sort   = request.GET.get("sort", "last_log_date")
    f_order  = request.GET.get("order", "desc")

    if f_sort not in SORT_FIELDS:
        f_sort = "last_log_date"
    if f_order not in ("asc", "desc"):
        f_order = "desc"

    gc_username = _resolve_gc_username()
    qs = _apply_filters(qs, fv, gc_username)

    field_expr = F(f_sort)
    if f_sort in _DATE_FIELDS or f_sort in _NUM_FIELDS:
        if f_order == "desc":
            qs = qs.order_by(field_expr.desc(nulls_last=True))
        else:
            qs = qs.order_by(field_expr.asc(nulls_first=True))
    else:
        if f_order == "desc":
            qs = qs.order_by(field_expr.desc())
        else:
            qs = qs.order_by(field_expr.asc())

    total = qs.count()
    paginator = Paginator(qs, PAGE_SIZE)
    page_obj = paginator.get_page(request.GET.get("page", 1))

    series_choices = list(
        Trackable.objects.exclude(series="").values_list("series", flat=True)
        .distinct().order_by("series")
    )
    from geocaches.models import Tag
    tag_choices = list(
        Tag.objects.filter(trackables__isnull=False).distinct().order_by("name")
        .values_list("name", flat=True)
    )

    ctx = {
        "page_obj":       page_obj,
        "total":          total,
        "f_q":            fv["q"],
        "f_state":        fv["state"],
        "f_kind":         fv["kind"],
        "f_series":       fv["series"],
        "f_tag":          fv["tag"],
        "f_mine":         fv["mine"],
        "f_held":         fv["held"],
        "f_sort":         f_sort,
        "f_order":        f_order,
        "kind_choices":   TrackableKind.choices,
        "state_choices":  TrackableHolderState.choices,
        "series_choices": series_choices,
        "tag_choices":    tag_choices,
        "gc_username":    gc_username,
        "tb_columns":     TB_COLUMNS,
    }
    return ctx


def trackable_list(request):
    ctx = _build_list_context(request)
    template = (
        "geocaches/partials/_trackable_table.html"
        if request.headers.get("HX-Request") else
        "geocaches/trackable_list.html"
    )
    return render(request, template, ctx)


def trackable_split(request):
    """List + detail-pane split view at /trackables/split/.

    Reuses the list-view context (same filter form + table partial) and
    embeds the detail page on the right via HTMX swap when a row is clicked.
    """
    ctx = _build_list_context(request)
    template = (
        "geocaches/partials/_trackable_table.html"
        if request.headers.get("HX-Request") else
        "geocaches/trackable_split.html"
    )
    return render(request, template, ctx)


def trackable_map(request):
    fv = _read_filters(request)
    gc_username = _resolve_gc_username()

    qs_all = _apply_filters(Trackable.objects.all(), fv, gc_username)
    pin_count_with_coords    = qs_all.filter(current_lat__isnull=False, current_lon__isnull=False).count()
    pin_count_without_coords = qs_all.filter(
        Q(current_lat__isnull=True) | Q(current_lon__isnull=True)
    ).count()

    series_choices = list(
        Trackable.objects.exclude(series="").values_list("series", flat=True)
        .distinct().order_by("series")
    )

    from preferences.models import ReferencePoint
    home_ref = ReferencePoint.objects.filter(is_home=True).first()

    ctx = {
        "f_q":                    fv["q"],
        "f_state":                fv["state"],
        "f_kind":                 fv["kind"],
        "f_series":               fv["series"],
        "f_mine":                 fv["mine"],
        "f_held":                 fv["held"],
        "gc_username":            gc_username,
        "kind_choices":           TrackableKind.choices,
        "state_choices":          TrackableHolderState.choices,
        "series_choices":         series_choices,
        "home_ref":               home_ref,
        "pin_count_with_coords":  pin_count_with_coords,
        "pin_count_without_coords": pin_count_without_coords,
    }
    return render(request, "geocaches/trackable_map.html", ctx)


def trackable_map_pins(request):
    from geocaches.services.image_cache import url_for as _img_url_for

    fv = _read_filters(request)
    gc_username = _resolve_gc_username()

    qs = Trackable.objects.filter(current_lat__isnull=False, current_lon__isnull=False)
    qs = _apply_filters(qs, fv, gc_username)

    pins = [
        {
            "ref":          tb.reference_code,
            "name":         tb.name,
            "kind":         tb.kind,
            "series":       tb.series,
            "state":        tb.holder_state,
            "state_label":  tb.get_holder_state_display(),
            "lat":          tb.current_lat,
            "lon":          tb.current_lon,
            "icon_url":     _img_url_for(tb.icon_url, category="tb_icon"),
            "cache_code":   tb.current_geocache_code,
            "cache_name":   tb.current_geocache_name,
            "holder_name":  tb.current_holder_name,
            "distance_km":  tb.total_distance_km,
            "detail_url":   reverse("geocaches:trackable_detail", args=[tb.reference_code]),
        }
        for tb in qs
    ]
    return JsonResponse({"ok": True, "pins": pins})


_SCOPE_TAGS = {
    "mine":       "",            # default; existing behaviour, no auto-tag
    "discovered": "discovered",
    "moved":      "moved",
}


@require_POST
def trackable_sync_plan(request):
    """Return the list of TB refs to sync for the requested scope.

    POST `scope` ∈ {mine, discovered, moved}. Default: mine (inventory +
    collection + owned, deduped). ``discovered`` and ``moved`` map to
    dedicated GC API filter values via ``TrackableClient``.
    """
    from gcprivate.trackable_client import TrackableClient

    scope = (request.POST.get("scope") or "mine").strip()
    if scope not in _SCOPE_TAGS:
        return JsonResponse({"ok": False, "error": f"unknown scope: {scope!r}"}, status=200)

    try:
        client = TrackableClient()
        if scope == "mine":
            items = (
                client.get_my_inventory()
                + client.get_my_collection()
                + client.get_owned_trackables()
            )
            refs = []
            seen: set[str] = set()
            for item in items:
                ref = (item.get("reference_code") or "").strip().upper()
                if ref and ref not in seen:
                    seen.add(ref)
                    refs.append(ref)
        elif scope == "discovered":
            refs = client.get_my_discovered_refs()
        elif scope == "moved":
            refs = client.get_my_moved_refs()
        else:
            refs = []
    except Exception as exc:  # noqa: BLE001
        logger.error("trackable_sync_plan(%s): %s", scope, exc)
        return JsonResponse({"ok": False, "error": str(exc)}, status=200)

    skipped = 0
    if scope in ("discovered", "moved") and refs:
        # Skip TBs we've recently re-synced — discovered/moved entries are
        # imported once and rarely need a refresh, and these lists can run
        # to thousands of refs for prolific cachers.
        from datetime import datetime, timedelta, timezone
        try:
            stale_days = max(0, int(request.POST.get("skip_days", "30")))
        except (ValueError, TypeError):
            stale_days = 30
        if stale_days > 0:
            cutoff = datetime.now(timezone.utc) - timedelta(days=stale_days)
            recent = set(
                Trackable.objects
                .filter(reference_code__in=refs, updated_at__gte=cutoff)
                .values_list("reference_code", flat=True)
            )
            if recent:
                before = len(refs)
                refs = [r for r in refs if r not in recent]
                skipped = before - len(refs)

    return JsonResponse({
        "ok":      True,
        "refs":    refs,
        "scope":   scope,
        "tag":     _SCOPE_TAGS[scope],
        "skipped": skipped,
    })


@require_POST
def trackable_tracking_codes_plan(request):
    """Return refs of owned TBs that have no tracking code stored locally.

    Owners can see the private tracking number on the GC website even when
    they're not the current holder — but the API only exposes it to the
    holder. This plan drives the website-scrape fallback.
    """
    gc_username = _resolve_gc_username()
    if not gc_username:
        return JsonResponse({"ok": False, "error": "No GC username configured."}, status=200)
    refs = list(
        Trackable.objects
        .filter(owner_name__iexact=gc_username, tracking_code="")
        .order_by("reference_code")
        .values_list("reference_code", flat=True)
    )
    return JsonResponse({"ok": True, "refs": refs})


@require_POST
def trackable_tracking_code_fetch(request):
    """Scrape one owned TB's tracking code from the GC website + persist it."""
    ref = (request.POST.get("ref") or "").strip().upper()
    if not ref:
        return JsonResponse({"ok": False, "error": "missing ref"}, status=400)
    tb = Trackable.objects.filter(reference_code=ref).first()
    if tb is None:
        return JsonResponse({"ok": False, "ref": ref, "error": "not in local DB"})
    try:
        from gcprivate.tb_tracking_scrape import fetch_tracking_code
        code = fetch_tracking_code(ref)
    except Exception as exc:  # noqa: BLE001
        logger.warning("trackable_tracking_code_fetch(%s): %s", ref, exc)
        return JsonResponse({"ok": False, "ref": ref, "error": str(exc)})
    if not code:
        return JsonResponse({"ok": False, "ref": ref, "error": "tracking code not found on page"})
    if tb.tracking_code != code:
        tb.tracking_code = code
        tb.save(update_fields=["tracking_code", "updated_at"])
    return JsonResponse({"ok": True, "ref": ref, "code": code})


@require_POST
def trackable_resolve_locations(request):
    """Fetch GC API coordinates for filtered TBs whose current cache is not in
    the local DB (current_geocache_code set, current_lat null).

    Filter params (q, state, kind, series, tag, mine) come from the POST body
    so the JS can forward the page's current query string.
    """
    from geocaches.services.trackable_sync import resolve_tb_locations

    fv = {
        "q":      request.POST.get("q", "").strip(),
        "state":  request.POST.get("state", ""),
        "kind":   request.POST.get("kind", ""),
        "series": request.POST.get("series", ""),
        "tag":    request.POST.get("tag", ""),
        "mine":   request.POST.get("mine", ""),
        "held":   request.POST.get("held", ""),
    }
    gc_username = _resolve_gc_username()
    qs = _apply_filters(Trackable.objects.all(), fv, gc_username)

    try:
        result = resolve_tb_locations(qs)
    except Exception as exc:  # noqa: BLE001
        logger.error("trackable_resolve_locations: %s", exc)
        return JsonResponse({"ok": False, "error": str(exc)})

    return JsonResponse({"ok": True, **result})


@require_POST
def trackable_sync_one(request):
    """Sync metadata + images for one TB. No logs (slow; opt-in elsewhere).

    Optional ``tag`` POST field attaches a Tag (auto-created) to the
    newly synced trackable. Used by the discovered/moved sync flows.
    """
    from geocaches.services.trackable_sync import sync_trackable
    ref = (request.POST.get("ref") or "").strip().upper()
    if not ref:
        return JsonResponse({"ok": False, "error": "missing ref"}, status=400)
    try:
        tb = sync_trackable(ref)
    except Exception as exc:  # noqa: BLE001
        logger.warning("trackable_sync_one(%s): %s", ref, exc)
        return JsonResponse({"ok": False, "ref": ref, "error": str(exc)})

    tag_name = (request.POST.get("tag") or "").strip()
    if tag_name:
        from geocaches.models import Tag
        tag, _ = Tag.objects.get_or_create(name=tag_name)
        tb.tags.add(tag)

    return JsonResponse({"ok": True, "ref": ref, "name": tb.name})
