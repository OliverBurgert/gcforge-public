"""Dashboard page — tabbed: placeholder / statistics / maps."""

import re
from urllib.parse import urlencode

from django.http import FileResponse, Http404, HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from preferences import dashboard_maps as maps_config
from preferences.models import ReferencePoint

from ..models import CacheType, CalendarEntry
from ..services import calendar as calendar_service
from ..services import souvenirs as souvenir_service
from ..services import stats as stats_service
from ..services import treasures as treasure_service


def _stats_context(cache_type: str | None):
    return {
        "tables": stats_service.type_filterable_tables(cache_type),
        "minimum_choices": range(1, 11),
        "type_options": stats_service.type_options(),
    }


def _default_ref_id():
    """PK of the default reference point (is_default, else first), or None."""
    rp = (
        ReferencePoint.objects.filter(is_default=True).first()
        or ReferencePoint.objects.first()
    )
    return rp.pk if rp else None


_LATEST_MODES = ("import", "hidden_no_events", "hidden_with_events")


def _latest_additions(limit=10, mode="import"):
    """The most recent caches for the Home tab's "Latest additions" card.

    ``mode`` selects what "latest" means:
      * ``import`` — newest by import date (``imported_at``).
      * ``hidden_no_events`` — newest by hidden/placed date, events excluded.
      * ``hidden_with_events`` — newest by hidden/placed date, events included.

    Each row carries the cache, its distance from the default reference point,
    and ``when`` — the date the chosen mode sorts on, so the list reads as
    reverse-chronological. Placeholders (field-note stubs not yet synced from the
    API) are always skipped.
    """
    from preferences.models import UserPreference

    from ..geo import haversine_km
    from ..models import EVENT_CACHE_TYPES, Geocache

    qs = Geocache.objects.filter(is_placeholder=False).prefetch_related("tags")
    if mode == "import":
        qs = qs.order_by("-imported_at")
    else:
        qs = qs.filter(hidden_date__isnull=False)
        if mode == "hidden_no_events":
            qs = qs.exclude(cache_type__in=EVENT_CACHE_TYPES)
        qs = qs.order_by("-hidden_date")
    caches = list(qs[:limit])
    if not caches:
        return []
    distance_unit = UserPreference.get("distance_unit", "km")
    ref = (
        ReferencePoint.objects.filter(is_default=True).first()
        or ReferencePoint.objects.first()
    )
    rows = []
    for c in caches:
        if ref and c.latitude is not None and c.longitude is not None:
            km = haversine_km(ref.latitude, ref.longitude, c.latitude, c.longitude)
            dist = km if distance_unit == "km" else km * 0.621371
            distance = f"{dist:.1f} {distance_unit}"
        else:
            distance = "—"
        when = c.imported_at if mode == "import" else c.hidden_date
        rows.append({"cache": c, "distance": distance, "when": when})
    return rows


def _build_country_map_list(map_levels):
    """Build the per-country list for the Maps tab's country sub-tabs.

    A country sub-tab appears if EITHER the country-region map OR the county
    map is enabled for that country. Each entry carries enough state for the
    template + JS to decide what to render (region map, county map, or both)
    and whether the boundary data is on disk yet.
    """
    by_type = {m["type"]: m for m in map_levels}
    country_cfg = by_type.get("country", {})
    county_cfg = by_type.get("county", {})
    region_visible = bool(country_cfg.get("visible"))
    county_visible = bool(county_cfg.get("visible"))
    if not (region_visible or county_visible):
        return []

    from geocaches.geo.countries import iso_to_name
    from preferences.services import boundaries

    all_finds = stats_service.finds_by_country_iso()  # {ISO2: count}
    total = sum(all_finds.values()) or 1

    def selection(cfg, visible):
        if not visible:
            return set()
        configured = cfg.get("countries")
        if configured:
            return {iso.upper() for iso in configured if iso.upper() in all_finds}
        return set(all_finds)  # default = all with finds

    region_isos = selection(country_cfg, region_visible)
    county_isos = selection(county_cfg, county_visible)
    iso_list = sorted(region_isos | county_isos,
                      key=lambda iso: -all_finds.get(iso, 0))

    result = []
    for iso in iso_list:
        finds = all_finds.get(iso, 0)
        result.append({
            "iso": iso,
            "name": iso_to_name(iso),
            "downloaded": boundaries.is_downloaded(iso),
            "county_downloaded": boundaries.is_downloaded(
                iso, boundaries.effective_county_level(iso)
            ),
            "show_region": iso in region_isos,
            "show_county": iso in county_isos,
            "finds": finds,
            "pct": finds / total * 100,
        })
    return result


def dashboard(request):
    """Dashboard shell — renders fast with only the lightweight Home (calendar)
    tab's controls.

    Each heavy tab (Statistics, Adventure Lab, Maps, the D/T-Jasmer tables)
    loads its body in the background via HTMX once the shell is on screen, so
    the page is interactive immediately and the other tabs are usually ready by
    the time the user clicks them. See :func:`dashboard_statistics`,
    :func:`dashboard_alc`, :func:`dashboard_maps_panel` and the existing
    :func:`dashboard_stat_tables`.
    """
    context = {
        "ref_points": list(ReferencePoint.objects.values("id", "name")),
        "default_ref_id": _default_ref_id(),
        "h360_default_km": 100,
        # Cheap controls shared by the Home and D/T-Jasmer tabs.
        "type_options": stats_service.type_options(),
        "minimum_choices": range(1, 11),
        **souvenir_service.dashboard_context(),
        **treasure_service.dashboard_context(),
        "calendar_feed_urls": calendar_service.feed_urls(request),
        "agenda_days": calendar_service.get_agenda_days(),
        "calendar_total": CalendarEntry.objects.count(),
    }
    return render(request, "geocaches/dashboard.html", context)


def dashboard_latest_additions(request):
    """HTMX partial: the Home tab's "Latest additions" list.

    ``?count=N`` (1–100) and ``?mode=`` (import / hidden_no_events /
    hidden_with_events) update and persist how the list is built. Both
    selections are remembered via ``UserPreference``.
    """
    from preferences.models import UserPreference

    raw = request.GET.get("count")
    if raw is not None:
        try:
            UserPreference.set("dashboard_latest_count", min(max(int(raw), 1), 100))
        except (TypeError, ValueError):
            pass
    if request.GET.get("mode") in _LATEST_MODES:
        UserPreference.set("dashboard_latest_mode", request.GET["mode"])
    try:
        count = min(max(int(UserPreference.get("dashboard_latest_count", 10)), 1), 100)
    except (TypeError, ValueError):
        count = 10
    mode = UserPreference.get("dashboard_latest_mode", "import")
    if mode not in _LATEST_MODES:
        mode = "import"
    return render(request, "geocaches/partials/_dashboard_latest_additions.html", {
        "latest_additions": _latest_additions(count, mode),
        "latest_count": count,
        "latest_mode": mode,
    })


def dashboard_statistics(request):
    """HTMX partial: the Statistics tab body (summary numbers + charts)."""
    return render(request, "geocaches/partials/_dashboard_statistics.html", {
        "summary": stats_service.summary_stats(),
        "by_type": stats_service.finds_by_type(),
        "by_size": stats_service.finds_by_size(),
        "by_difficulty": stats_service.finds_by_rating("difficulty", "D"),
        "by_terrain": stats_service.finds_by_rating("terrain", "T"),
        "by_year": stats_service.finds_by_year(),
        "by_month": stats_service.finds_by_month(),
        "cumulative_finds": stats_service.finds_cumulative_by_month(),
        "bearing": stats_service.finds_by_bearing(),
        "ref_points": list(ReferencePoint.objects.values("id", "name")),
    })


def dashboard_alc(request):
    """HTMX partial: the Adventure Lab tab body.

    Always available, independent of the include-AL statistics toggle.
    """
    return render(request, "geocaches/partials/_dashboard_alc.html", {
        "alc_summary": stats_service.alc_summary(),
        "alc_by_country": stats_service.alc_finds_by_country(),
        "alc_found_date": stats_service.alc_finds_by_found_date(),
        "alc_cumulative": stats_service.alc_cumulative_by_month(),
        "alc_by_theme": stats_service.alc_theme_breakdown(),
        "alc_lab_type": CacheType.LAB.value,
        "minimum_choices": range(1, 11),
    })


def dashboard_maps_panel(request):
    """HTMX partial: the Maps tab body — sub-tab nav + choropleth roots."""
    map_levels = maps_config.get_config()
    # Maps the Maps tab can render: bundled levels + country when visible.
    renderable = [
        m for m in map_levels
        if m["visible"] and (
            m["type"] in maps_config.BUNDLED_LEVELS or m["type"] == "country"
        )
    ]
    country_maps = _build_country_map_list(map_levels)
    return render(request, "geocaches/partials/_dashboard_maps.html", {
        "map_levels": renderable,
        "country_counts": stats_service.finds_by_country_iso(),
        "all_country_counts": stats_service.all_by_country_iso(),
        "country_maps": country_maps,
        "show_world_subtab": any(
            m["type"] in {"world", "continent"} for m in renderable
        ),
        "show_countries_subtab": bool(country_maps),
        # Show the "enable more maps" note whenever something is switched off.
        "maps_note": any(not m["visible"] for m in map_levels),
        "maps_settings_url": reverse("preferences:settings") + "?tab=dashboard#dashboard",
    })


def dashboard_stat_tables(request):
    """HTMX partial: the four type-filterable tables for a chosen type."""
    cache_type = (request.GET.get("stat_type") or "").strip() or None
    return render(
        request,
        "geocaches/partials/_dashboard_stat_tables.html",
        _stats_context(cache_type),
    )


_STAT_TABLE_IDS = {"dt", "found_date", "placed_month", "placed_date"}


def dashboard_stat_table(request):
    """HTMX partial: one type-filterable table section, for per-table type select."""
    table = request.GET.get("table", "")
    if table not in _STAT_TABLE_IDS:
        raise Http404("unknown table")
    cache_type = (request.GET.get("stat_type") or "").strip() or None
    return render(
        request,
        f"geocaches/partials/_dashboard_stat_{table}.html",
        _stats_context(cache_type),
    )


def dashboard_alc_theme(request):
    """Filter the list view from a clickable count in the AL tab's theme table.

    ``?theme=<token>`` (empty = the "no theme" bucket) + ``&status=``:
    ``completed`` / ``incomplete`` / ``not_started`` list parent adventures;
    ``stages`` lists the found lab stages of that theme.
    """
    theme = request.GET.get("theme", "")
    status = request.GET.get("status", "")
    if status == "stages":
        ids = stats_service.alc_theme_stage_ids(theme)
    elif status in ("completed", "incomplete", "not_started"):
        ids = stats_service.alc_theme_parent_ids(theme, status)
    else:
        ids = []
    sql = "id IN (%s)" % ",".join(str(i) for i in ids) if ids else "1 = 0"
    return redirect(reverse("geocaches:list") + "?" + urlencode({"where_sql": sql}))


def dashboard_bearing_data(request):
    """JSON: bearing wind-rose data for one reference point (``?ref=<id>``).

    Drives the per-chart reference-point selector — the page re-renders the two
    roses client-side from this payload without a full reload.
    """
    raw = request.GET.get("ref")
    try:
        ref_id = int(raw) if raw else None
    except (TypeError, ValueError):
        ref_id = None
    data = stats_service.finds_by_bearing(ref_id)
    if data is None:
        return JsonResponse({"error": "no reference point"}, status=404)
    return JsonResponse(data)


def dashboard_360_data(request):
    """JSON: 360-sectors-from-a-location data (``?ref=<id>&max_km=<float>``).

    Feeds the "360° from Location" tab — Overview ring, Table and density Map
    all render client-side from this single payload.
    """
    raw = request.GET.get("ref")
    try:
        ref_id = int(raw) if raw else None
    except (TypeError, ValueError):
        ref_id = None
    try:
        max_km = float(request.GET.get("max_km", 100))
    except (TypeError, ValueError):
        max_km = 100.0
    max_km = min(max(max_km, 1.0), 20000.0)
    corrected = request.GET.get("corrected") in ("1", "true", "on", "yes")

    data = stats_service.finds_360(ref_id, max_km, use_corrected=corrected)
    if data is None:
        return JsonResponse({"error": "no reference point"}, status=404)
    return JsonResponse(data)


def dashboard_360_missing(request):
    """Find unfound caches in the under-goal bearing sectors within max_km of the
    location, and bounce to the filtered list view (``?ref=&max_km=&goal=&corrected=``)."""
    raw = request.GET.get("ref")
    try:
        ref_id = int(raw) if raw else None
    except (TypeError, ValueError):
        ref_id = None
    try:
        max_km = float(request.GET.get("max_km", 100))
    except (TypeError, ValueError):
        max_km = 100.0
    max_km = min(max(max_km, 1.0), 20000.0)
    try:
        goal = int(request.GET.get("goal", 1))
    except (TypeError, ValueError):
        goal = 1
    goal = min(max(goal, 1), 10)
    corrected = request.GET.get("corrected") in ("1", "true", "on", "yes")

    data = stats_service.finds_360(ref_id, max_km, use_corrected=corrected)
    if data is None:
        return redirect(reverse("geocaches:list"))
    incomplete = [s["i"] for s in data["sectors"] if s["count"] < goal]
    sql = stats_service.build_360_missing_where_sql(
        data["ref_id"], max_km, incomplete, use_corrected=corrected
    )
    return redirect(reverse("geocaches:list") + "?" + urlencode({"where_sql": sql}))


def dashboard_360_grid(request):
    """JSON: grid-search candidates around the location
    (``?ref=&max_km=&goal=&corrected=&grid_width=``)."""
    raw = request.GET.get("ref")
    try:
        ref_id = int(raw) if raw else None
    except (TypeError, ValueError):
        ref_id = None
    try:
        max_km = float(request.GET.get("max_km", 100))
    except (TypeError, ValueError):
        max_km = 100.0
    max_km = min(max(max_km, 1.0), 20000.0)
    try:
        goal = int(request.GET.get("goal", 1))
    except (TypeError, ValueError):
        goal = 1
    goal = min(max(goal, 1), 10)
    try:
        grid_width = int(request.GET.get("grid_width", 9))
    except (TypeError, ValueError):
        grid_width = 9
    corrected = request.GET.get("corrected") in ("1", "true", "on", "yes")

    data = stats_service.grid_search_360(ref_id, max_km, goal, grid_width, corrected)
    if data is None:
        return JsonResponse({"error": "no reference point"}, status=404)
    return JsonResponse(data)


@require_POST
def dashboard_360_set_location(request):
    """Move a reference point to new coordinates (the grid-search "update
    location" action).  Saving fires the signal that invalidates its
    DistanceCache."""
    try:
        ref_id = int(request.POST.get("ref"))
        lat = float(request.POST.get("lat"))
        lon = float(request.POST.get("lon"))
    except (TypeError, ValueError):
        return JsonResponse({"error": "bad parameters"}, status=400)
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return JsonResponse({"error": "out of range"}, status=400)
    rp = ReferencePoint.objects.filter(pk=ref_id).first()
    if rp is None:
        return JsonResponse({"error": "no such location"}, status=404)
    rp.latitude = lat
    rp.longitude = lon
    rp.save()
    return JsonResponse({"ok": True, "lat": lat, "lon": lon})


def dashboard_souvenirs_list(request):
    """HTMX partial: the souvenir controls + list (order dropdown, tag filter,
    grouped cards), re-rendered with state preserved on any change.

    ``order`` = ``date`` | ``year`` | ``tag``; with ``tagfilter=1`` the ``tag``
    params (repeatable ids) + ``untagged`` checkbox filter which show.
    """
    order = request.GET.get("order", "date")
    if order not in ("date", "year", "tag"):
        order = "date"
    summary = souvenir_service.tag_summary()
    all_ids = [t["id"] for t in summary["tags"]]
    if request.GET.get("tagfilter"):
        selected = [int(t) for t in request.GET.getlist("tag") if t.isdigit()]
        untagged = request.GET.get("untagged") == "1"
        tag_ids = selected
    else:
        selected, untagged, tag_ids = all_ids, True, None  # default: show all
    ctx = souvenir_service.view_data(order, tag_ids, untagged)
    ctx.update({
        "souvenir_tags": summary,
        "selected_tags": set(selected),
        "untagged_checked": untagged,
    })
    return render(request, "geocaches/partials/_souvenirs_body.html", ctx)


def dashboard_souvenirs_tag_editor(request):
    """HTMX partial: the per-souvenir tag-edit form shown in the modal."""
    from geocaches.models import Souvenir, SouvenirTag
    try:
        sid = int(request.GET.get("sid", ""))
    except (TypeError, ValueError):
        raise Http404("bad souvenir id") from None
    souvenir = Souvenir.objects.filter(pk=sid).first()
    if souvenir is None:
        raise Http404("no such souvenir")
    return render(request, "geocaches/partials/_souvenir_tag_editor.html", {
        "souvenir": souvenir,
        "all_tags": SouvenirTag.objects.all(),
        "assigned": set(souvenir.tags.values_list("id", flat=True)),
    })


@require_POST
def dashboard_souvenirs_set_tags(request):
    """Replace a souvenir's tags from the modal; reload list + close modal."""
    try:
        sid = int(request.POST.get("sid", ""))
    except (TypeError, ValueError):
        return JsonResponse({"error": "bad id"}, status=400)
    tag_ids = [t for t in request.POST.getlist("tag") if t.isdigit()]
    souvenir_service.set_tags(sid, tag_ids, request.POST.get("new_tag", ""))
    resp = HttpResponse(status=204)
    resp["HX-Trigger"] = "souvenirs:reload, souvenir-tags-saved"
    return resp


def dashboard_souvenirs_tags_manage(request):
    """GET: render the tag-management panel (modal body).
    POST: create / rename / delete a tag, re-render the panel, reload the list."""
    if request.method == "POST":
        action = request.POST.get("_action")
        if action == "create":
            souvenir_service.create_tag(request.POST.get("name", ""))
        elif action == "rename":
            souvenir_service.rename_tag(request.POST.get("tag_id"), request.POST.get("name", ""))
        elif action == "delete":
            souvenir_service.delete_tag(request.POST.get("tag_id"))
    resp = render(request, "geocaches/partials/_souvenir_tags_manage.html",
                  {"souvenir_tags": souvenir_service.tag_summary()})
    if request.method == "POST":
        resp["HX-Trigger"] = "souvenirs:reload"
    return resp


@require_POST
def dashboard_souvenirs_refresh(request):
    """Fetch souvenirs from the GC API (``?mode=all|latest``) and upsert."""
    mode = request.GET.get("mode", "latest")
    try:
        summary = (
            souvenir_service.refresh_all() if mode == "all"
            else souvenir_service.refresh_latest()
        )
    except Exception as exc:  # noqa: BLE001 — surface the API error to the user
        return JsonResponse({"error": str(exc)[:200]}, status=502)
    return JsonResponse({"ok": True, **summary})


def dashboard_treasures_list(request):
    """HTMX partial: the treasure collections list."""
    return render(request, "geocaches/partials/_treasures_list.html", {
        "collections": treasure_service.list_collections(),
    })


@require_POST
def dashboard_treasures_refresh(request):
    """Scrape geocaching.com Treasures via the web session and upsert."""
    try:
        summary = treasure_service.refresh()
    except Exception as exc:  # noqa: BLE001 — surface the scrape error to the user
        return JsonResponse({"error": str(exc)[:200]}, status=502)
    return JsonResponse({"ok": True, **summary})


def dashboard_treasures_candidates(request):
    """Build a candidates filter from a collection's criteria and bounce to the
    list view (unfound caches that would earn the collection's Treasures)."""
    try:
        cid = int(request.GET.get("collection", ""))
    except (TypeError, ValueError):
        return redirect(reverse("geocaches:list"))
    from geocaches.models import TreasureCollection
    col = TreasureCollection.objects.filter(collection_id=cid).first()
    if col is None:
        return redirect(reverse("geocaches:list"))
    sql = treasure_service.criteria_to_where_sql(col.criteria)
    return redirect(reverse("geocaches:list") + "?" + urlencode({"where_sql": sql}))


def dashboard_treasures_detail(request):
    """HTMX partial (modal body): one collection's criteria + treasure artwork,
    fetched live from gc.com."""
    try:
        cid = int(request.GET.get("collection", ""))
    except (TypeError, ValueError):
        raise Http404("bad collection id") from None
    from geocaches.models import TreasureCollection
    col = TreasureCollection.objects.filter(collection_id=cid).first()
    if col is None:
        raise Http404("no such collection")
    reveal = request.GET.get("reveal") == "1"
    try:
        detail = treasure_service.fetch_detail(cid, reveal_locked=reveal)
        error = ""
    except Exception as exc:  # noqa: BLE001 — surface the scrape error in the modal
        detail, error = None, str(exc)[:200]
    return render(request, "geocaches/partials/_treasure_detail.html", {
        "collection": col, "detail": detail, "error": error, "reveal": reveal,
    })


_MISSING_WHICH = {"all", "dt", "placed_month", "placed_date"}


def dashboard_missing(request):
    """Build a "find missing in DB" WHERE clause and bounce to the list view.

    The list view applies ``?where_sql=`` so the user lands on the filtered
    split/list view and can stack more filters (tags, etc.) on top.
    """
    which = request.GET.get("which", "all")
    if which not in _MISSING_WHICH:
        which = "all"
    cache_type = (request.GET.get("stat_type") or "").strip() or None
    try:
        minimum = max(1, int(request.GET.get("minimum", 1)))
    except (TypeError, ValueError):
        minimum = 1

    sql = stats_service.build_missing_where_sql(which, cache_type, minimum) or "1 = 0"
    return redirect(reverse("geocaches:list") + "?" + urlencode({"where_sql": sql}))


_REGION_FLAG_CODE_RE = re.compile(r"^[a-z0-9]{2}-[a-z0-9]{1,4}$")


def dashboard_region_flag(request, code):
    """Serve one cached region flag PNG (downloaded with the boundary).

    Code is the lowercased ISO 3166-2 (e.g. ``de-by``).  Validated via regex to
    keep the file lookup inside the boundaries dir — no path traversal.
    """
    from preferences.services import boundaries

    if not _REGION_FLAG_CODE_RE.match(code or ""):
        raise Http404("invalid region code")
    path = boundaries.flag_path(code)
    if not path.exists():
        raise Http404("flag not cached")
    return FileResponse(path.open("rb"), content_type="image/png")


def dashboard_region_data(request, iso2):
    """JSON: a downloaded country's region GeoJSON with per-region find counts.

    404 when the country's region boundary hasn't been downloaded yet.
    """
    from preferences.services import boundaries

    iso2 = (iso2 or "").upper()
    if len(iso2) != 2 or not iso2.isalpha():
        return JsonResponse({"error": "invalid country code"}, status=400)
    data = boundaries.region_map_data(iso2)
    if data is None:
        return JsonResponse({"error": "boundary not downloaded"}, status=404)
    return JsonResponse(data)


def dashboard_county_data(request, iso2):
    """JSON: a downloaded country's county GeoJSON with per-county find counts.

    404 when the county boundary hasn't been downloaded yet.
    """
    from preferences.services import boundaries

    iso2 = (iso2 or "").upper()
    if len(iso2) != 2 or not iso2.isalpha():
        return JsonResponse({"error": "invalid country code"}, status=400)
    data = boundaries.county_map_data(iso2)
    if data is None:
        return JsonResponse({"error": "county boundary not downloaded"}, status=404)
    return JsonResponse(data)


def dashboard_district_data(request, iso2, state):
    """JSON: a single-county state's sub-county district (Bezirk/ward) GeoJSON
    with per-district find counts.  404 when its districts haven't been fetched
    (``manage.py fetch_districts``)."""
    from preferences.services import boundaries

    iso2 = (iso2 or "").upper()
    if len(iso2) != 2 or not iso2.isalpha():
        return JsonResponse({"error": "invalid country code"}, status=400)
    data = boundaries.district_map_data(iso2, state)
    if data is None:
        return JsonResponse({"error": "districts not downloaded"}, status=404)
    return JsonResponse(data)
