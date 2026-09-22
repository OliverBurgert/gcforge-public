"""Tests for preferences.context_processors' process-level caches.

See docs/architecture-review-2026-09.md §4.1 item 1 and
docs/architecture-review-2026-09-workplan.md WP-01.
"""
from unittest.mock import patch

from django.contrib.sessions.backends.db import SessionStore
from django.db import connection
from django.test import RequestFactory, TestCase
from django.test.utils import CaptureQueriesContext

from preferences import context_processors as cp
from preferences.context_processors import (
    forging_scope, invalidate_connected_platforms_cache, invalidate_profile_cache,
)


def _request():
    req = RequestFactory().get("/")
    req.session = SessionStore()
    return req


class ForgingScopeQueryCountTests(TestCase):
    def test_warm_call_is_cheap(self):
        forging_scope(_request())  # cold: builds every underlying cache
        with CaptureQueriesContext(connection) as ctx:
            forging_scope(_request())
        self.assertLessEqual(len(ctx.captured_queries), 3, ctx.captured_queries)

    def test_warm_call_has_no_preference_selects(self):
        forging_scope(_request())
        with CaptureQueriesContext(connection) as ctx:
            forging_scope(_request())
        pref_queries = [q for q in ctx.captured_queries if "preferences_userpreference" in q["sql"]]
        self.assertEqual(pref_queries, [])


class ConnectedPlatformsCacheTests(TestCase):
    def test_cached_between_calls(self):
        calls = []

        def fake_compute():
            calls.append(1)
            return []

        with patch.object(cp, "_compute_connected_platforms", fake_compute):
            cp._connected_platforms_cache.invalidate()
            cp._connected_platforms()
            cp._connected_platforms()
        self.assertEqual(len(calls), 1)

    def test_invalidate_forces_recompute(self):
        calls = []

        def fake_compute():
            calls.append(1)
            return []

        with patch.object(cp, "_compute_connected_platforms", fake_compute):
            cp._connected_platforms_cache.invalidate()
            cp._connected_platforms()
            invalidate_connected_platforms_cache()
            cp._connected_platforms()
        self.assertEqual(len(calls), 2)


class ProfileInfoCacheTests(TestCase):
    def test_cached_between_calls(self):
        calls = []

        def fake_compute():
            calls.append(1)
            return {"name": "Default", "count": 1}

        with patch.object(cp, "_compute_profile_info", fake_compute):
            cp._profile_info_cache.invalidate()
            forging_scope(_request())
            forging_scope(_request())
        self.assertEqual(len(calls), 1)

    def test_invalidate_forces_recompute(self):
        calls = []

        def fake_compute():
            calls.append(1)
            return {"name": "Default", "count": 1}

        with patch.object(cp, "_compute_profile_info", fake_compute):
            cp._profile_info_cache.invalidate()
            forging_scope(_request())
            invalidate_profile_cache()
            forging_scope(_request())
        self.assertEqual(len(calls), 2)
