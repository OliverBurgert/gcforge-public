"""
Tests for save_geocache field precedence — the GC-vs-OC ownership rules.

Rule: if a cache has a gc_code, GC owns the shared fields. An OC update (from
any OC platform, including oc_de) may not overwrite _GC_OWNED_FIELDS; it may
still touch OC-specific fields and append new logs. primary_source plays no
role in this decision — presence of gc_code is the sole determinant.

Test parameterization is on (primary_source, has_gc_code, update_source) →
whether _GC_OWNED_FIELDS are protected from the incoming update.
"""

from datetime import date

from django.test import TestCase

from geocaches.models import (
    CacheSize,
    CacheStatus,
    CacheType,
    Geocache,
)
from geocaches.services import save_geocache


def _make_cache(*, primary_source, gc_code="", oc_code="", **overrides):
    defaults = dict(
        name="Original Name",
        owner="OriginalOwner",
        cache_type=CacheType.TRADITIONAL,
        size=CacheSize.SMALL,
        status=CacheStatus.ACTIVE,
        latitude=48.0,
        longitude=9.0,
        difficulty=2.0,
        terrain=2.0,
        hidden_date=date(2020, 1, 1),
        country="Germany",
        primary_source=primary_source,
        gc_code=gc_code,
        oc_code=oc_code,
    )
    defaults.update(overrides)
    return Geocache.objects.create(**defaults)


def _update_payload(**overrides):
    payload = dict(
        name="Updated Name",
        owner="UpdatedOwner",
        cache_type=CacheType.MULTI,
        size=CacheSize.REGULAR,
        status=CacheStatus.ACTIVE,
        latitude=49.0,
        longitude=10.0,
        difficulty=4.0,
        terrain=4.0,
        hidden_date=date(2021, 6, 1),
        country="Austria",
    )
    payload.update(overrides)
    return payload


# ---------------------------------------------------------------------------
# GC primary, OC update — owned fields MUST be protected
# ---------------------------------------------------------------------------

class GcPrimaryOcUpdate(TestCase):
    """When primary=gc and update_source=oc, GC-owned fields are preserved."""

    def test_owned_fields_preserved(self):
        cache = _make_cache(primary_source="gc", gc_code="GC1111", oc_code="OC1111")
        save_geocache(
            gc_code="GC1111",
            oc_code="OC1111",
            fields=_update_payload(),
            update_source="oc",
        )
        cache.refresh_from_db()
        self.assertEqual(cache.name, "Original Name")
        self.assertEqual(cache.owner, "OriginalOwner")
        self.assertEqual(cache.cache_type, CacheType.TRADITIONAL)
        self.assertEqual(cache.country, "Germany")


# ---------------------------------------------------------------------------
# GC primary, GC update — owned fields MUST update
# ---------------------------------------------------------------------------

class GcPrimaryGcUpdate(TestCase):
    def test_owned_fields_overwritten(self):
        cache = _make_cache(primary_source="gc", gc_code="GC2222")
        save_geocache(
            gc_code="GC2222",
            fields=_update_payload(),
            update_source="gc",
        )
        cache.refresh_from_db()
        self.assertEqual(cache.name, "Updated Name")
        self.assertEqual(cache.country, "Austria")


# ---------------------------------------------------------------------------
# OC-de primary WITH gc_code — OC update must NOT overwrite GC-owned fields
# (oc_de is not special; any OC update to a gc-coded cache is guarded)
# ---------------------------------------------------------------------------

class OcDePrimaryOcUpdate(TestCase):
    def test_owned_fields_preserved(self):
        cache = _make_cache(
            primary_source="oc_de", gc_code="GC3333", oc_code="OC3333"
        )
        save_geocache(
            gc_code="GC3333",
            oc_code="OC3333",
            fields=_update_payload(),
            update_source="oc",
        )
        cache.refresh_from_db()
        self.assertEqual(cache.name, "Original Name")
        self.assertEqual(cache.country, "Germany")


# ---------------------------------------------------------------------------
# Pure OC cache (no gc_code) — OC update always wins regardless of primary
# ---------------------------------------------------------------------------

class PureOcCacheOcUpdate(TestCase):
    def test_no_gc_code_means_guard_inactive(self):
        cache = _make_cache(primary_source="oc_de", oc_code="OC4444")
        save_geocache(
            oc_code="OC4444",
            fields=_update_payload(),
            update_source="oc",
        )
        cache.refresh_from_db()
        self.assertEqual(cache.name, "Updated Name")


# ---------------------------------------------------------------------------
# Found never demotes
# ---------------------------------------------------------------------------

class FoundNeverDemotes(TestCase):
    def test_found_true_stays_true_when_incoming_false(self):
        cache = _make_cache(primary_source="gc", gc_code="GC5555", found=True)
        save_geocache(
            gc_code="GC5555",
            fields=_update_payload(),
            found=False,
            update_source="gc",
        )
        cache.refresh_from_db()
        self.assertTrue(cache.found)

    def test_found_false_promotes_to_true(self):
        cache = _make_cache(primary_source="gc", gc_code="GC5556", found=False)
        save_geocache(
            gc_code="GC5556",
            fields=_update_payload(),
            found=True,
            found_date=date(2024, 5, 1),
            update_source="gc",
        )
        cache.refresh_from_db()
        self.assertTrue(cache.found)
        self.assertEqual(cache.found_date, date(2024, 5, 1))


# ---------------------------------------------------------------------------
# Fused caches with non-oc_de OC primary — GC-owned fields still protected
# because presence of gc_code is what triggers the guard, not primary_source.
# ---------------------------------------------------------------------------

class OcUsPrimaryOcUpdate(TestCase):
    def test_owned_fields_preserved(self):
        cache = _make_cache(
            primary_source="oc_us", gc_code="GC6666", oc_code="OC6666"
        )
        save_geocache(
            gc_code="GC6666",
            oc_code="OC6666",
            fields=_update_payload(),
            update_source="oc",
        )
        cache.refresh_from_db()
        self.assertEqual(cache.name, "Original Name")


class OcPlPrimaryOcUpdate(TestCase):
    def test_owned_fields_preserved(self):
        cache = _make_cache(
            primary_source="oc_pl", gc_code="GC7777", oc_code="OC7777"
        )
        save_geocache(
            gc_code="GC7777",
            oc_code="OC7777",
            fields=_update_payload(),
            update_source="oc",
        )
        cache.refresh_from_db()
        self.assertEqual(cache.name, "Original Name")


# ---------------------------------------------------------------------------
# Empty primary_source with gc_code — guard still fires (gc_code presence
# alone is the determinant). Protects half-initialised imports.
# ---------------------------------------------------------------------------

class EmptyPrimarySourceProtected(TestCase):
    def test_empty_primary_still_protects_gc_fields(self):
        cache = _make_cache(
            primary_source="", gc_code="GC8888", oc_code="OC8888"
        )
        save_geocache(
            gc_code="GC8888",
            oc_code="OC8888",
            fields=_update_payload(),
            update_source="oc",
        )
        cache.refresh_from_db()
        self.assertEqual(cache.name, "Original Name")
