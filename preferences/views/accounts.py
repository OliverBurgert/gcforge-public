from django.http import HttpResponseNotAllowed
from django.shortcuts import render

from preferences.models import LOG_TEMPLATE_SCOPES, LogTemplate, UserPreference
from ._helpers import _redirect_tab


def save_gc_username(request):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    UserPreference.set("gc_username", request.POST.get("gc_username", "").strip())
    return _redirect_tab("accounts")


def fetch_gc_public_guid(request):
    """POST: scrape geocaching.com/find/default.aspx to find the user's public GUID."""
    import http.cookiejar
    import re
    import urllib.parse
    import urllib.request
    from django.http import JsonResponse

    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    username = UserPreference.get("gc_username", "").strip()
    if not username:
        return JsonResponse({"error": "No geocaching.com username configured — set it above first."}, status=400)

    _FIND_URL = "https://www.geocaching.com/find/default.aspx"
    _UA = "Mozilla/5.0 (compatible; GCForge)"

    cookie_jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cookie_jar))

    # Step 1: GET the page to harvest ASP.NET hidden fields
    try:
        req = urllib.request.Request(_FIND_URL, headers={"User-Agent": _UA})
        with opener.open(req, timeout=15) as resp:
            html = resp.read().decode("utf-8", errors="replace")
    except Exception as exc:
        return JsonResponse({"error": f"Could not load find page: {exc}"}, status=502)

    def _hidden(name):
        m = re.search(
            r'<input[^>]+name=["\']' + re.escape(name) + r'["\'][^>]+value=["\']([^"\']*)["\']',
            html, re.IGNORECASE,
        )
        return m.group(1) if m else ""

    # Step 2: POST the search form (ASP.NET WebForms button submit)
    post_data = urllib.parse.urlencode({
        "__EVENTTARGET": "",
        "__EVENTARGUMENT": "",
        "__VIEWSTATE": _hidden("__VIEWSTATE"),
        "__VIEWSTATEGENERATOR": _hidden("__VIEWSTATEGENERATOR"),
        "__EVENTVALIDATION": _hidden("__EVENTVALIDATION"),
        "ctl00$ContentBody$txtFindUser": username,
        "ctl00$ContentBody$btnFindUser": "Go",
    }).encode()

    req2 = urllib.request.Request(
        _FIND_URL,
        data=post_data,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": _UA,
            "Referer": _FIND_URL,
        },
    )
    try:
        with opener.open(req2, timeout=15) as resp:
            final_url = resp.url
            result_html = resp.read().decode("utf-8", errors="replace")
    except Exception as exc:
        return JsonResponse({"error": f"Search request failed: {exc}"}, status=502)

    # Try the redirect URL first (most common — GC redirects to the profile page)
    m = re.search(r"[?&]guid=([\w-]{36})", final_url, re.IGNORECASE)
    if not m:
        # Fall back: scan the response HTML for a profile link containing the GUID
        m = re.search(r"[?&]guid=([\w-]{36})", result_html, re.IGNORECASE)
    if not m:
        return JsonResponse(
            {"error": f"Could not find GUID for '{username}' — check the username is correct."},
            status=404,
        )

    guid = m.group(1)
    UserPreference.set("gc_public_guid", guid)
    return JsonResponse({"guid": guid})


def save_al_prefs(request):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    UserPreference.set("gc_public_guid", request.POST.get("gc_public_guid", "").strip())
    return _redirect_tab("accounts")


def _fetch_total_platform_finds() -> tuple[int, bool, list[str]]:
    """Fetch find counts from every configured platform and sum them.

    Returns ``(total, ok, errors)`` where ``ok`` is True iff at least one
    platform yielded a numeric value. ``errors`` lists per-platform failures
    so the UI can surface partial-success.
    """
    from accounts.models import UserAccount
    from accounts import gc_client as _gc, keyring_util, okapi_client

    errors: list[str] = []
    total = 0
    have_any = False

    # GC
    if UserAccount.objects.filter(platform="gc").exists() and _gc.has_api_tokens():
        try:
            from gcprivate.gc_client import GCClient
            client = GCClient()
            raw = client._api.get("/users/me", fields="findCount")
            n = raw.get("findCount")
            if isinstance(n, int):
                total += n
                have_any = True
        except Exception as exc:
            errors.append(f"gc: {exc}")

    # OC platforms
    for acct in UserAccount.objects.filter(platform__startswith="oc_"):
        try:
            node_url = okapi_client.get_node_url(acct.platform)
            if not node_url:
                continue
            custom_key = UserPreference.get(f"okapi_consumer_key_{acct.platform}", "")
            custom_secret = UserPreference.get(f"okapi_consumer_secret_{acct.platform}", "")
            creds = okapi_client.get_consumer_credentials(acct.platform, custom_key, custom_secret)
            if not creds:
                continue
            consumer_key, consumer_secret = creds
            oauth_creds = keyring_util.get_oauth_token(acct.platform, acct.user_id)
            url = f"{node_url}/okapi/services/users/user"
            params = {"fields": "caches_found"}
            if oauth_creds:
                ot, ots = oauth_creds
                raw = okapi_client._get_level3(url, params, consumer_key, consumer_secret, ot, ots)
            else:
                params["user_uuid"] = acct.user_id
                raw = okapi_client._get_level1(url, params, consumer_key)
            n = raw.get("caches_found")
            if isinstance(n, int):
                total += n
                have_any = True
        except Exception as exc:
            errors.append(f"{acct.platform}: {exc}")

    if have_any:
        UserPreference.set("cached_total_finds", total)
    return total, have_any, errors


def refresh_total_finds(request):
    """Refresh the cached total of platform finds. POST → JSON."""
    from django.http import JsonResponse
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    total, ok, errors = _fetch_total_platform_finds()
    return JsonResponse({"total": total, "ok": ok, "errors": errors})


def add_log_template(request):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    name = request.POST.get("template_name", "").strip()
    body = request.POST.get("template_body", "")
    scope = request.POST.get("template_scope", "any").strip() or "any"
    is_default = request.POST.get("template_default") == "1"
    valid_scopes = {s for s, _ in LOG_TEMPLATE_SCOPES}
    if scope not in valid_scopes:
        scope = "any"
    if name and body:
        LogTemplate.objects.update_or_create(
            name=name,
            defaults={"body": body, "scope": scope, "is_default": is_default},
        )
    return _redirect_tab("logging")


def delete_log_template(request):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    LogTemplate.objects.filter(id=request.POST.get("template_id")).delete()
    return _redirect_tab("logging")


def save_logging_prefs(request):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    UserPreference.set("log_image_strip_exif", "log_image_strip_exif" in request.POST)
    try:
        log_image_max_px = int(request.POST.get("log_image_max_px", 1024))
        if log_image_max_px not in (512, 1024, 1600, 2048, 0):
            log_image_max_px = 1024
    except (ValueError, TypeError):
        log_image_max_px = 1024
    UserPreference.set("log_image_max_px", log_image_max_px)
    UserPreference.set("auto_fetch_tb_on_log", "auto_fetch_tb_on_log" in request.POST)
    return _redirect_tab("logging")


def log_view(request):
    """Display the last N lines from all log files (current + rotated), newest first."""
    import time as _time
    from pathlib import Path
    from django.conf import settings as django_settings

    log_dir = Path(django_settings.LOG_DIR)
    log_base = log_dir / "gcforge.log"

    # Collect all rotated files: gcforge.log, gcforge.log.1, ..., gcforge.log.4
    candidates = [log_base] + [Path(f"{log_base}.{i}") for i in range(1, 6)]
    lines = []
    for path in candidates:
        if path.exists():
            try:
                file_lines = path.read_text(encoding="utf-8").splitlines()
                file_lines.reverse()
                lines.extend(file_lines)
            except OSError:
                pass

    lines = lines[:500]

    until = request.GET.get("until", "")
    auto_refresh = False
    if until:
        try:
            auto_refresh = int(until) > _time.time()
        except ValueError:
            pass

    return render(request, "preferences/log.html", {
        "lines": lines,
        "log_path": log_base,
        "auto_refresh": auto_refresh,
        "until": until,
    })
