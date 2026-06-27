from django.shortcuts import render

from preferences.models import UserPreference


def user_profile(request):
    from accounts.models import UserAccount
    from accounts import gc_client, keyring_util, okapi_client
    from datetime import date

    cards = []

    # --- GC accounts ---
    gc_accounts = UserAccount.objects.filter(platform="gc")
    if gc_accounts.exists():
        for acct in gc_accounts:
            card = {
                "platform": "geocaching.com",
                "platform_key": "gc",
                "username": acct.username,
                "profile_url": acct.profile_url,
                "error": None,
                "data": None,
            }
            if gc_client.has_api_tokens():
                try:
                    from gcprivate.gc_client import GCClient
                    client = GCClient()
                    raw = client._api.get(
                        "/users/me",
                        fields="referenceCode,findCount,hideCount,favoritePoints,"
                               "membershipLevelId,avatarUrl,homeCoordinates,"
                               "username,joinedDateUtc",
                    )
                    level = raw.get("membershipLevelId", 0)
                    level_names = {0: "Unknown", 1: "Basic", 2: "Charter", 3: "Premium"}
                    home = raw.get("homeCoordinates") or {}
                    card["data"] = {
                        "username": raw.get("username", ""),
                        "reference_code": raw.get("referenceCode", ""),
                        "find_count": raw.get("findCount"),
                        "hide_count": raw.get("hideCount"),
                        "favorite_points": raw.get("favoritePoints"),
                        "membership": level_names.get(level, f"Level {level}"),
                        "avatar_url": raw.get("avatarUrl", ""),
                        "join_date": (raw.get("joinedDateUtc") or "")[:10],
                        "home_lat": home.get("latitude"),
                        "home_lon": home.get("longitude"),
                    }
                    # Quota info — ensure today's records exist so we always show usage
                    from geocaches.sync.rate_limiter import QuotaTracker
                    today = date.today()
                    quotas = []
                    for mode in ("light", "full"):
                        remaining = QuotaTracker.remaining("gc", mode)
                        from geocaches.models import SyncQuota
                        sq = SyncQuota.objects.get(platform="gc", mode=mode, date=today)
                        quotas.append({
                            "mode": mode,
                            "used": sq.used,
                            "limit": sq.limit,
                            "remaining": remaining,
                        })
                    card["quotas"] = quotas

                    # Trackable inventory (non-fatal — surface a notice on error).
                    # Enriched with local auto-visit settings so the user can
                    # toggle per-TB auto-visit + text directly from this panel.
                    try:
                        from gcprivate.trackable_client import TrackableClient
                        from geocaches.models import Trackable
                        tbs = TrackableClient().get_my_inventory()
                        refs = [t.get("reference_code", "") for t in tbs if t.get("reference_code")]
                        local = {
                            row.reference_code: row
                            for row in Trackable.objects.filter(reference_code__in=refs)
                        }
                        card["trackables"] = []
                        for t in tbs:
                            ref = t.get("reference_code", "")
                            row = local.get(ref)
                            card["trackables"].append({
                                "ref_code":           ref,
                                "name":               t.get("name", ""),
                                "icon_url":           t.get("icon_url", ""),
                                "auto_visit_enabled": bool(row.auto_visit_enabled) if row else False,
                                "auto_visit_text":    row.auto_visit_text if row else "",
                            })
                    except Exception as tb_exc:
                        card["trackables_error"] = str(tb_exc)
                        card["trackables"] = []
                except Exception as exc:
                    card["error"] = str(exc)
            else:
                card["error"] = "No GC API tokens available."
            cards.append(card)
    else:
        cards.append({
            "platform": "geocaching.com",
            "platform_key": "gc",
            "username": None,
            "profile_url": "",
            "error": "Not configured",
            "data": None,
        })

    # --- OC accounts ---
    oc_accounts = UserAccount.objects.filter(platform__startswith="oc_")
    if oc_accounts.exists():
        for acct in oc_accounts:
            card = {
                "platform": acct.get_platform_display(),
                "platform_key": acct.platform,
                "username": acct.username,
                "profile_url": acct.profile_url,
                "error": None,
                "data": None,
            }
            node_url = okapi_client.get_node_url(acct.platform)
            if not node_url:
                card["error"] = f"Unknown OC platform: {acct.platform}"
                cards.append(card)
                continue

            custom_key = UserPreference.get(f"okapi_consumer_key_{acct.platform}", "")
            custom_secret = UserPreference.get(f"okapi_consumer_secret_{acct.platform}", "")
            creds = okapi_client.get_consumer_credentials(acct.platform, custom_key, custom_secret)
            if not creds:
                card["error"] = "No consumer key available."
                cards.append(card)
                continue

            consumer_key, consumer_secret = creds
            oauth_creds = keyring_util.get_oauth_token(acct.platform, acct.user_id)

            try:
                fields = "uuid|username|profile_url|caches_found|caches_notfound|caches_hidden|rcmds_given|date_registered|home_location"
                if oauth_creds:
                    oauth_token, oauth_token_secret = oauth_creds
                    raw = okapi_client._get_level3(
                        f"{node_url}/okapi/services/users/user",
                        {"fields": fields},
                        consumer_key, consumer_secret,
                        oauth_token, oauth_token_secret,
                    )
                else:
                    raw = okapi_client._get_level1(
                        f"{node_url}/okapi/services/users/user",
                        {"fields": fields, "user_uuid": acct.user_id},
                        consumer_key,
                    )
                home = raw.get("home_location") or ""
                home_lat = None
                home_lon = None
                if home and "|" in home:
                    parts = home.split("|")
                    try:
                        home_lat = float(parts[0])
                        home_lon = float(parts[1])
                    except (ValueError, IndexError):
                        pass
                card["data"] = {
                    "username": raw.get("username", ""),
                    "uuid": raw.get("uuid", ""),
                    "profile_url": raw.get("profile_url", ""),
                    "caches_found": raw.get("caches_found"),
                    "caches_notfound": raw.get("caches_notfound"),
                    "caches_hidden": raw.get("caches_hidden"),
                    "rcmds_given": raw.get("rcmds_given"),
                    "date_registered": raw.get("date_registered", ""),
                    "home_lat": home_lat,
                    "home_lon": home_lon,
                }
            except Exception as exc:
                card["error"] = str(exc)
            cards.append(card)
    else:
        has_oc = any(p.startswith("oc_") for p, _ in UserAccount.PLATFORM_CHOICES)
        if has_oc:
            cards.append({
                "platform": "opencaching",
                "platform_key": "oc",
                "username": None,
                "profile_url": "",
                "error": "Not configured",
                "data": None,
            })

    # Cache total platform finds (sum of GC findCount + OC caches_found over
    # all configured accounts). Read by the log-compose dialog to seed the
    # "Find #" field. Refreshed implicitly every time the user visits this
    # page; the user can adjust the field manually for any individual log.
    total = 0
    have_any = False
    for c in cards:
        d = c.get("data") or {}
        for key in ("find_count", "caches_found"):
            v = d.get(key)
            if isinstance(v, int):
                total += v
                have_any = True
    if have_any:
        UserPreference.set("cached_total_finds", total)

    return render(request, "preferences/user_profile.html", {"cards": cards})
