"""Wire-format tests for /map/markers/ (geocaches/views/map.py).

The endpoint sends column arrays rather than per-marker objects — see
MARKER_FIELDS in the view and _gcfDecodeMarkers in static/js/cache-map.js.
These tests pin the contract both sides rely on: the field order, the
trailing-null trimming, the optional columns, and the gzip negotiation.
"""
import gzip
import json
from datetime import date

from django.test import TestCase
from django.urls import reverse

from geocaches.models import (
    ALStageDetail,
    Adventure,
    CacheSize,
    CacheStatus,
    CacheType,
    CorrectedCoordinates,
    Geocache,
)
from geocaches.tests.marker_payload import decode_markers
from geocaches.views.map import MARKER_FIELDS, MARKER_REQUIRED_FIELDS


def _cache(**overrides):
    defaults = {
        "name": "Test Cache",
        "owner": "Owner",
        "cache_type": CacheType.TRADITIONAL,
        "size": CacheSize.SMALL,
        "status": CacheStatus.ACTIVE,
        "latitude": 48.1234567,
        "longitude": 9.7654321,
        "difficulty": 2.0,
        "terrain": 1.5,
        "short_description": "",
        "long_description": "",
        "hint": "",
        "hidden_date": date(2020, 1, 1),
        "country": "Germany",
        "state": "BW",
        "fav_points": 0,
        "has_trackable": False,
        "primary_source": "gc",
    }
    defaults.update(overrides)
    return Geocache.objects.create(**defaults)


class MarkerPayloadShapeTests(TestCase):
    def _payload(self, **params):
        return self.client.get(reverse("geocaches:map_markers"), params).json()

    def test_payload_has_fields_rows_and_count(self):
        _cache(gc_code="GCSHAPE")
        payload = self._payload()
        self.assertEqual(payload["fields"], list(MARKER_FIELDS))
        self.assertEqual(payload["count"], 1)
        self.assertEqual(len(payload["rows"]), 1)

    def test_plain_cache_row_stops_after_the_required_columns(self):
        # Nothing optional is set, so every trailing null is trimmed away.
        _cache(gc_code="GCPLAIN")
        row = self._payload()["rows"][0]
        self.assertEqual(len(row), MARKER_REQUIRED_FIELDS)

    def test_required_columns_carry_the_expected_values(self):
        _cache(gc_code="GCVALS", name="A Name")
        marker = decode_markers(self.client.get(reverse("geocaches:map_markers")))[0]
        self.assertEqual(marker["c"], "GCVALS")
        self.assertEqual(marker["n"], "A Name")
        self.assertEqual(marker["la"], 48.123457)  # rounded to 6 decimals
        self.assertEqual(marker["lo"], 9.765432)
        self.assertEqual(marker["t"], "T")
        self.assertEqual(marker["sz"], "S")
        self.assertEqual(marker["d"], 2.0)
        self.assertEqual(marker["tr"], 1.5)
        self.assertIs(marker["f"], False)
        self.assertEqual(marker["s"], "A")
        self.assertIs(marker["m"], False)

    def test_name_is_truncated_to_60_characters(self):
        _cache(gc_code="GCLONG", name="x" * 80)
        marker = decode_markers(self.client.get(reverse("geocaches:map_markers")))[0]
        self.assertEqual(len(marker["n"]), 60)

    def test_completed_counts_as_found(self):
        _cache(gc_code="GCDONE", cache_type=CacheType.LAB, completed=True)
        marker = decode_markers(self.client.get(reverse("geocaches:map_markers")))[0]
        self.assertIs(marker["f"], True)

    def test_dnf_column_only_set_when_not_found(self):
        _cache(gc_code="GCDNF", dnf=True)
        _cache(gc_code="GCDNFF", dnf=True, found=True)
        markers = {
            m["c"]: m
            for m in decode_markers(self.client.get(reverse("geocaches:map_markers")))
        }
        self.assertIs(markers["GCDNF"]["dnf"], True)
        self.assertIsNone(markers["GCDNFF"].get("dnf"))

    def test_oc_column_set_and_gc_only_for_dual_code_caches(self):
        _cache(oc_code="OCONLY", primary_source="oc")
        _cache(gc_code="GCDUAL", oc_code="OCDUAL")
        markers = {
            m["c"]: m
            for m in decode_markers(self.client.get(reverse("geocaches:map_markers")))
        }
        self.assertEqual(markers["OCONLY"]["oc"], "OCONLY")
        self.assertIsNone(markers["OCONLY"].get("gc"))
        self.assertEqual(markers["GCDUAL"]["oc"], "OCDUAL")
        self.assertEqual(markers["GCDUAL"]["gc"], "GCDUAL")

    def test_corrected_coordinates_ride_along_rounded(self):
        cache = _cache(gc_code="GCCORR")
        CorrectedCoordinates.objects.create(
            geocache=cache, latitude=47.1234567, longitude=8.7654321,
        )
        marker = decode_markers(self.client.get(reverse("geocaches:map_markers")))[0]
        self.assertEqual(marker["cla"], 47.123457)
        self.assertEqual(marker["clo"], 8.765432)

    def test_adventure_columns_for_parent_and_stage(self):
        adventure = Adventure.objects.create(
            code="LCADV", title="Adv", latitude=48.5, longitude=9.1,
        )
        parent = _cache(al_code="LCADV", cache_type=CacheType.LAB, primary_source="al")
        parent.adventure = adventure
        parent.is_al_parent = True
        parent.save()
        stage = _cache(al_code="LCADV-1", cache_type=CacheType.LAB, primary_source="al")
        stage.adventure = adventure
        stage.save()
        ALStageDetail.objects.create(
            geocache=stage, stage_number=1, geofencing_radius=30,
        )

        markers = {
            m["c"]: m
            for m in decode_markers(self.client.get(reverse("geocaches:map_markers")))
        }
        self.assertEqual(markers["LCADV"]["aid"], adventure.pk)
        self.assertIsNone(markers["LCADV"].get("sn"))
        self.assertIs(markers["LCADV"]["alp"], True)
        self.assertEqual(markers["LCADV-1"]["aid"], adventure.pk)
        self.assertEqual(markers["LCADV-1"]["sn"], 1)
        self.assertEqual(markers["LCADV-1"]["gr"], 30)
        # A stage carries the adventure id but never the parent flag — this is
        # the signal the client uses to tell parent and stage apart.
        self.assertIsNone(markers["LCADV-1"].get("alp"))

    def test_al_parent_flag_trims_away_for_a_plain_stage_with_no_other_optionals(self):
        # Regression guard for the trailing-null trimming logic: "alp" is the
        # last column in MARKER_FIELDS, so a stage row with nothing else set
        # should trim all the way back down to just past "aid".
        adventure = Adventure.objects.create(
            code="LCFLAG", title="Adv", latitude=48.5, longitude=9.1,
        )
        parent = _cache(al_code="LCFLAG", cache_type=CacheType.LAB, primary_source="al")
        parent.adventure = adventure
        parent.is_al_parent = True
        parent.save()
        stage = _cache(al_code="LCFLAG-1", cache_type=CacheType.LAB, primary_source="al")
        stage.adventure = adventure
        stage.save()

        response = self.client.get(reverse("geocaches:map_markers"))
        raw_rows = {row[0]: row for row in response.json()["rows"]}
        self.assertEqual(len(raw_rows["LCFLAG-1"]), MARKER_REQUIRED_FIELDS + 1)  # required + aid

        markers = {
            m["c"]: m
            for m in decode_markers(self.client.get(reverse("geocaches:map_markers")))
        }
        self.assertIs(markers["LCFLAG"]["alp"], True)
        self.assertIsNone(markers["LCFLAG-1"].get("alp"))


class MarkerPayloadEncodingTests(TestCase):
    def setUp(self):
        _cache(gc_code="GCENC")

    def test_uncompressed_when_client_does_not_accept_gzip(self):
        response = self.client.get(reverse("geocaches:map_markers"))
        self.assertEqual(response["Content-Type"], "application/json")
        self.assertNotIn("Content-Encoding", response)
        self.assertIn("Accept-Encoding", response["Vary"])
        self.assertEqual(json.loads(response.content)["count"], 1)

    def test_gzipped_when_the_client_accepts_it(self):
        response = self.client.get(
            reverse("geocaches:map_markers"), HTTP_ACCEPT_ENCODING="gzip, deflate, br",
        )
        self.assertEqual(response["Content-Encoding"], "gzip")
        self.assertIn("Accept-Encoding", response["Vary"])
        payload = json.loads(gzip.decompress(response.content))
        self.assertEqual(payload["count"], 1)
        self.assertEqual(payload["fields"], list(MARKER_FIELDS))

    def test_json_has_no_separator_padding(self):
        response = self.client.get(reverse("geocaches:map_markers"))
        self.assertNotIn(b", ", response.content)
        self.assertNotIn(b'": ', response.content)
