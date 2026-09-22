from django.http import HttpResponseNotAllowed

from preferences.models import UserPreference
from ._helpers import _redirect_tab


def _build_country_finds(country_cfg=None, county_cfg=None):
    """Return one dict per country with finds, sorted by count desc.

    Each entry: ``{iso, name, count, downloaded, download_info, selected,
    county_downloaded, county_selected}`` where the ``county_*`` fields mirror
    the region ones but for the second-tier boundary. ``selected`` /
    ``county_selected`` reflect the saved selection (None config → all on).
    """
    from geocaches.geo.countries import iso_to_name
    from geocaches.services import stats
    from preferences.services import boundaries

    raw = stats.finds_by_country_iso()
    manifest = boundaries.status()
    region_configured = (country_cfg or {}).get("countries")
    county_configured = (county_cfg or {}).get("countries")
    region_selected = (
        {iso.upper() for iso in region_configured}
        if isinstance(region_configured, list) else None
    )
    county_selected = (
        {iso.upper() for iso in county_configured}
        if isinstance(county_configured, list) else None
    )
    rows = []
    for iso, cnt in raw.items():
        info = manifest.get(f"{iso}_{boundaries.effective_level(iso)}")
        cty_info = manifest.get(f"{iso}_{boundaries.effective_county_level(iso)}")
        rows.append({
            "iso": iso,
            "name": iso_to_name(iso),
            "count": cnt,
            "downloaded": bool(info),
            "download_info": info,
            "selected": True if region_selected is None else iso in region_selected,
            "county_downloaded": bool(cty_info),
            "county_download_info": cty_info,
            "county_selected": (
                True if county_selected is None else iso in county_selected
            ),
        })
    rows.sort(key=lambda r: -r["count"])
    return rows


def _countries_without_finds(existing_isos) -> list[dict]:
    """Every ISO country not already listed in the finds table AND not already
    downloaded — lets the user pre-download boundaries for a country they plan
    to visit but haven't found anything in yet (so it wouldn't otherwise appear
    anywhere), without cluttering the list with countries already handled."""
    from geocaches.geo.countries import all_countries
    from preferences.services import boundaries

    manifest_isos = {info["iso2"] for info in boundaries.status().values()}
    existing = {iso.upper() for iso in existing_isos} | manifest_isos
    return [{"iso": iso, "name": name}
            for iso, name in all_countries() if iso not in existing]


def save_dashboard_maps(request):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    from django.utils.translation import gettext as _
    from preferences import dashboard_maps as dashboard_maps_mod
    config = {m["type"]: m for m in dashboard_maps_mod.get_config()}
    # Bundled levels: world + continent.
    for lvl in dashboard_maps_mod.BUNDLED_LEVELS:
        m = config[lvl]
        m["visible"] = request.POST.get(f"vis_{lvl}") == "1"
        try:
            m["order"] = int(request.POST.get(f"order_{lvl}", m["order"]))
        except (TypeError, ValueError):
            pass
    # Country + county levels each carry a per-country selection list.
    for lvl, post_key in (("country", "country_iso"), ("county", "county_iso")):
        m = config[lvl]
        m["visible"] = request.POST.get(f"vis_{lvl}") == "1"
        try:
            m["order"] = int(request.POST.get(f"order_{lvl}", m["order"]))
        except (TypeError, ValueError):
            pass
        selected = request.POST.getlist(post_key)
        m["countries"] = selected if selected else None
    dashboard_maps_mod.save_config(list(config.values()))
    request.session["dashboard_msg"] = {"ok": True, "text": _("Dashboard map settings saved.")}
    return _redirect_tab("dashboard")


def save_dashboard_stats(request):
    """POST — toggle OC/AL inclusion in the Statistics tab."""
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    from django.utils.translation import gettext as _
    UserPreference.set("stats_include_oc", request.POST.get("stats_include_oc") == "1")
    UserPreference.set("stats_include_al", request.POST.get("stats_include_al") == "1")
    request.session["dashboard_stats_msg"] = {
        "ok": True, "text": _("Statistics inclusion settings saved."),
    }
    return _redirect_tab("dashboard")


def download_boundary(request):
    """POST iso2=XX — download that country's region AND county boundary in
    the background, so the user only has to click Download once per country.

    County-level data isn't always available (some small countries/territories
    have no ADM2 in geoBoundaries) — that failure is swallowed so the region
    download still counts as a success.

    ``other_iso2`` is a second, distinct field name for the "download a
    country with no finds yet" dropdown — it shares the big dashboard-maps
    <form> with each row's own ``name="iso2"`` Download button, so it can't
    reuse that name without both values riding along on every submit.
    """
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    import requests
    from django.utils.translation import gettext as _
    from geocaches.tasks import submit_task
    from preferences.services import boundaries

    iso2 = (request.POST.get("iso2") or request.POST.get("other_iso2") or "").upper().strip()
    if len(iso2) != 2 or not iso2.isalpha():
        request.session["dashboard_msg"] = {"ok": False, "text": _("Invalid country code.")}
        return _redirect_tab("dashboard")

    def _task(*, task_info):
        result = {"region": boundaries.download_boundary(iso2)}
        try:
            result["county"] = boundaries.download_boundary(
                iso2, level=boundaries.effective_county_level(iso2)
            )
        except (requests.RequestException, ValueError, RuntimeError, OSError) as exc:
            result["county"] = f"error: {exc}"
        return result

    submit_task(f"Download boundary {iso2}", _task)
    request.session["dashboard_msg"] = {
        "ok": True,
        "text": _("Download started for %(iso2)s. Refresh this page to see updated status.") % {"iso2": iso2},
    }
    return _redirect_tab("dashboard")


def update_all_boundaries(request):
    """POST — re-download all boundaries in the manifest, in the background."""
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    from django.utils.translation import gettext as _
    from geocaches.tasks import submit_task
    from preferences.services import boundaries

    def _task(*, task_info):
        return boundaries.update_all()

    submit_task("Update all boundaries", _task)
    request.session["dashboard_msg"] = {
        "ok": True,
        "text": _("Update started for all downloaded boundaries. Refresh to see updated status."),
    }
    return _redirect_tab("dashboard")


def enrich_locations_offline(request):
    """Trigger polygon-based location enrichment (no internet) as a background
    task.  Fills empty country / state / county fields; the ``override``
    checkbox also rewrites existing values when the polygon disagrees."""
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    from django.utils.translation import gettext as _
    from geocaches.services import offline_enrich
    from geocaches.tasks import submit_task

    override = request.POST.get("override") == "1"

    def _task(*, task_info):
        def report(done, total):
            task_info.total = total
            task_info.completed = done
        return offline_enrich.enrich_all(override=override, progress=report)

    submit_task("Offline location enrichment", _task)
    request.session["enrich_offline_msg"] = {
        "ok": True,
        "text": _("Offline location enrichment started in the background. "
                  "Refresh this page in a moment to see how many caches were updated."),
    }
    return _redirect_tab("enrichment")
