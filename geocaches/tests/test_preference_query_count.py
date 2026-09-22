"""Preference-SELECT-count tests for the pages named in the WP-01 acceptance
criteria: '/', '/settings/', '/trackables/' should each issue at most 2
SELECTs against preferences_userpreference once UserPreference's process-
level cache is warm.

See docs/architecture-review-2026-09.md §3 and
docs/architecture-review-2026-09-workplan.md WP-01.
"""
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse


def _pref_query_count(ctx):
    return sum(1 for q in ctx.captured_queries if "preferences_userpreference" in q["sql"])


class PreferenceQueryCountTests(TestCase):
    def _warm_and_measure(self, path):
        self.client.get(path)  # cold: builds the preference cache
        with CaptureQueriesContext(connection) as ctx:
            response = self.client.get(path)
        self.assertEqual(response.status_code, 200)
        return _pref_query_count(ctx)

    def test_list_page(self):
        self.assertLessEqual(self._warm_and_measure(reverse("geocaches:list")), 2)

    def test_settings_page(self):
        self.assertLessEqual(self._warm_and_measure(reverse("preferences:settings")), 2)

    def test_trackables_page(self):
        self.assertLessEqual(self._warm_and_measure(reverse("geocaches:trackable_list")), 2)
