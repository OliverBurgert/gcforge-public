"""Unit tests for geocaches.enrichment._extract_address_fields.

Pure unit tests — no database, no Django setup required.
"""
import unittest

from geocaches.enrichment import _extract_address_fields


class TestExtractAddressFields(unittest.TestCase):

    # ------------------------------------------------------------------
    # Normal cases
    # ------------------------------------------------------------------

    def test_normal_state_and_county(self):
        """Standard German Landkreis: both state and county present."""
        addr = {
            "state": "Baden-Württemberg",
            "county": "Landkreis Tübingen",
            "country": "Deutschland",
        }
        result = _extract_address_fields(addr)
        self.assertEqual(result["state"], "Baden-Württemberg")
        self.assertEqual(result["county"], "Landkreis Tübingen")

    def test_normal_us_location(self):
        """US state + county both present."""
        addr = {
            "state": "California",
            "county": "Los Angeles County",
            "country": "United States",
        }
        result = _extract_address_fields(addr)
        self.assertEqual(result["state"], "California")
        self.assertEqual(result["county"], "Los Angeles County")

    # ------------------------------------------------------------------
    # City-states (Berlin, Hamburg) — state falls back to city,
    # county falls back to city_district
    # ------------------------------------------------------------------

    def test_berlin_city_state(self):
        """Berlin: no 'state' key — falls back to city; county from city_district."""
        addr = {
            "city": "Berlin",
            "city_district": "Mitte",
            "country": "Deutschland",
        }
        result = _extract_address_fields(addr)
        self.assertEqual(result["state"], "Berlin")
        self.assertEqual(result["county"], "Mitte")

    def test_berlin_pankow_district(self):
        """Berlin Pankow district — verifies district name appears in county, not city."""
        addr = {
            "city": "Berlin",
            "city_district": "Pankow",
            "country": "Deutschland",
        }
        result = _extract_address_fields(addr)
        self.assertEqual(result["state"], "Berlin")
        self.assertEqual(result["county"], "Pankow")
        # This was a real bug: county must be the district, not the city
        self.assertNotEqual(result["county"], "Berlin")

    def test_hamburg_city_state(self):
        """Hamburg: similar city-state structure to Berlin."""
        addr = {
            "city": "Hamburg",
            "city_district": "Altona",
            "country": "Deutschland",
        }
        result = _extract_address_fields(addr)
        self.assertEqual(result["state"], "Hamburg")
        self.assertEqual(result["county"], "Altona")

    # ------------------------------------------------------------------
    # Kreisfreie Stadt (e.g. Munich) — has state but no county
    # ------------------------------------------------------------------

    def test_kreisfreie_stadt_uses_city_not_district(self):
        """Munich: state=Bayern, no county — the city IS the county-level unit
        (kreisfreie Stadt), so use "München", not the Stadtbezirk."""
        addr = {
            "state": "Bayern",
            "city": "München",
            "city_district": "Maxvorstadt",
            "country": "Deutschland",
        }
        result = _extract_address_fields(addr)
        self.assertEqual(result["state"], "Bayern")
        self.assertEqual(result["county"], "München")

    def test_kreisfreie_stadt_uses_city_over_borough(self):
        """Kreisfreie Stadt: city wins over the borough fallback too."""
        addr = {
            "state": "Bayern",
            "city": "Nürnberg",
            "borough": "Mitte",
            "country": "Deutschland",
        }
        result = _extract_address_fields(addr)
        self.assertEqual(result["state"], "Bayern")
        self.assertEqual(result["county"], "Nürnberg")

    def test_kreisfreie_stadt_city_beats_municipality(self):
        """Chemnitz: Nominatim attaches a neighbouring Gemeinde as
        ``municipality`` ("Lichtenau") next to ``city`` — the kreisfreie-Stadt
        city must win, not the finer municipality."""
        addr = {
            "state": "Sachsen",
            "city": "Chemnitz",
            "municipality": "Lichtenau",
            "country": "Deutschland",
        }
        self.assertEqual(_extract_address_fields(addr)["county"], "Chemnitz")

    def test_dc_stores_the_ward(self):
        """Washington DC is a city-state where city != state; store the ward
        (Nominatim's borough), like Berlin stores its Bezirk, so the district
        map can break DC down."""
        addr = {
            "state": "District of Columbia",
            "city": "Washington",
            "borough": "Ward 4",
            "country": "United States",
        }
        result = _extract_address_fields(addr)
        self.assertEqual(result["state"], "District of Columbia")
        self.assertEqual(result["county"], "Ward 4")

    def test_kreisfreie_stadt_leipzig(self):
        """Leipzig (Sachsen): city differs from state → county = "Leipzig"."""
        addr = {
            "state": "Sachsen",
            "city": "Leipzig",
            "city_district": "Zentrum",
            "country": "Deutschland",
        }
        self.assertEqual(_extract_address_fields(addr)["county"], "Leipzig")

    def test_city_state_with_explicit_state_key_keeps_district(self):
        """Berlin can return both state=Berlin and city=Berlin; state == city
        marks a city-state, so the Bezirk stays the county-level value."""
        addr = {
            "state": "Berlin",
            "city": "Berlin",
            "city_district": "Mitte",
            "country": "Deutschland",
        }
        result = _extract_address_fields(addr)
        self.assertEqual(result["state"], "Berlin")
        self.assertEqual(result["county"], "Mitte")

    # ------------------------------------------------------------------
    # Washington DC — not a state
    # ------------------------------------------------------------------

    def test_washington_dc(self):
        """Washington DC: no 'state' key; county from city_district."""
        addr = {
            "city": "Washington",
            "city_district": "Ward 2",
            "country": "United States",
            "country_code": "us",
        }
        result = _extract_address_fields(addr)
        # DC is not a US state — city is the best state-level fallback
        self.assertEqual(result["state"], "Washington")
        self.assertEqual(result["county"], "Ward 2")

    # ------------------------------------------------------------------
    # Province fallback (Canada, etc.)
    # ------------------------------------------------------------------

    def test_province_fallback(self):
        """Canada uses 'province' instead of 'state'."""
        addr = {
            "province": "Ontario",
            "county": "Regional Municipality of Waterloo",
            "country": "Canada",
        }
        result = _extract_address_fields(addr)
        self.assertEqual(result["state"], "Ontario")
        self.assertEqual(result["county"], "Regional Municipality of Waterloo")

    def test_province_preferred_over_region(self):
        """Province takes priority over region in the state fallback chain."""
        addr = {
            "province": "British Columbia",
            "region": "Pacific Region",
            "country": "Canada",
        }
        result = _extract_address_fields(addr)
        self.assertEqual(result["state"], "British Columbia")

    # ------------------------------------------------------------------
    # Municipality fallback for county
    # ------------------------------------------------------------------

    def test_municipality_fallback(self):
        """Municipality used when county absent."""
        addr = {
            "state": "Some State",
            "municipality": "Some Municipality",
            "country": "Some Country",
        }
        result = _extract_address_fields(addr)
        self.assertEqual(result["state"], "Some State")
        self.assertEqual(result["county"], "Some Municipality")

    def test_county_preferred_over_municipality(self):
        """County takes priority over municipality."""
        addr = {
            "state": "Bayern",
            "county": "Landkreis München",
            "municipality": "Should Not Appear",
        }
        result = _extract_address_fields(addr)
        self.assertEqual(result["county"], "Landkreis München")

    # ------------------------------------------------------------------
    # Borough fallback for county
    # ------------------------------------------------------------------

    def test_borough_fallback(self):
        """Borough used as last resort for county when nothing else available."""
        addr = {
            "state": "New York",
            "borough": "Manhattan",
            "country": "United States",
        }
        result = _extract_address_fields(addr)
        self.assertEqual(result["state"], "New York")
        self.assertEqual(result["county"], "Manhattan")

    def test_city_district_preferred_over_borough(self):
        """city_district takes priority over borough in county fallback chain."""
        addr = {
            "state": "Some State",
            "city_district": "District A",
            "borough": "Borough B",
        }
        result = _extract_address_fields(addr)
        self.assertEqual(result["county"], "District A")

    # ------------------------------------------------------------------
    # Empty / minimal address
    # ------------------------------------------------------------------

    def test_empty_address(self):
        """Empty dict returns empty strings for both fields."""
        result = _extract_address_fields({})
        self.assertEqual(result["state"], "")
        self.assertEqual(result["county"], "")

    def test_result_has_exactly_two_keys(self):
        """Result dict always has exactly state and county keys."""
        result = _extract_address_fields({"state": "X", "county": "Y"})
        self.assertIn("state", result)
        self.assertIn("county", result)
        self.assertEqual(len(result), 2)

    def test_irrelevant_keys_ignored(self):
        """Unrelated address fields don't affect output."""
        addr = {
            "road": "Hauptstraße",
            "postcode": "72070",
            "country": "Deutschland",
            "country_code": "de",
        }
        result = _extract_address_fields(addr)
        self.assertEqual(result["state"], "")
        self.assertEqual(result["county"], "")

    # ------------------------------------------------------------------
    # Region fallback for state
    # ------------------------------------------------------------------

    def test_region_fallback_for_state(self):
        """Region used when state and province both absent."""
        addr = {
            "region": "Corsica",
            "county": "Haute-Corse",
            "country": "France",
        }
        result = _extract_address_fields(addr)
        self.assertEqual(result["state"], "Corsica")
        self.assertEqual(result["county"], "Haute-Corse")


class TestStripAdminSuffixDE(unittest.TestCase):
    """DE county normalisation: drop the kreisfreie-Stadt "Stadt " prefix but
    keep "Landkreis" so a city and its surrounding Landkreis stay distinct."""

    def test_strips_stadt_prefix(self):
        from geocaches.geo.countries import strip_admin_suffix
        self.assertEqual(
            strip_admin_suffix("Stadt München", "DE", "county"), "München")
        self.assertEqual(
            strip_admin_suffix("Stadt Karlsruhe", "DE", "county"), "Karlsruhe")

    def test_keeps_landkreis_so_it_stays_distinct(self):
        from geocaches.geo.countries import strip_admin_suffix
        # Must NOT collapse onto the same-named kreisfreie Stadt.
        self.assertEqual(
            strip_admin_suffix("Landkreis München", "DE", "county"),
            "Landkreis München")
        self.assertEqual(
            strip_admin_suffix("Landkreis Esslingen", "DE", "county"),
            "Landkreis Esslingen")

    def test_keeps_compound_kreis_names(self):
        from geocaches.geo.countries import strip_admin_suffix
        # "Kreis " as a standalone prefix is not stripped (would maul "Ilm-Kreis").
        self.assertEqual(
            strip_admin_suffix("Ilm-Kreis", "DE", "county"), "Ilm-Kreis")


if __name__ == "__main__":
    unittest.main()


# ---------------------------------------------------------------------------
# Integration tests for enrich_geocache — DB + mocked external calls
# ---------------------------------------------------------------------------

from unittest.mock import patch

from django.test import TestCase

from geocaches.enrichment import enrich_geocache, _needs_work_q
from geocaches.models import CacheSize, CacheStatus, CacheType, Geocache


def _make_cache(**kwargs):
    defaults = dict(
        gc_code="GC11111", name="Test", cache_type=CacheType.TRADITIONAL,
        size=CacheSize.SMALL, status=CacheStatus.ACTIVE,
        latitude=48.5, longitude=9.1,
    )
    defaults.update(kwargs)
    return Geocache.objects.create(**defaults)


class EnrichGeocacheElevationTest(TestCase):
    def test_elevation_filled_from_srtm(self):
        cache = _make_cache()
        with patch("geocaches.enrichment.fetch_elevation", return_value=250.0):
            changed = enrich_geocache(cache, {"elevation"})
        self.assertTrue(changed)
        cache.refresh_from_db()
        self.assertEqual(cache.elevation, 250.0)

    def test_elevation_not_filled_if_already_set(self):
        cache = _make_cache(elevation=100.0)
        with patch("geocaches.enrichment.fetch_elevation") as mock_fetch:
            changed = enrich_geocache(cache, {"elevation"})
        mock_fetch.assert_not_called()
        self.assertFalse(changed)

    def test_elevation_not_filled_if_user_override(self):
        cache = _make_cache(elevation_user=200.0)
        with patch("geocaches.enrichment.fetch_elevation") as mock_fetch:
            changed = enrich_geocache(cache, {"elevation"})
        mock_fetch.assert_not_called()
        self.assertFalse(changed)

    def test_srtm_void_falls_back_to_online(self):
        cache = _make_cache()
        with (
            patch("geocaches.enrichment.fetch_elevation", return_value=None),
            patch("geocaches.enrichment.fetch_elevation_online", return_value=350.0),
        ):
            changed = enrich_geocache(cache, {"elevation"})
        self.assertTrue(changed)
        cache.refresh_from_db()
        self.assertEqual(cache.elevation, 350.0)

    def test_overwrite_mode_re_fetches_existing(self):
        cache = _make_cache(elevation=100.0)
        with patch("geocaches.enrichment.fetch_elevation", return_value=200.0):
            changed = enrich_geocache(cache, {"elevation"}, overwrite={"elevation"})
        self.assertTrue(changed)
        cache.refresh_from_db()
        self.assertEqual(cache.elevation, 200.0)

    def test_stale_elevation_cleared_when_both_sources_return_none(self):
        cache = _make_cache(elevation=100.0)
        with (
            patch("geocaches.enrichment.fetch_elevation", return_value=None),
            patch("geocaches.enrichment.fetch_elevation_online", return_value=None),
        ):
            changed = enrich_geocache(cache, {"elevation"}, overwrite={"elevation"})
        self.assertTrue(changed)
        cache.refresh_from_db()
        self.assertIsNone(cache.elevation)

    def test_no_change_when_both_sources_return_none_fill_mode(self):
        cache = _make_cache()
        with (
            patch("geocaches.enrichment.fetch_elevation", return_value=None),
            patch("geocaches.enrichment.fetch_elevation_online", return_value=None),
        ):
            changed = enrich_geocache(cache, {"elevation"})
        self.assertFalse(changed)


class EnrichGeocacheLocationTest(TestCase):
    def test_location_filled(self):
        cache = _make_cache()
        loc = {"iso_country_code": "DE", "country": "Germany", "state": "Bayern", "county": "München"}
        with patch("geocaches.enrichment.fetch_location", return_value=loc):
            changed = enrich_geocache(cache, {"location"})
        self.assertTrue(changed)
        cache.refresh_from_db()
        self.assertEqual(cache.iso_country_code, "DE")
        self.assertEqual(cache.state, "Bayern")

    def test_location_not_fetched_if_complete(self):
        cache = _make_cache(iso_country_code="DE", country="Germany", state="Bayern", county="Munich")
        with patch("geocaches.enrichment.fetch_location") as mock_fetch:
            changed = enrich_geocache(cache, {"location"})
        mock_fetch.assert_not_called()
        self.assertFalse(changed)

    def test_location_empty_response_no_change(self):
        cache = _make_cache()
        with patch("geocaches.enrichment.fetch_location", return_value={}):
            changed = enrich_geocache(cache, {"location"})
        self.assertFalse(changed)

    def test_location_overwrite_mode(self):
        cache = _make_cache(iso_country_code="FR", country="France", state="IDF", county="Paris")
        loc = {"iso_country_code": "DE", "country": "Germany", "state": "Bayern", "county": "München"}
        with patch("geocaches.enrichment.fetch_location", return_value=loc):
            changed = enrich_geocache(cache, {"location"}, overwrite={"location"})
        self.assertTrue(changed)
        cache.refresh_from_db()
        self.assertEqual(cache.iso_country_code, "DE")


class NeedsWorkQTest(TestCase):
    def test_elevation_fill_mode(self):
        cache_needs = _make_cache(gc_code="GC11111")  # no elevation
        cache_done = _make_cache(gc_code="GC22222", elevation=100.0)
        q = _needs_work_q({"elevation"}, set())
        result = list(Geocache.objects.filter(q).values_list("gc_code", flat=True))
        self.assertIn("GC11111", result)
        self.assertNotIn("GC22222", result)

    def test_elevation_overwrite_mode(self):
        cache_user_override = _make_cache(gc_code="GC11111", elevation_user=500.0)
        cache_normal = _make_cache(gc_code="GC22222", elevation=100.0)
        q = _needs_work_q({"elevation"}, {"elevation"})
        result = list(Geocache.objects.filter(q).values_list("gc_code", flat=True))
        self.assertIn("GC22222", result)
        self.assertNotIn("GC11111", result)  # user override excluded

    def test_location_fill_mode(self):
        cache_missing = _make_cache(gc_code="GC11111")  # no country
        cache_complete = _make_cache(
            gc_code="GC22222", iso_country_code="DE", country="Germany",
            state="Bayern", county="Munich",
        )
        q = _needs_work_q({"location"}, set())
        result = list(Geocache.objects.filter(q).values_list("gc_code", flat=True))
        self.assertIn("GC11111", result)
        self.assertNotIn("GC22222", result)
