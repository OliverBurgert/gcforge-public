"""Tests for UserPreference's process-level cache.

See docs/architecture-review-2026-09.md §4.1 item 2 and
docs/architecture-review-2026-09-workplan.md WP-01.
"""
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from preferences.models import UserPreference


class UserPreferenceCacheTests(TestCase):
    def test_repeated_get_issues_no_further_selects_once_warm(self):
        UserPreference.set("icon_set", "cgeo")
        UserPreference.get("icon_set")  # warm the cache
        with CaptureQueriesContext(connection) as ctx:
            for _ in range(5):
                UserPreference.get("icon_set")
                UserPreference.get("distance_unit", "km")
        self.assertEqual(len(ctx.captured_queries), 0)

    def test_set_is_visible_immediately(self):
        UserPreference.get("distance_unit", "km")  # warm the cache with the default
        UserPreference.set("distance_unit", "mi")
        self.assertEqual(UserPreference.get("distance_unit", "km"), "mi")

    def test_direct_create_invalidates_via_signal(self):
        UserPreference.get("scope_found", True)  # warm cache (row absent -> default)
        UserPreference.objects.create(key="scope_found", value="false")
        self.assertIs(UserPreference.get("scope_found", True), False)

    def test_queryset_delete_invalidates_via_signal(self):
        UserPreference.objects.create(key="scope_found", value="false")
        UserPreference.get("scope_found", True)  # warm cache with the row present
        UserPreference.objects.filter(key="scope_found").delete()
        self.assertIs(UserPreference.get("scope_found", True), True)

    def test_missing_key_returns_default(self):
        self.assertEqual(UserPreference.get("does_not_exist", "fallback"), "fallback")
