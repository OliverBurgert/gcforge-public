"""Tests for geocaches.runtime_cache and its use for platform availability.

See docs/architecture-review-2026-09.md §4.1 item 1 and
docs/architecture-review-2026-09-workplan.md WP-01/WP-04.
"""
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse

from accounts.models import UserAccount
from geocaches.models import Attribute, CacheType, Geocache
from geocaches.runtime_cache import (
    TTLCache,
    get_attribute_groups,
    get_available_platforms,
    get_country_list,
    get_my_usernames,
    invalidate_filter_dialog_options,
    invalidate_my_usernames_cache,
    invalidate_platform_availability,
)
from geocaches.services.trash import restore_cache, trash_cache


class TTLCacheTests(TestCase):
    def test_caches_the_computed_value(self):
        calls = []

        def compute():
            calls.append(1)
            return "value"

        cache = TTLCache(ttl_seconds=None)
        self.assertEqual(cache.get_or_compute(compute), "value")
        self.assertEqual(cache.get_or_compute(compute), "value")
        self.assertEqual(len(calls), 1)

    def test_invalidate_forces_recompute(self):
        calls = []

        def compute():
            calls.append(1)
            return len(calls)

        cache = TTLCache(ttl_seconds=None)
        self.assertEqual(cache.get_or_compute(compute), 1)
        cache.invalidate()
        self.assertEqual(cache.get_or_compute(compute), 2)

    def test_ttl_expiry_forces_recompute(self):
        calls = []

        def compute():
            calls.append(1)
            return len(calls)

        cache = TTLCache(ttl_seconds=60)
        with patch("time.monotonic", return_value=1000.0):
            self.assertEqual(cache.get_or_compute(compute), 1)
        with patch("time.monotonic", return_value=1000.0 + 61):
            self.assertEqual(cache.get_or_compute(compute), 2)

    def test_ttl_not_yet_expired_stays_cached(self):
        calls = []

        def compute():
            calls.append(1)
            return len(calls)

        cache = TTLCache(ttl_seconds=60)
        with patch("time.monotonic", return_value=1000.0):
            self.assertEqual(cache.get_or_compute(compute), 1)
        with patch("time.monotonic", return_value=1000.0 + 30):
            self.assertEqual(cache.get_or_compute(compute), 1)


class PlatformAvailabilityCacheTests(TestCase):
    def test_empty_database_reports_no_platforms(self):
        self.assertEqual(get_available_platforms(), [])

    def test_stays_stale_until_invalidated(self):
        self.assertEqual(get_available_platforms(), [])
        Geocache.objects.create(gc_code="GC00001", cache_type=CacheType.TRADITIONAL, latitude=48.0, longitude=9.0)
        # Direct ORM create bypasses save_geocache's invalidation hook, so the
        # cached (empty) result is still served until something invalidates it.
        self.assertEqual(get_available_platforms(), [])
        invalidate_platform_availability()
        self.assertIn("gc", get_available_platforms())

    def test_save_geocache_create_path_invalidates(self):
        from geocaches.services import save_geocache

        self.assertEqual(get_available_platforms(), [])
        save_geocache(
            gc_code="GC00002",
            fields={
                "name": "Test cache",
                "cache_type": CacheType.TRADITIONAL,
                "latitude": 48.0,
                "longitude": 9.0,
                "owner": "tester",
                "primary_source": "gc",
            },
        )
        self.assertIn("gc", get_available_platforms())

    def test_trash_and_restore_invalidate(self):
        cache = Geocache.objects.create(
            al_code="LC1TEST", cache_type=CacheType.LAB, latitude=48.0, longitude=9.0,
        )
        # First-ever call computes fresh (nothing cached yet), so no manual
        # invalidate() is needed to see the newly-created cache here.
        self.assertIn("lc", get_available_platforms())

        trash_cache(cache)
        self.assertEqual(get_available_platforms(), [])

        restore_cache(cache)
        self.assertIn("lc", get_available_platforms())

    def test_delete_task_invalidates(self):
        """start_deletion() runs the batch delete on a background thread with
        its own DB connection, which doesn't see this test's uncommitted
        transaction — so run it synchronously on the test's own thread/
        connection instead, the way TASKS_RUN_SYNC does for tasks/runner.py.
        """
        from geocaches.tasks import delete as delete_task

        class _SyncThread:
            def __init__(self, target=None, daemon=None):
                self._target = target

            def start(self):
                self._target()

        cache = Geocache.objects.create(
            oc_code="OC1TEST", cache_type=CacheType.TRADITIONAL, latitude=48.0, longitude=9.0,
        )
        invalidate_platform_availability()
        self.assertIn("oc", get_available_platforms())

        with patch("geocaches.tasks.delete.threading.Thread", _SyncThread):
            self.assertTrue(delete_task.start_deletion([cache.pk]))

        self.assertFalse(delete_task.get_status()["running"])
        self.assertEqual(get_available_platforms(), [])


class CountryListCacheTests(TestCase):
    def test_empty_database_reports_no_countries(self):
        data = get_country_list()
        self.assertEqual(data["countries"], [])
        self.assertFalse(data["has_no_country"])

    def test_stays_stale_until_invalidated(self):
        self.assertEqual(get_country_list()["countries"], [])
        Geocache.objects.create(
            gc_code="GC00001", cache_type=CacheType.TRADITIONAL,
            latitude=48.0, longitude=9.0, iso_country_code="DE",
        )
        # Direct ORM create doesn't invalidate — cached (empty) result served
        # until something invalidates it.
        self.assertEqual(get_country_list()["countries"], [])
        invalidate_filter_dialog_options()
        codes = [c["code"] for c in get_country_list()["countries"]]
        self.assertIn("DE", codes)

    def test_save_geocache_create_path_invalidates(self):
        from geocaches.services import save_geocache

        self.assertEqual(get_country_list()["countries"], [])
        save_geocache(
            gc_code="GC00002",
            fields={
                "name": "Test cache",
                "cache_type": CacheType.TRADITIONAL,
                "latitude": 48.0,
                "longitude": 9.0,
                "owner": "tester",
                "primary_source": "gc",
                "iso_country_code": "FR",
            },
        )
        codes = [c["code"] for c in get_country_list()["countries"]]
        self.assertIn("FR", codes)

    def test_trash_and_restore_invalidate(self):
        cache = Geocache.objects.create(
            gc_code="GC00003", cache_type=CacheType.TRADITIONAL,
            latitude=48.0, longitude=9.0, iso_country_code="IT",
        )
        self.assertIn("IT", [c["code"] for c in get_country_list()["countries"]])

        trash_cache(cache)
        self.assertEqual(get_country_list()["countries"], [])

        restore_cache(cache)
        self.assertIn("IT", [c["code"] for c in get_country_list()["countries"]])

    def test_enrichment_completing_invalidates(self):
        from geocaches.enrichment import enrich_queryset

        cache = Geocache.objects.create(
            gc_code="GC00004", cache_type=CacheType.TRADITIONAL,
            latitude=48.0, longitude=9.0,
        )
        self.assertEqual(get_country_list()["countries"], [])

        with patch("geocaches.enrichment.enrich_geocache") as mocked:
            def _fake_enrich(c, fields, overwrite):
                c.iso_country_code = "ES"
                c.save(update_fields=["iso_country_code"])
                return True
            mocked.side_effect = _fake_enrich
            enrich_queryset(Geocache.objects.filter(pk=cache.pk), fields={"location"})

        self.assertIn("ES", [c["code"] for c in get_country_list()["countries"]])


class AttributeGroupsCacheTests(TestCase):
    def test_empty_database_reports_no_attributes(self):
        self.assertEqual(get_attribute_groups(), {})

    def test_stays_stale_until_invalidated(self):
        self.assertEqual(get_attribute_groups(), {})
        Attribute.objects.create(source="gc", attribute_id=1, name="Dogs", is_positive=True)
        self.assertEqual(get_attribute_groups(), {})
        invalidate_filter_dialog_options()
        self.assertIn("gc", get_attribute_groups())

    def test_grouped_by_source(self):
        Attribute.objects.create(source="gc", attribute_id=1, name="Dogs", is_positive=True)
        Attribute.objects.create(source="oc", attribute_id=2, name="Kids", is_positive=True)
        invalidate_filter_dialog_options()
        groups = get_attribute_groups()
        self.assertEqual({a.name for a in groups["gc"]}, {"Dogs"})
        self.assertEqual({a.name for a in groups["oc"]}, {"Kids"})


class MyUsernamesCacheTests(TestCase):
    """See docs/architecture-review-2026-09.md §8 (image_cache._my_usernames)."""

    def test_empty_database_reports_no_usernames(self):
        self.assertEqual(get_my_usernames(), set())

    def test_stays_stale_until_invalidated(self):
        self.assertEqual(get_my_usernames(), set())
        UserAccount.objects.create(platform="gc", username="Alice", user_id="1")
        # Direct ORM create bypasses the accounts views' invalidation hook,
        # so the cached (empty) result is still served until something
        # invalidates it.
        self.assertEqual(get_my_usernames(), set())
        invalidate_my_usernames_cache()
        self.assertEqual(get_my_usernames(), {"alice"})

    def test_gc_username_preference_is_a_fallback(self):
        from preferences.models import UserPreference

        UserPreference.set("gc_username", "Bob")
        invalidate_my_usernames_cache()
        self.assertEqual(get_my_usernames(), {"bob"})

    def test_add_account_view_invalidates_immediately(self):
        self.assertEqual(get_my_usernames(), set())
        resp = self.client.post(reverse("accounts:add_account"), {
            "acct_platform": "gc",
            "acct_username": "carol",
        })
        self.assertEqual(resp.status_code, 302)
        self.assertIn("carol", get_my_usernames())

    def test_delete_account_view_invalidates_immediately(self):
        acct = UserAccount.objects.create(platform="gc", username="dave", user_id="2")
        invalidate_my_usernames_cache()
        self.assertIn("dave", get_my_usernames())

        resp = self.client.post(reverse("accounts:delete_account"), {"acct_id": acct.pk})
        self.assertEqual(resp.status_code, 302)
        self.assertNotIn("dave", get_my_usernames())

    def test_save_gc_username_view_invalidates_immediately(self):
        self.assertEqual(get_my_usernames(), set())
        resp = self.client.post(reverse("preferences:save_gc_username"), {"gc_username": "erin"})
        self.assertEqual(resp.status_code, 302)
        self.assertIn("erin", get_my_usernames())


class StateForCacheQueryCountTests(TestCase):
    """image_cache.state_for_cache() is called once per image — 22 times on a
    cache-detail render, once per cache in the image-cache bulk-fill task's
    loop — so a warm call must not re-query UserAccount/UserPreference."""

    def test_repeated_calls_issue_no_further_account_queries(self):
        from geocaches.services.image_cache import state_for_cache

        UserAccount.objects.create(platform="gc", username="finder", user_id="3")
        cache = Geocache.objects.create(
            gc_code="GC00005", cache_type=CacheType.TRADITIONAL,
            latitude=48.0, longitude=9.0, owner="finder",
        )
        invalidate_my_usernames_cache()
        self.assertEqual(state_for_cache(cache), "mine")  # cold: computes + caches

        with self.assertNumQueries(0):
            self.assertEqual(state_for_cache(cache), "mine")
            self.assertEqual(state_for_cache(cache), "mine")
