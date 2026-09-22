"""OpenCaching profile-page automation — drives ``myprofile.php`` on any OC
node to read or write the user's notification settings.

The OC profile form mixes the notification fields (``notifyRadius``,
``notifyOconly``, reference coords) with unrelated profile data (first/last
name, country, mailing prefs).  Every save must therefore round-trip the
*entire* form so we don't accidentally clear other fields — the public API
intentionally only exposes the notification slice.
"""
from __future__ import annotations

import logging
import math
import re
from typing import Any

import requests
from bs4 import BeautifulSoup, Tag

from accounts.oc_web_session import base_url, get_session, reset_session

logger = logging.getLogger("geocaches.notify")


# Only opencaching.de itself runs the German fork's profile UI
# (myprofile.php with notifyRadius / notifyOconly).  The .nl / .pl / .uk /
# .us nodes all use the OC.us-style codebase: /MyNeighbourhood/config/<id>
# with multi-nbh + per-nbh AJAX toggles on /UserProfile/notifySettings.
DE_FAMILY = frozenset({"oc_de"})
US_FAMILY = frozenset({"oc_us", "oc_nl", "oc_pl", "oc_uk"})
SUPPORTED_PLATFORMS = DE_FAMILY | US_FAMILY


def _de_profile_url(platform: str) -> str:
    return f"{base_url(platform)}/myprofile.php"


def _check_login_redirect(html: str, url: str, platform: str) -> None:
    if "login.php" in url:
        reset_session(platform)
        raise RuntimeError(
            f"{platform} web session expired while loading the profile page — "
            "session has been reset, please try again."
        )


def _decimal_from_mindec(degs: str, mins: str, sign: str) -> float:
    try:
        d = abs(int(degs))
        m = float(mins or "0")
    except (TypeError, ValueError):
        return 0.0
    val = d + m / 60.0
    return -val if sign in ("S", "W", "-1") else val


def _decimal_to_mindec(dec: float) -> tuple[int, str, int]:
    """Decimal degrees → (degs, mins-string, sign).  sign: +1 N/E, −1 S/W."""
    sign = 1 if dec >= 0 else -1
    absd = abs(dec)
    degs = int(math.floor(absd))
    mins = (absd - degs) * 60.0
    return degs, f"{mins:.3f}", sign


def _fetch_edit_form(platform: str) -> tuple[BeautifulSoup, Tag]:
    """GET the profile page, POST action=change, return the resulting edit form."""
    session = get_session(platform)
    url = _de_profile_url(platform)

    r = session.post(
        url,
        data={"action": "change", "showAllCountries": "", "change": "Ändern"},
        timeout=30,
    )
    r.raise_for_status()
    _check_login_redirect(r.text, r.url, platform)

    soup = BeautifulSoup(r.text, "html.parser")
    for form in soup.find_all("form"):
        if not isinstance(form, Tag):
            continue
        if "myprofile.php" not in (form.get("action") or ""):
            continue
        names = {i.get("name", "") for i in form.find_all("input") if isinstance(i, Tag)}
        if "notifyRadius" in names:
            return soup, form
    raise RuntimeError(
        f"{platform} edit form did not contain expected fields — site may have changed."
    )


def _form_state(form: Tag) -> dict[str, str]:
    """Capture every input/select/textarea value from the edit form.

    Checkboxes that are *not* checked are deliberately omitted, matching what
    a browser would submit.  Submit buttons are skipped.
    """
    state: dict[str, str] = {}
    for inp in form.find_all("input"):
        if not isinstance(inp, Tag):
            continue
        name = inp.get("name", "")
        if not name:
            continue
        itype = (inp.get("type") or "text").lower()
        if itype == "submit":
            continue
        if itype == "checkbox":
            if inp.has_attr("checked"):
                state[name] = inp.get("value", "1") or "1"
            continue
        state[name] = inp.get("value", "") or ""
    for sel in form.find_all("select"):
        if not isinstance(sel, Tag):
            continue
        name = sel.get("name", "")
        if not name:
            continue
        val = ""
        for opt in sel.find_all("option"):
            if isinstance(opt, Tag) and opt.has_attr("selected"):
                val = opt.get("value", "")
                break
        state[name] = val
    for ta in form.find_all("textarea"):
        if not isinstance(ta, Tag):
            continue
        name = ta.get("name", "")
        if name:
            state[name] = (ta.string or "")
    return state


def _fetch_de(platform: str) -> dict[str, Any]:
    """Return the .de-family notification settings."""
    _soup, form = _fetch_edit_form(platform)
    state = _form_state(form)

    lat = _decimal_from_mindec(state.get("coordLat", "0"),
                               state.get("coordLatMin", "0"),
                               state.get("coordNS", "N"))
    lon = _decimal_from_mindec(state.get("coordLon", "0"),
                               state.get("coordLonMin", "0"),
                               state.get("coordEW", "E"))

    try:
        radius = int(re.sub(r"[^\d]", "", state.get("notifyRadius", "0")) or "0")
    except ValueError:
        radius = 0
    oconly = "notifyOconly" in state  # checkbox present == checked

    return {
        "platform": platform,
        "latitude": lat,
        "longitude": lon,
        "radius_km": radius,
        "notify_oconly": oconly,
        # .de-family doesn't have these:
        "notify_logs": False,
        "frequency": "daily",
    }


def _save_de(
    platform: str,
    *,
    latitude: float,
    longitude: float,
    radius_km: int,
    notify_oconly: bool,
) -> None:
    """Patch the four .de-family notification fields and POST the whole form.

    All other profile fields (name, country, mailing prefs, ...) are forwarded
    verbatim from the freshly-fetched edit form.
    """
    _soup, form = _fetch_edit_form(platform)
    state = _form_state(form)

    lat_d, lat_m, lat_sign = _decimal_to_mindec(latitude)
    lon_d, lon_m, lon_sign = _decimal_to_mindec(longitude)
    state["coordLat"] = str(lat_d)
    state["coordLatMin"] = lat_m
    state["coordNS"] = "N" if lat_sign >= 0 else "S"
    state["coordLon"] = f"{lon_d:03d}"
    state["coordLonMin"] = lon_m
    state["coordEW"] = "E" if lon_sign >= 0 else "W"
    state["notifyRadius"] = str(radius_km)
    if notify_oconly:
        state["notifyOconly"] = "1"
    else:
        state.pop("notifyOconly", None)
    state["action"] = "change"
    state["save"] = "Bestätigen"

    session = get_session(platform)
    url = _de_profile_url(platform)
    logger.debug("OC notify save POST (de): platform=%s radius=%s oconly=%s coords=%s,%s",
                 platform, radius_km, notify_oconly, latitude, longitude)
    r = session.post(url, data=state, timeout=30)
    r.raise_for_status()
    _check_login_redirect(r.text, r.url, platform)

    # Verify by re-reading.
    after = _fetch_de(platform)
    if after["radius_km"] != radius_km or after["notify_oconly"] != notify_oconly:
        raise RuntimeError(
            f"{platform} profile save did not stick: "
            f"radius={after['radius_km']!r} oconly={after['notify_oconly']!r}"
        )


# ---------------------------------------------------------------------------
# opencaching.us — different fork, different mechanics
#
#   * Reference coords + radius live on /MyNeighbourhood/config/0 and are
#     saved with POST /MyNeighbourhood/save/0 (lon, lat, radius, style, …).
#   * Notification toggles + frequency live on /UserProfile/notifySettings;
#     the page auto-saves via three AJAX endpoints:
#       GET  /UserProfile/ajaxSetNotifyCaches/<0|1>
#       GET  /UserProfile/ajaxSetNotifyLogs/<0|1>
#       POST /UserProfile/ajaxSetNotifySettings (watchmail_mode/day/hour)
#   * Toggle state isn't in form inputs — it's encoded in which of two
#     <img id="notifyXxxOn|Off"> elements carries the ``no-display`` class.
# ---------------------------------------------------------------------------

_US_INTERVAL_TO_FREQ = {"0": "daily", "1": "hourly", "2": "weekly"}
_US_FREQ_TO_INTERVAL = {v: k for k, v in _US_INTERVAL_TO_FREQ.items()}


def _us_get(platform: str, path: str) -> requests.Response:
    session = get_session(platform)
    url = f"{base_url(platform)}{path}"
    r = session.get(url, timeout=30)
    r.raise_for_status()
    _check_login_redirect(r.text, r.url, platform)
    return r


def _us_post(platform: str, path: str, data: dict[str, str]) -> requests.Response:
    session = get_session(platform)
    url = f"{base_url(platform)}{path}"
    r = session.post(url, data=data, timeout=30, headers={"Referer": url})
    r.raise_for_status()
    _check_login_redirect(r.text, r.url, platform)
    return r


def _us_is_toggle_on(soup: BeautifulSoup, on_id: str) -> bool:
    """The ON <img id="..."> has the ``no-display`` class when the toggle is off."""
    el = soup.find(id=on_id)
    if not isinstance(el, Tag):
        return False
    classes = (el.get("class") or [])
    return "no-display" not in classes


def _parse_us_nbh_config(html: str) -> dict[str, Any]:
    """Parse one /MyNeighbourhood/config/<id> page → {name, lat, lon, radius_km}.

    Name is only present on additional (id>=1) configs.  Coords come from the
    hidden form inputs; if those are empty (e.g. on the create form), fall
    back to the page's L.marker / L.circle JS bootstrap.
    """
    soup = BeautifulSoup(html, "html.parser")
    name = ""
    form_lat = form_lon = form_radius = ""
    form = soup.find("form", action=re.compile(r"/MyNeighbourhood/save/"))
    if isinstance(form, Tag):
        for inp in form.find_all("input"):
            if not isinstance(inp, Tag):
                continue
            n = inp.get("name", "")
            v = inp.get("value", "") or ""
            if n == "name":
                name = v
            elif n == "lat":
                form_lat = v
            elif n == "lon":
                form_lon = v
            elif n == "radius":
                form_radius = v

    if not form_lat:
        m = re.search(r"L\.marker\s*\(\s*\[\s*([-\d.]+)\s*,\s*([-\d.]+)\s*\]", html)
        if m:
            form_lat, form_lon = m.group(1), m.group(2)
    if not form_radius:
        m = re.search(r"L\.circle\s*\(\s*\[\s*[-\d.]+\s*,\s*[-\d.]+\s*\]\s*,\s*\{?\s*radius\s*:\s*(\d+)", html)
        if m:
            form_radius = str(round(int(m.group(1)) / 1000.0))

    try:
        lat = float(form_lat) if form_lat else 0.0
        lon = float(form_lon) if form_lon else 0.0
        radius_km = int(float(form_radius)) if form_radius else 0
    except ValueError:
        lat, lon, radius_km = 0.0, 0.0, 0
    return {"name": name, "latitude": lat, "longitude": lon, "radius_km": radius_km}


def _list_us_neighbourhoods(platform: str) -> tuple[list[str], dict[str, bool], dict[str, Any]]:
    """Read /UserProfile/notifySettings → (additional_ids, enabled_by_id, globals).

    ``additional_ids`` is the sorted list of nbh ids >= 1 that the user has.
    ``enabled_by_id`` maps each additional id → bool (its per-nbh toggle).
    ``globals`` carries the master caches/logs toggles + frequency.
    """
    r = _us_get(platform, "/UserProfile/notifySettings")
    soup = BeautifulSoup(r.text, "html.parser")

    caches_on = _us_is_toggle_on(soup, "notifyCachesOn")
    logs_on = _us_is_toggle_on(soup, "notifyLogsOn")
    m = re.search(r'\$\("#intervalSelect"\)\.val\("(\d+)"\)', r.text)
    interval = m.group(1) if m else "0"

    add_ids = sorted({m.group(1) for m in re.finditer(
        r'notifyNbh(?:On|Off)-(\d+)', r.text)}, key=int)
    enabled_by_id: dict[str, bool] = {}
    for nid in add_ids:
        enabled_by_id[nid] = _us_is_toggle_on(soup, f"notifyNbhOn-{nid}")

    return add_ids, enabled_by_id, {
        "caches_enabled": caches_on,
        "notify_logs": logs_on,
        "frequency": _US_INTERVAL_TO_FREQ.get(interval, "daily"),
    }


def _fetch_us(platform: str) -> list[dict[str, Any]]:
    """Return all .us neighbourhoods, default + additional.

    Each dict has: server_id, name, latitude, longitude, radius_km, enabled,
    plus the global ``notify_logs`` and ``frequency`` (denormalised onto
    every row — they're not per-nbh on the server).
    """
    add_ids, enabled_by_id, globals_ = _list_us_neighbourhoods(platform)

    rows: list[dict[str, Any]] = []
    # nbh 0 = default. Always present (or, if the user has deleted it, the
    # server still serves /config/0 with empty coords).
    r0 = _us_get(platform, "/MyNeighbourhood/config/0")
    cfg = _parse_us_nbh_config(r0.text)
    rows.append({
        "platform": platform,
        "server_id": "0",
        "name": "",
        "latitude": cfg["latitude"],
        "longitude": cfg["longitude"],
        "radius_km": cfg["radius_km"],
        "enabled": globals_["caches_enabled"],
        "notify_oconly": False,
        "notify_logs": globals_["notify_logs"],
        "frequency": globals_["frequency"],
    })

    for nid in add_ids:
        r = _us_get(platform, f"/MyNeighbourhood/config/{nid}")
        cfg = _parse_us_nbh_config(r.text)
        rows.append({
            "platform": platform,
            "server_id": nid,
            "name": cfg["name"],
            "latitude": cfg["latitude"],
            "longitude": cfg["longitude"],
            "radius_km": cfg["radius_km"],
            # Mirror the master state — GCForge exposes a single enable per
            # site; the server's per-nbh toggle is normalised to ON on push.
            "enabled": globals_["caches_enabled"],
            "notify_oconly": False,
            "notify_logs": globals_["notify_logs"],
            "frequency": globals_["frequency"],
        })
    return rows


def _us_enable_all_additionals(platform: str) -> None:
    """Force every additional neighbourhood's per-nbh toggle ON.

    GCForge exposes a single master enable per site; per-nbh toggles are
    invisible to the user.  Normalising them all to ON makes the master
    toggle the only switch that actually changes the user's notification
    behaviour.
    """
    add_ids, _, _ = _list_us_neighbourhoods(platform)
    for nid in add_ids:
        _us_post(platform, "/UserProfile/ajaxSetNeighbourhoodNotify", {
            "nbh": nid,
            "state": "1",
        })


def _save_us_one(
    platform: str,
    *,
    server_id: str,
    name: str,
    latitude: float,
    longitude: float,
    radius_km: int,
    enabled: bool,
) -> None:
    """Save one .us neighbourhood (coords + radius) by id.

    ``server_id == "0"`` saves the default neighbourhood AND flips the master
    enable toggle (``ajaxSetNotifyCaches``).  ``server_id >= "1"`` only saves
    the row's coords/name/radius — the ``enabled`` parameter is ignored;
    per-nbh state is always forced ON so the master remains the sole switch.
    """
    if server_id == "0":
        # Default nbh: needs style + caches-perpage (otherwise the server would
        # blank them).  Pull current values first to round-trip cleanly.
        r = _us_get(platform, "/MyNeighbourhood/config/0")
        soup = BeautifulSoup(r.text, "html.parser")
        form = soup.find("form", action=re.compile(r"/MyNeighbourhood/save/0"))
        style = "full"
        caches_perpage = "5"
        if isinstance(form, Tag):
            sel_style = form.find("input", attrs={"name": "style", "checked": True})
            if isinstance(sel_style, Tag):
                style = sel_style.get("value", style) or style
            perpage = form.find("input", attrs={"name": "caches-perpage"})
            if isinstance(perpage, Tag):
                caches_perpage = perpage.get("value", caches_perpage) or caches_perpage
        _us_post(platform, "/MyNeighbourhood/save/0", {
            "style": style,
            "caches-perpage": caches_perpage,
            "lat": f"{latitude}",
            "lon": f"{longitude}",
            "radius": str(radius_km),
        })
        # The ajaxSetNotifyCaches/<state> URL takes the *new* state directly
        # (verified by the JS dispatch in notifySettings.php).
        _us_get(platform, f"/UserProfile/ajaxSetNotifyCaches/{1 if enabled else 0}")
        # Normalise every additional neighbourhood to ON so the master is the
        # only switch that actually gates notifications.
        _us_enable_all_additionals(platform)
        logger.debug("OC.us nbh 0 saved: coords=%s,%s radius=%s enabled=%s",
                     latitude, longitude, radius_km, enabled)
        return

    # Additional nbh: just name/lat/lon/radius. The per-nbh toggle is always
    # set to ON; we don't expose it.
    _us_post(platform, f"/MyNeighbourhood/save/{server_id}", {
        "name": name,
        "lat": f"{latitude}",
        "lon": f"{longitude}",
        "radius": str(radius_km),
    })
    _us_post(platform, "/UserProfile/ajaxSetNeighbourhoodNotify", {
        "nbh": server_id,
        "state": "1",
    })
    logger.debug("OC.us nbh %s saved: name=%r coords=%s,%s radius=%s (per-nbh forced ON)",
                 server_id, name, latitude, longitude, radius_km)


def _save_us_globals(platform: str, *, notify_logs: bool, frequency: str) -> None:
    """Save the platform-global toggles (logs) and frequency."""
    _us_get(platform, f"/UserProfile/ajaxSetNotifyLogs/{0 if notify_logs else 1}")
    interval = _US_FREQ_TO_INTERVAL.get(frequency, "0")
    _us_post(platform, "/UserProfile/ajaxSetNotifySettings", {
        "watchmail_mode": interval,
        "watchmail_day": "1",
        "watchmail_hour": "0",
    })
    logger.debug("OC.us globals saved: notify_logs=%s frequency=%s",
                 notify_logs, frequency)


def _create_us_nbh(platform: str, *, name: str, latitude: float, longitude: float,
                    radius_km: int) -> str:
    """Create a new additional neighbourhood; return its server-assigned id."""
    # POST to save/-1 — the server assigns the new id and redirects.
    pre_ids, _, _ = _list_us_neighbourhoods(platform)
    _us_post(platform, "/MyNeighbourhood/save/-1", {
        "name": name,
        "lat": f"{latitude}",
        "lon": f"{longitude}",
        "radius": str(radius_km),
    })
    post_ids, _, _ = _list_us_neighbourhoods(platform)
    new = sorted(set(post_ids) - set(pre_ids), key=int)
    if not new:
        raise RuntimeError(
            f"Created OC.us neighbourhood {name!r} but no new id appeared in the listing."
        )
    new_id = new[0]
    # Force the new nbh's per-nbh toggle ON so it's covered by the master.
    _us_post(platform, "/UserProfile/ajaxSetNeighbourhoodNotify", {
        "nbh": new_id,
        "state": "1",
    })
    logger.info("OC.us nbh created: id=%s name=%r", new_id, name)
    return new_id


def _delete_us_nbh(platform: str, server_id: str) -> None:
    """Delete an additional neighbourhood by id."""
    if not server_id.isdigit() or int(server_id) < 1:
        raise RuntimeError(
            f"Refusing to delete OC.us nbh with non-additional server_id={server_id!r} "
            "(only id >= 1 may be deleted)."
        )
    _us_get(platform, f"/MyNeighbourhood/delete/{server_id}")
    logger.info("OC.us nbh deleted: id=%s", server_id)


# ---------------------------------------------------------------------------
# Public dispatcher
# ---------------------------------------------------------------------------

def fetch_all(platform: str) -> list[dict[str, Any]]:
    """Return all notification rows for ``platform`` (one for .de, N for .us)."""
    if platform in DE_FAMILY:
        return [_fetch_de(platform)]
    if platform in US_FAMILY:
        return _fetch_us(platform)
    raise RuntimeError(
        f"{platform} notification automation is not supported "
        f"(only {', '.join(sorted(SUPPORTED_PLATFORMS))})."
    )


def _check_radius(radius_km: int) -> None:
    if radius_km < 0 or radius_km > 150:
        raise ValueError(f"OC notification radius must be 0..150 km (got {radius_km})")


def save_row(
    platform: str,
    *,
    server_id: str = "",
    name: str = "",
    latitude: float,
    longitude: float,
    radius_km: int,
    enabled: bool = True,
    notify_oconly: bool = False,
) -> None:
    """Save a single notification row to ``platform``.

    For .de family ``server_id`` is ignored (one row per platform).
    For .us, ``server_id`` must be the existing nbh id (use ``create_nbh`` for
    a new one).
    """
    _check_radius(radius_km)
    if platform in DE_FAMILY:
        return _save_de(
            platform,
            latitude=latitude, longitude=longitude,
            radius_km=radius_km, notify_oconly=notify_oconly,
        )
    if platform in US_FAMILY:
        return _save_us_one(
            platform,
            server_id=server_id, name=name,
            latitude=latitude, longitude=longitude,
            radius_km=radius_km, enabled=enabled,
        )
    raise RuntimeError(
        f"{platform} notification automation is not supported "
        f"(only {', '.join(sorted(SUPPORTED_PLATFORMS))})."
    )


def save_globals(platform: str, *, notify_logs: bool, frequency: str) -> None:
    """Save platform-global toggles (currently .us-only: notify_logs + frequency)."""
    if platform in US_FAMILY:
        return _save_us_globals(platform, notify_logs=notify_logs, frequency=frequency)
    # .de family has no globals beyond what save_row covers.
    return None


def create_nbh(platform: str, *, name: str, latitude: float, longitude: float,
                radius_km: int) -> str:
    """Create a new neighbourhood on ``platform`` and return its new server_id.

    Only meaningful on .us; .de family supports a single rule.
    """
    _check_radius(radius_km)
    if platform in US_FAMILY:
        return _create_us_nbh(
            platform,
            name=name, latitude=latitude, longitude=longitude,
            radius_km=radius_km,
        )
    raise RuntimeError(
        f"{platform} doesn't support multiple neighbourhoods."
    )


def delete_nbh(platform: str, server_id: str) -> None:
    """Delete an additional neighbourhood on ``platform``."""
    if platform in US_FAMILY:
        return _delete_us_nbh(platform, server_id)
    raise RuntimeError(
        f"{platform} doesn't support deleting neighbourhoods."
    )
