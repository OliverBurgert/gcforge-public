"""Tests for the duplicate-log tools views (tools_dedup.py).

Covers:
- tools_duped_my_logs: no accounts, no dupes, actual dupes
- tools_duped_cache_logs: no owned caches, actual dupes
"""
from datetime import date

from django.test import TestCase, RequestFactory

from accounts.models import UserAccount
from geocaches.models import (
    CacheSize, CacheStatus, CacheType, Geocache, Log,
)
from geocaches.views.tools_dedup import tools_duped_my_logs, tools_duped_cache_logs

D = date(2024, 1, 1)


def _make_cache(gc_code, owner="someone", owner_gc_id=None, found=False):
    return Geocache.objects.create(
        gc_code=gc_code, name=f"Cache {gc_code}",
        cache_type=CacheType.TRADITIONAL, size=CacheSize.SMALL,
        status=CacheStatus.ACTIVE, latitude=48.0, longitude=9.0,
        difficulty=2.0, terrain=2.0, hidden_date=D,
        owner=owner, owner_gc_id=owner_gc_id, found=found,
    )


def _log(cache, user_id="42", user_name="alice", source="gc"):
    return Log.objects.create(
        geocache=cache, log_type="Found it", user_id=user_id,
        user_name=user_name, source=source, logged_date=D,
    )


class TestDupedMyLogs(TestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def test_no_accounts_shows_message(self):
        req = self.factory.get("/tools/duped-my-logs/")
        resp = tools_duped_my_logs(req)
        self.assertContains(resp, "No accounts configured")

    def test_no_dupes_returns_empty(self):
        UserAccount.objects.create(platform="gc", username="alice", user_id="42")
        cache = _make_cache("GC00001")
        _log(cache)
        req = self.factory.get("/tools/duped-my-logs/")
        resp = tools_duped_my_logs(req)
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, "GC00001")

    def test_finds_duplicates(self):
        UserAccount.objects.create(platform="gc", username="alice", user_id="42")
        cache = _make_cache("GC00001")
        _log(cache)
        _log(cache)
        req = self.factory.get("/tools/duped-my-logs/")
        resp = tools_duped_my_logs(req)
        self.assertContains(resp, "GC00001")

    def test_groups_by_source(self):
        UserAccount.objects.create(platform="gc", username="alice", user_id="42")
        UserAccount.objects.create(platform="oc_de", username="alice", user_id="42oc")
        cache = _make_cache("GC00001")
        _log(cache, source="gc")
        _log(cache, source="gc")
        _log(cache, user_id="42oc", source="oc_de")
        _log(cache, user_id="42oc", source="oc_de")
        req = self.factory.get("/tools/duped-my-logs/")
        resp = tools_duped_my_logs(req)
        content = resp.content.decode()
        self.assertIn("GC", content)
        self.assertIn("OC DE", content)


class TestDupedCacheLogs(TestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def test_no_owned_caches_shows_message(self):
        UserAccount.objects.create(platform="gc", username="alice", user_id="42")
        _make_cache("GC00001", owner="other")
        req = self.factory.get("/tools/duped-cache-logs/")
        resp = tools_duped_cache_logs(req)
        self.assertContains(resp, "No owned caches")

    def test_finds_dupes_on_owned_cache(self):
        UserAccount.objects.create(platform="gc", username="alice", user_id="42")
        cache = _make_cache("GC00001", owner="alice", owner_gc_id=42)
        _log(cache, user_id="99", user_name="bob")
        _log(cache, user_id="99", user_name="bob")
        req = self.factory.get("/tools/duped-cache-logs/")
        resp = tools_duped_cache_logs(req)
        self.assertContains(resp, "GC00001")
        self.assertContains(resp, "bob")
