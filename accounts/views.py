from django.http import HttpResponse, HttpResponseNotAllowed
from django.shortcuts import redirect
from django.urls import reverse

from . import gc_client, keyring_util, okapi_client
from .models import UserAccount
from preferences.models import UserPreference


def _build_platform_keys_context() -> list[dict]:
    """Return per-platform OKAPI key info for the Platforms settings tab."""
    rows = []
    platform_labels = dict(UserAccount.PLATFORM_CHOICES)
    for platform, node_url in okapi_client._OC_NODES.items():
        custom_key    = UserPreference.get(f"okapi_consumer_key_{platform}",    "")
        custom_secret = UserPreference.get(f"okapi_consumer_secret_{platform}", "")
        bundled       = okapi_client._BUNDLED_KEYS.get(platform)
        rows.append({
            "platform":       platform,
            "label":          platform_labels.get(platform, platform),
            "node_url":       node_url,
            "signup_url":     f"{node_url}/okapi/signup.html",
            "custom_key":     custom_key,
            "custom_secret":  custom_secret,
            "has_bundled":    bundled is not None,
            "effective_key":  custom_key or (bundled[0] if bundled else ""),
        })
    return rows


def save_platform_keys(request):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    for platform in okapi_client._OC_NODES:
        key    = request.POST.get(f"okapi_key_{platform}",    "").strip()
        secret = request.POST.get(f"okapi_secret_{platform}", "").strip()
        if key:
            UserPreference.set(f"okapi_consumer_key_{platform}",    key)
            UserPreference.set(f"okapi_consumer_secret_{platform}", secret)
        else:
            # Empty = revert to bundled default
            UserPreference.set(f"okapi_consumer_key_{platform}",    "")
            UserPreference.set(f"okapi_consumer_secret_{platform}", "")
    return _redirect_tab("platforms")


def _redirect_tab(tab: str):
    return redirect(reverse("preferences:settings") + f"#{tab}")


def _start_oauth_for_account(request, acct):
    """Start the OAuth 1.0a authorization flow for acct. Returns a redirect response."""
    custom_key    = UserPreference.get(f"okapi_consumer_key_{acct.platform}", "")
    custom_secret = UserPreference.get(f"okapi_consumer_secret_{acct.platform}", "")
    creds = okapi_client.get_consumer_credentials(acct.platform, custom_key, custom_secret)
    if not creds:
        request.session["account_msg"] = {
            "ok": False,
            "text": f"No consumer key for {acct.get_platform_display()}. Add one in the Platforms tab.",
        }
        return _redirect_tab("accounts")
    consumer_key, consumer_secret = creds
    callback_url = request.build_absolute_uri(reverse("accounts:oauth_callback"))
    try:
        req_token, req_token_secret = okapi_client.get_request_token(
            acct.platform, consumer_key, consumer_secret, callback_url
        )
    except ValueError as exc:
        request.session["account_msg"] = {"ok": False, "text": f"OAuth error: {exc}"}
        return _redirect_tab("accounts")
    request.session[f"oauth_{req_token}"] = {
        "platform":         acct.platform,
        "acct_id":          str(acct.pk),
        "consumer_key":     consumer_key,
        "consumer_secret":  consumer_secret,
        "req_token_secret": req_token_secret,
    }
    return redirect(okapi_client.get_authorize_url(acct.platform, req_token))


def _build_user_accounts_context() -> list[dict]:
    """Annotate each UserAccount with live keyring/token status."""
    gc_token_info = gc_client.get_api_token_info()  # cached once for all GC accounts
    rows = []
    for acct in UserAccount.objects.all():
        if acct.platform == "gc":
            has_api = gc_client.has_api_tokens()
            row = {
                "acct": acct,
                "has_password": bool(keyring_util.get_password(acct.platform, acct.username)),
                "has_oauth": False,
                "has_gc_api": has_api,
                "gc_api_verified": gc_client.is_gc_api_verified(),
                "gc_token_info": gc_token_info if has_api else None,
            }
        else:
            row = {
                "acct": acct,
                "has_password": bool(keyring_util.get_password(acct.platform, acct.username)),
                "has_oauth": keyring_util.has_oauth_token(acct.platform, acct.user_id),
            }
        rows.append(row)
    return rows


def add_account(request):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    platform = request.POST.get("acct_platform", "").strip()
    username = request.POST.get("acct_username", "").strip()
    user_id  = request.POST.get("acct_user_id",  "").strip()
    if platform and username:
        UserAccount.objects.update_or_create(
            platform=platform,
            user_id=user_id,
            defaults={
                "username":    username,
                "label":       request.POST.get("acct_label",       "").strip(),
                "profile_url": request.POST.get("acct_profile_url", "").strip(),
                "notes":       request.POST.get("acct_notes",       "").strip(),
            },
        )
    return _redirect_tab("accounts")


def edit_account(request):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    acct_id = request.POST.get("acct_id")
    acct = UserAccount.objects.filter(id=acct_id).first()
    if acct:
        acct.platform    = request.POST.get("acct_platform", acct.platform).strip()
        acct.user_id     = request.POST.get("acct_user_id",  acct.user_id).strip()
        acct.username    = request.POST.get("acct_username", acct.username).strip()
        acct.label       = request.POST.get("acct_label",    acct.label).strip()
        acct.profile_url = request.POST.get("acct_profile_url", acct.profile_url).strip()
        acct.notes       = request.POST.get("acct_notes",    acct.notes).strip()
        acct.consumer_key       = request.POST.get("acct_consumer_key",       acct.consumer_key).strip()
        acct.save()
        new_password = request.POST.get("acct_new_password", "").strip()
        store_pw     = request.POST.get("acct_store_password") == "1"
        if new_password and store_pw:
            keyring_util.store_password(acct.platform, acct.username, new_password)
        if new_password and acct.platform != "gc":
            # Re-authorize: the password changed, so we kick off a fresh OAuth flow
            return _start_oauth_for_account(request, acct)
    return _redirect_tab("accounts")


def delete_account(request):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    acct = UserAccount.objects.filter(id=request.POST.get("acct_id")).first()
    if acct:
        keyring_util.delete_password(acct.platform, acct.username)
        keyring_util.delete_oauth_token(acct.platform, acct.user_id)
        acct.delete()
    return _redirect_tab("accounts")


def set_default_account(request):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    acct_id = request.POST.get("acct_id")
    UserAccount.objects.all().update(is_default=False)
    UserAccount.objects.filter(id=acct_id).update(is_default=True)
    return _redirect_tab("accounts")


def login_account(request):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    platform     = request.POST.get("login_platform",      "").strip()
    custom_key   = request.POST.get("login_consumer_key",  "").strip()
    username     = request.POST.get("login_username",      "").strip()
    password     = request.POST.get("login_password",      "").strip()
    store_pw     = request.POST.get("login_store_password") == "1"

    if not (platform and username):
        request.session["account_msg"] = {
            "ok": False,
            "text": "Platform and username are required.",
        }
        return _redirect_tab("accounts")

    if platform == "gc":
        # GC: no OKAPI lookup — create account directly with manual data
        acct, created = UserAccount.objects.update_or_create(
            platform=platform,
            user_id=username,  # use username as user_id until API lookup is available
            defaults={
                "username":     username,
                "profile_url":  f"https://www.geocaching.com/p/{username}",
            },
        )
        if password and store_pw:
            keyring_util.store_password(platform, username, password)
        verb = "Created" if created else "Updated"
        request.session["account_msg"] = {
            "ok": True,
            "text": f"{verb} account for {username} on {acct.get_platform_display()}.",
        }
        return _redirect_tab("accounts")

    # OC platforms: resolve consumer key and look up user via OKAPI
    custom_secret = UserPreference.get(f"okapi_consumer_secret_{platform}", "")
    if not custom_key:
        custom_key = UserPreference.get(f"okapi_consumer_key_{platform}", "")
    creds = okapi_client.get_consumer_credentials(platform, custom_key, custom_secret)
    if not creds:
        request.session["account_msg"] = {
            "ok": False,
            "text": f"No consumer key available for {platform}. Add one in the Platforms tab.",
        }
        return _redirect_tab("accounts")
    consumer_key, _ = creds

    try:
        user_data = okapi_client.lookup_user_by_username(platform, consumer_key, username)
    except ValueError as exc:
        request.session["account_msg"] = {"ok": False, "text": str(exc)}
        return _redirect_tab("accounts")

    fetched_id       = user_data.get("uuid", "")
    fetched_username = user_data.get("username", username)
    fetched_profile  = user_data.get("profile_url", "")

    acct, created = UserAccount.objects.update_or_create(
        platform=platform,
        user_id=fetched_id,
        defaults={
            "username":     fetched_username,
            "profile_url":  fetched_profile,
            "consumer_key": consumer_key,
        },
    )

    if password and store_pw:
        keyring_util.store_password(platform, fetched_username, password)

    verb = "Created" if created else "Updated"
    request.session["account_msg"] = {
        "ok": True,
        "text": f"{verb} account for {fetched_username} on {acct.get_platform_display()}.",
    }
    return _redirect_tab("accounts")


def oauth_start(request):
    """Step 1: get an OKAPI request token and redirect the user to the OC authorization page."""
    if request.method != "POST":
        return redirect("preferences:settings")

    acct_id = request.POST.get("acct_id", "").strip()
    acct = UserAccount.objects.filter(id=acct_id).first()
    if not acct or acct.platform not in ("oc_de", "oc_pl", "oc_uk", "oc_nl", "oc_us"):
        request.session["account_msg"] = {"ok": False, "text": "Invalid account for OAuth."}
        return _redirect_tab("accounts")

    return _start_oauth_for_account(request, acct)


def oauth_callback(request):
    """Step 2: receive oauth_verifier from OC, exchange for access token, store on UserAccount."""
    oauth_token    = request.GET.get("oauth_token",    "")
    oauth_verifier = request.GET.get("oauth_verifier", "")

    if not oauth_token or not oauth_verifier:
        request.session["account_msg"] = {"ok": False, "text": "OAuth callback missing parameters."}
        return _redirect_tab("accounts")

    state = request.session.pop(f"oauth_{oauth_token}", None)
    if not state:
        request.session["account_msg"] = {"ok": False, "text": "OAuth session expired or not found."}
        return _redirect_tab("accounts")

    try:
        access_token, access_token_secret = okapi_client.get_access_token(
            state["platform"],
            state["consumer_key"],
            state["consumer_secret"],
            oauth_token,
            state["req_token_secret"],
            oauth_verifier,
        )
    except ValueError as exc:
        request.session["account_msg"] = {"ok": False, "text": f"OAuth error: {exc}"}
        return _redirect_tab("accounts")

    acct = UserAccount.objects.filter(id=state["acct_id"]).first()
    if acct:
        keyring_util.store_oauth_token(acct.platform, acct.user_id,
                                       access_token, access_token_secret)
        request.session["account_msg"] = {
            "ok": True,
            "text": f"Full access (Level 3) authorized for {acct.get_label()}.",
        }
    return _redirect_tab("accounts")


def account_validate_oauth(request):
    """HTMX endpoint: validate the stored OAuth token for an account via a Level 3 API call."""
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    acct = UserAccount.objects.filter(id=request.POST.get("acct_id")).first()
    if not acct:
        return HttpResponse('<span class="text-danger small">No token stored</span>')

    oauth_creds = keyring_util.get_oauth_token(acct.platform, acct.user_id)
    if not oauth_creds:
        return HttpResponse('<span class="text-danger small">No token stored</span>')
    oauth_token, oauth_token_secret = oauth_creds

    custom_key    = UserPreference.get(f"okapi_consumer_key_{acct.platform}", "")
    custom_secret = UserPreference.get(f"okapi_consumer_secret_{acct.platform}", "")
    creds = okapi_client.get_consumer_credentials(acct.platform, custom_key, custom_secret)
    if not creds:
        return HttpResponse('<span class="text-danger small">No consumer key</span>')
    consumer_key, consumer_secret = creds

    try:
        okapi_client.validate_oauth_token(
            acct.platform, consumer_key, consumer_secret,
            oauth_token, oauth_token_secret,
        )
        return HttpResponse('<span class="text-success small">&#10003; Valid</span>')
    except ValueError as exc:
        return HttpResponse(f'<span class="text-danger small">&#10007; {exc}</span>')


def account_validate_gc(request):
    """HTMX endpoint: validate GC API token by calling /users/me."""
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    try:
        from geocaches.sync.gc_client import GCClient
        client = GCClient()
        raw = client._api.get("/users/me", fields="referenceCode,username,membershipLevelId")
        username = raw.get("username", "?")
        level = raw.get("membershipLevelId", 0)
        level_names = {0: "Basic", 1: "Basic", 2: "Charter", 3: "Premium"}
        level_str = level_names.get(level, f"Level {level}")

        # Update membership level in the account and quota limit
        acct = UserAccount.objects.filter(platform="gc").first()
        if acct and acct.membership_level != level:
            acct.membership_level = level
            acct.save(update_fields=["membership_level"])

        # Update today's full-mode quota to match membership tier
        from geocaches.sync.rate_limiter import QuotaTracker
        full_limit = 16_000 if level >= 2 else 3
        QuotaTracker.set_limit("gc", "full", full_limit)

        gc_client.set_gc_api_verified(True)
        resp = HttpResponse(
            f'<span id="gc-api-status" class="text-success small">'
            f'&#10003; {username} ({level_str})</span>'
        )
        resp["HX-Trigger"] = "gcApiValidated"
        return resp
    except Exception as exc:
        gc_client.set_gc_api_verified(False)
        return HttpResponse(
            f'<span id="gc-api-status" class="text-danger small">'
            f'&#10007; {exc}</span>'
        )


def account_test_password(request):
    """HTMX endpoint: test the keyring-stored password by attempting an OC credential check."""
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    acct = UserAccount.objects.filter(id=request.POST.get("acct_id")).first()
    if not acct:
        return HttpResponse('<span class="text-danger small">Account not found</span>')

    password = keyring_util.get_password(acct.platform, acct.username)
    if not password:
        return HttpResponse('<span class="text-muted small">No password stored</span>')

    if acct.platform == "gc":
        valid, msg = gc_client.test_credentials(acct.username, password)
    else:
        valid, msg = okapi_client.test_credentials(acct.platform, acct.username, password)
    if valid:
        return HttpResponse(f'<span class="text-success small">&#10003; {msg}</span>')
    return HttpResponse(f'<span class="text-danger small">&#10007; {msg}</span>')
