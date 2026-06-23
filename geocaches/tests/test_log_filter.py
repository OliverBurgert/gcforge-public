"""
Behaviour-pinning tests for the cache-detail log filter (``?log_filter=``).

These guard the subtle per-platform / blank-source / is_local rules in
``geocaches.views.detail.cache_detail`` so the shared-identity refactor
(``query.resolve_my_identities``) can't change observable behaviour.

The "my" filter rules being pinned:
  * per-platform: a log matches when its ``source`` equals an account's
    platform AND its ``user_id`` / ``user_name`` matches that platform's
    identities (user_name is matched case-insensitively),
  * blank ``source`` (GSAK / legacy imports): matches ANY known id or name,
  * ``is_local=True`` logs are always mine,
  * a non-blank-source log is NOT matched by an identity from a *different*
    platform,
  * the legacy ``gc_username`` preference is folded into the GC platform.

The "owner" filter rules being pinned:
  * owner-action log types always match,
  * gc-owner identity (``user_id`` / ``user_name``) matches on gc / blank
    sources,
  * for an OC-only cache, the gc-owner identity also matches OC sources.
"""

from datetime import date

from django.test import TestCase
from django.urls import reverse

from accounts.models import UserAccount
from geocaches.models import CacheSize, CacheStatus, CacheType, Geocache, Log


def _make_cache(gc_code="GCLOG1", *, owner="cacheowner", owner_gc_id=None,
                oc_code=""):
    return Geocache.objects.create(
        gc_code=gc_code,
        name="Log Filter Cache",
        cache_type=CacheType.TRADITIONAL,
        size=CacheSize.SMALL,
        status=CacheStatus.ACTIVE,
        latitude=48.0,
        longitude=9.0,
        difficulty=2.0,
        terrain=2.0,
        hidden_date=date(2020, 1, 1),
        owner=owner,
        owner_gc_id=owner_gc_id,
        oc_code=oc_code,
    )


def _log(cache, *, log_type="Found it", user_id="", user_name="",
         source="", is_local=False):
    return Log.objects.create(
        geocache=cache,
        log_type=log_type,
        user_id=user_id,
        user_name=user_name,
        source=source,
        is_local=is_local,
        logged_date=date(2024, 1, 1),
    )


def _filtered_log_ids(client, code, log_filter):
    resp = client.get(reverse("geocaches:detail", args=[code]),
                      {"log_filter": log_filter})
    assert resp.status_code == 200, resp.status_code
    return {log.pk for log in resp.context["log_page_obj"].object_list}


class TestMyLogFilter(TestCase):
    def test_matches_per_platform_by_user_id_and_name(self):
        cache = _make_cache()
        UserAccount.objects.create(platform="gc", username="alice", user_id="42")
        mine_id = _log(cache, user_id="42", user_name="alice", source="gc")
        mine_name = _log(cache, user_id="", user_name="Alice", source="gc")  # iexact
        not_mine = _log(cache, user_id="99", user_name="bob", source="gc")

        ids = _filtered_log_ids(self.client, cache.gc_code, "my")
        self.assertIn(mine_id.pk, ids)
        self.assertIn(mine_name.pk, ids)
        self.assertNotIn(not_mine.pk, ids)

    def test_blank_source_matches_any_known_identity(self):
        cache = _make_cache()
        UserAccount.objects.create(platform="gc", username="alice", user_id="42")
        UserAccount.objects.create(platform="oc_de", username="bob", user_id="bob-uuid")
        # Blank source (GSAK/legacy): any known id or name matches.
        by_gc_id = _log(cache, user_id="42", user_name="someone", source="")
        by_oc_name = _log(cache, user_id="", user_name="bob", source="")
        stranger = _log(cache, user_id="0", user_name="charlie", source="")

        ids = _filtered_log_ids(self.client, cache.gc_code, "my")
        self.assertIn(by_gc_id.pk, ids)
        self.assertIn(by_oc_name.pk, ids)
        self.assertNotIn(stranger.pk, ids)

    def test_is_local_always_mine(self):
        cache = _make_cache()
        UserAccount.objects.create(platform="gc", username="alice", user_id="42")
        local = _log(cache, user_id="999", user_name="nomatch", source="gc",
                     is_local=True)
        ids = _filtered_log_ids(self.client, cache.gc_code, "my")
        self.assertIn(local.pk, ids)

    def test_cross_platform_name_does_not_match_non_blank_source(self):
        # An identity from platform gc must NOT match a log whose source is
        # a different platform (oc_de) — only blank source crosses platforms.
        cache = _make_cache()
        UserAccount.objects.create(platform="gc", username="alice", user_id="42")
        cross = _log(cache, user_id="", user_name="alice", source="oc_de")
        ids = _filtered_log_ids(self.client, cache.gc_code, "my")
        self.assertNotIn(cross.pk, ids)

    def test_gc_username_preference_fallback(self):
        from preferences.models import UserPreference
        cache = _make_cache()
        # No UserAccount; only the legacy gc_username preference.
        UserPreference.set("gc_username", "legacyme")
        try:
            mine = _log(cache, user_id="", user_name="legacyme", source="gc")
            not_mine = _log(cache, user_id="", user_name="other", source="gc")
            ids = _filtered_log_ids(self.client, cache.gc_code, "my")
            self.assertIn(mine.pk, ids)
            self.assertNotIn(not_mine.pk, ids)
        finally:
            UserPreference.objects.filter(key="gc_username").delete()

    def test_no_identities_returns_only_local(self):
        from preferences.models import UserPreference
        UserPreference.objects.filter(key="gc_username").delete()
        cache = _make_cache()
        # No accounts, no gc_username: my_q_parts has only Q(is_local=True).
        local = _log(cache, user_id="42", user_name="x", source="gc", is_local=True)
        remote = _log(cache, user_id="42", user_name="x", source="gc")
        ids = _filtered_log_ids(self.client, cache.gc_code, "my")
        self.assertEqual(ids, {local.pk})


class TestOwnerLogFilter(TestCase):
    def test_owner_action_log_types_always_match(self):
        cache = _make_cache(owner="cacheowner", owner_gc_id=7)
        maint = _log(cache, log_type="Owner Maintenance", user_id="999",
                     user_name="someoneelse", source="gc")
        found = _log(cache, log_type="Found it", user_id="999",
                     user_name="someoneelse", source="gc")
        ids = _filtered_log_ids(self.client, cache.gc_code, "owner")
        self.assertIn(maint.pk, ids)
        self.assertNotIn(found.pk, ids)

    def test_owner_identity_matches_on_gc_and_blank_source(self):
        cache = _make_cache(owner="cacheowner", owner_gc_id=7)
        by_id = _log(cache, log_type="Write note", user_id="7",
                     user_name="cacheowner", source="gc")
        by_name_blank = _log(cache, log_type="Write note", user_id="",
                             user_name="cacheowner", source="")
        other = _log(cache, log_type="Write note", user_id="8",
                     user_name="other", source="gc")
        ids = _filtered_log_ids(self.client, cache.gc_code, "owner")
        self.assertIn(by_id.pk, ids)
        self.assertIn(by_name_blank.pk, ids)
        self.assertNotIn(other.pk, ids)

    def test_oc_only_cache_owner_name_matches_oc_source(self):
        # OC-only cache (no gc_code): owner identity also matches OC sources.
        cache = _make_cache(gc_code="", oc_code="OC1234", owner="ocowner")
        oc_owner = _log(cache, log_type="Write note", user_id="",
                        user_name="ocowner", source="oc_de")
        ids = _filtered_log_ids(self.client, cache.oc_code, "owner")
        self.assertIn(oc_owner.pk, ids)
