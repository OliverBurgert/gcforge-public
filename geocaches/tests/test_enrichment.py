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

    def test_kreisfreie_stadt_with_city_district(self):
        """Munich: has state=Bayern but no county; falls back to city_district."""
        addr = {
            "state": "Bayern",
            "city": "München",
            "city_district": "Maxvorstadt",
            "country": "Deutschland",
        }
        result = _extract_address_fields(addr)
        self.assertEqual(result["state"], "Bayern")
        self.assertEqual(result["county"], "Maxvorstadt")

    def test_kreisfreie_stadt_with_borough(self):
        """Kreisfreie Stadt with borough fallback when city_district absent."""
        addr = {
            "state": "Bayern",
            "city": "Nürnberg",
            "borough": "Mitte",
            "country": "Deutschland",
        }
        result = _extract_address_fields(addr)
        self.assertEqual(result["state"], "Bayern")
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


if __name__ == "__main__":
    unittest.main()
