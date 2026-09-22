"""
Tests for preferences migration 0006: rekeying the 'pq_imported' preference's
bare-identifier entries (GUIDs from the website download path, referenceCodes
from the official-API path, both pre-fix) to "<identifier>@<pst-date>" — see
geocaches.pq.service.pq_import_key() and its 2026-09-06 fix.
"""

import importlib

from django.test import TestCase

from preferences.models import UserPreference


def _load_migration_0006():
    mod = importlib.import_module(
        "preferences.migrations.0006_key_pq_web_imports_by_generation_date"
    )
    return mod.key_pq_imports_by_generation_date, mod.unkey_pq_imports


def _migration_apps():
    from django.apps import apps
    return apps


class KeyPqImportsByGenerationDateTest(TestCase):
    def test_bare_guid_keys_get_dated(self):
        UserPreference.set("pq_imported", {
            "45760092-82b7-4f36-ae69-cec08c88e2a4": "2026-08-17T10:54:35+00:00",
        })
        forward, _ = _load_migration_0006()

        forward(_migration_apps(), None)

        imported = UserPreference.get("pq_imported")
        self.assertEqual(
            imported,
            {"45760092-82b7-4f36-ae69-cec08c88e2a4@2026-08-17": "2026-08-17T10:54:35+00:00"},
        )

    def test_bare_reference_code_keys_also_get_dated(self):
        """referenceCode is *also* stable across regenerations, not just
        GUID — confirmed live 2026-09-06 (same referenceCode returned by the
        API for the same PQ 3+ weeks apart). The first version of this
        migration only dated GUID-shaped keys, which left every
        official-API-imported PQ showing "not imported" on the next page
        load even right after a genuinely fresh download."""
        UserPreference.set("pq_imported", {"PQND4TX": "2026-09-06T14:14:17+00:00"})
        forward, _ = _load_migration_0006()

        forward(_migration_apps(), None)

        self.assertEqual(
            UserPreference.get("pq_imported"),
            {"PQND4TX@2026-09-06": "2026-09-06T14:14:17+00:00"},
        )

    def test_already_dated_keys_are_untouched(self):
        UserPreference.set("pq_imported", {
            "45760092-82b7-4f36-ae69-cec08c88e2a4@2026-09-06": "2026-09-06T14:14:21+00:00",
        })
        forward, _ = _load_migration_0006()

        forward(_migration_apps(), None)

        self.assertEqual(
            UserPreference.get("pq_imported"),
            {"45760092-82b7-4f36-ae69-cec08c88e2a4@2026-09-06": "2026-09-06T14:14:21+00:00"},
        )

    def test_utc_timestamp_near_midnight_converts_to_the_correct_pst_date(self):
        # 2026-08-18T02:00:00Z is still 2026-08-17 in PDT (UTC-7).
        UserPreference.set("pq_imported", {
            "45760092-82b7-4f36-ae69-cec08c88e2a4": "2026-08-18T02:00:00+00:00",
        })
        forward, _ = _load_migration_0006()

        forward(_migration_apps(), None)

        self.assertEqual(
            UserPreference.get("pq_imported"),
            {"45760092-82b7-4f36-ae69-cec08c88e2a4@2026-08-17": "2026-08-18T02:00:00+00:00"},
        )

    def test_no_pq_imported_preference_is_a_no_op(self):
        forward, _ = _load_migration_0006()
        forward(_migration_apps(), None)  # must not raise
        self.assertIsNone(UserPreference.get("pq_imported"))

    def test_reverse_strips_the_date_suffix(self):
        UserPreference.set("pq_imported", {
            "45760092-82b7-4f36-ae69-cec08c88e2a4@2026-08-17": "2026-08-17T10:54:35+00:00",
            "PQND4TX@2026-09-06": "2026-09-06T14:14:17+00:00",
        })
        _, reverse = _load_migration_0006()

        reverse(_migration_apps(), None)

        self.assertEqual(
            UserPreference.get("pq_imported"),
            {
                "45760092-82b7-4f36-ae69-cec08c88e2a4": "2026-08-17T10:54:35+00:00",
                "PQND4TX": "2026-09-06T14:14:17+00:00",
            },
        )
