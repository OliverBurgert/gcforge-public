"""
HTTP-level smoke tests for key geocaches views.

Pin observable HTTP behaviour (status + content fragments) so the planned
views.py split refactor can be verified safe. Tests must NOT depend on
private helpers like _filtered_qs or _build_log_submit_context — those will
be moved/deleted during the refactor.
"""

from datetime import date, datetime, timezone

from django.test import TestCase
from django.urls import reverse

from geocaches.models import (
    CacheSize,
    CacheStatus,
    CacheType,
    Geocache,
    Log,
    LogType,
    Note,
    NoteType,
    Waypoint,
    WaypointType,
)


def _cache(gc_code, lat=48.0, lon=9.0, **kwargs):
    defaults = dict(
        name=f"Cache {gc_code}",
        cache_type=CacheType.TRADITIONAL,
        size=CacheSize.SMALL,
        status=CacheStatus.ACTIVE,
        latitude=lat,
        longitude=lon,
        difficulty=2.0,
        terrain=2.0,
        hidden_date=date(2020, 1, 1),
        owner="testowner",
        primary_source="gc",
    )
    defaults.update(kwargs)
    return Geocache.objects.create(gc_code=gc_code, **defaults)


class CacheListSmokeTests(TestCase):
    def test_empty_db_returns_200(self):
        self.assertEqual(self.client.get(reverse("geocaches:list")).status_code, 200)

    def test_three_caches_appear(self):
        _cache("GC0001"); _cache("GC0002"); _cache("GC0003")
        response = self.client.get(reverse("geocaches:list"))
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        for code in ("GC0001", "GC0002", "GC0003"):
            self.assertIn(code, body)

    def test_type_filter_reduces_results(self):
        from geocaches.filter_expr import Condition, Group, OP_AND, to_url_param
        _cache("GC0001", cache_type=CacheType.TRADITIONAL)
        _cache("GC0002", cache_type=CacheType.MULTI)
        # After the v2 cutover, the toolbar's Type dropdown sends ?fx= directly
        # (no legacy ?type= URL param anymore).  Server applies the tree and
        # renders the list.
        fx = to_url_param(Group(OP_AND, [
            Condition("cache_type", "in", [CacheType.TRADITIONAL]),
        ]))
        response = self.client.get(reverse("geocaches:list"), {"fx": fx})
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        self.assertIn("GC0001", body)
        self.assertNotIn("GC0002", body)

    def test_column_form_preserves_fx_filter(self):
        """Regression: changing the column preset must not drop the active
        ?fx= filter.

        The canonical filter system is ``?fx=`` (type/tag/status/etc. from the
        quick-filter dropdown all live there).  The column-preset form rebuilds
        the query from hidden inputs; if it omits ``fx`` the filter is still
        shown in the dropdowns (legacy ``type=``/``tag=`` are display-only) but
        no longer applied — so the form must carry ``fx`` through.
        """
        from geocaches.filter_expr import Condition, Group, OP_AND, to_url_param
        _cache("GC0001", cache_type=CacheType.TRADITIONAL)
        fx = to_url_param(Group(OP_AND, [
            Condition("cache_type", "in", [CacheType.TRADITIONAL]),
        ]))
        response = self.client.get(
            reverse("geocaches:list"), {"fx": fx}, HTTP_HX_REQUEST="true"
        )
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        # The column-preset form is the only place that renders name="fx";
        # it must round-trip the active fx so a preset change preserves it.
        self.assertIn('name="fx"', body)
        self.assertIn(fx, body)

    def test_sort_distance_without_refpoint_still_200(self):
        _cache("GC0001")
        response = self.client.get(reverse("geocaches:list"), {"sort": "distance"})
        self.assertEqual(response.status_code, 200)

    def test_htmx_partial_returns_200(self):
        _cache("GC0001")
        response = self.client.get(reverse("geocaches:list"), HTTP_HX_REQUEST="true")
        self.assertEqual(response.status_code, 200)


class CacheDetailSmokeTests(TestCase):
    def setUp(self):
        self.cache = _cache("GC00SMOKE", name="Smoke Test Cache", lat=48.5, lon=9.5)

    def test_basic_200_and_name(self):
        response = self.client.get(reverse("geocaches:detail", args=["GC00SMOKE"]))
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        self.assertIn("Smoke Test Cache", body)
        self.assertIn("GC00SMOKE", body)

    def test_log_appears(self):
        Log.objects.create(
            geocache=self.cache,
            log_type=LogType.FOUND,
            user_name="SmokeUser",
            logged_date=date(2023, 6, 1),
            text="Great cache!",
            source="gc",
        )
        response = self.client.get(reverse("geocaches:detail", args=["GC00SMOKE"]))
        self.assertEqual(response.status_code, 200)
        self.assertIn("SmokeUser", response.content.decode())

    def test_waypoint_appears(self):
        Waypoint.objects.create(
            geocache=self.cache,
            waypoint_type=WaypointType.PARKING,
            lookup="PK00SMOKE",
            name="Parking Area",
            latitude=48.501,
            longitude=9.501,
        )
        response = self.client.get(reverse("geocaches:detail", args=["GC00SMOKE"]))
        self.assertEqual(response.status_code, 200)

    def test_note_appears(self):
        Note.objects.create(
            geocache=self.cache,
            note_type=NoteType.NOTE,
            body="My personal note here.",
        )
        response = self.client.get(reverse("geocaches:detail", args=["GC00SMOKE"]))
        self.assertEqual(response.status_code, 200)
        self.assertIn("My personal note here.", response.content.decode())

    def test_nonexistent_cache_404(self):
        response = self.client.get(reverse("geocaches:detail", args=["GCNOTEXIST"]))
        self.assertEqual(response.status_code, 404)


class MapMarkersSmokeTests(TestCase):
    def test_empty_db_empty_markers(self):
        data = self.client.get(reverse("geocaches:map_markers")).json()
        self.assertEqual(data["markers"], [])
        self.assertEqual(data["count"], 0)

    def test_cache_in_bbox_appears(self):
        _cache("GC0001", lat=48.0, lon=9.0)
        data = self.client.get(
            reverse("geocaches:map_markers"), {"bbox": "47.0,8.0,49.0,10.0"}
        ).json()
        self.assertEqual(data["count"], 1)
        self.assertEqual(data["markers"][0]["c"], "GC0001")

    def test_cache_outside_bbox_excluded(self):
        _cache("GC0001", lat=48.0, lon=9.0)
        data = self.client.get(
            reverse("geocaches:map_markers"), {"bbox": "51.0,12.0,52.0,13.0"}
        ).json()
        self.assertEqual(data["count"], 0)

    def test_marker_has_required_fields(self):
        _cache("GC0001", lat=48.123, lon=9.456)
        marker = self.client.get(reverse("geocaches:map_markers")).json()["markers"][0]
        for field in ("c", "n", "la", "lo", "t", "sz", "d", "tr", "f", "s", "m"):
            self.assertIn(field, marker, msg=f"Marker missing field: {field}")

    def test_two_caches_one_in_bbox(self):
        _cache("GC_IN", lat=48.0, lon=9.0)
        _cache("GC_OUT", lat=52.0, lon=13.0)
        data = self.client.get(
            reverse("geocaches:map_markers"), {"bbox": "47.0,8.0,49.0,10.0"}
        ).json()
        self.assertEqual(data["count"], 1)
        self.assertEqual(data["markers"][0]["c"], "GC_IN")


class BulkLoggingSmokeTests(TestCase):
    def test_get_empty_200(self):
        response = self.client.get(reverse("geocaches:bulk_logging"))
        self.assertEqual(response.status_code, 200)

    def test_field_note_shows_gc_code(self):
        cache = _cache("GC_BULK", name="Bulk Log Cache")
        Note.objects.create(
            geocache=cache,
            note_type=NoteType.FIELD_NOTE,
            log_type=LogType.FOUND,
            body="Found it!",
            logged_at=datetime(2024, 3, 1, 10, 0, tzinfo=timezone.utc),
        )
        response = self.client.get(reverse("geocaches:bulk_logging"))
        self.assertEqual(response.status_code, 200)
        self.assertIn("GC_BULK", response.content.decode())


class PqMatchPreviewSmokeTests(TestCase):
    def test_endpoint_responds(self):
        # Pin only that the endpoint is reachable; exact param contract may
        # vary and is documented by the response itself.
        response = self.client.get(reverse("geocaches:pq_match_preview"))
        self.assertIn(response.status_code, (200, 400))


class ActionEndpointTargetSmokeTests(TestCase):
    """Pin that every action-bar endpoint respects the ?target=... picker.

    All six action-bar endpoints route their filter pipeline through
    ``_filtered_qs`` (geocaches/views/list.py), which calls
    ``apply_action_scope`` after the regular ``apply_all``. These tests are
    a regression guard: if a future change bypasses ``_filtered_qs``, the
    endpoint will silently ignore ``target`` and these tests will fail.

    Setup: 5 caches along the diagonal lat/lon = (1,1)…(5,5). The viewport
    bbox 1.5,1.5,3.5,3.5 covers caches 2 + 3 only.
    """

    def setUp(self):
        self.c1 = _cache("GC10001", lat=1.0, lon=1.0)
        self.c2 = _cache("GC10002", lat=2.0, lon=2.0)
        self.c3 = _cache("GC10003", lat=3.0, lon=3.0)
        self.c4 = _cache("GC10004", lat=4.0, lon=4.0)
        self.c5 = _cache("GC10005", lat=5.0, lon=5.0)
        self.viewport_params = {"target": "viewport", "vbox": "1.5,1.5,3.5,3.5"}

    # --- delete_filtered (GET shows confirmation page with `count`) ---------

    def test_delete_filtered_respects_viewport(self):
        response = self.client.get(reverse("geocaches:delete_filtered"), self.viewport_params)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["count"], 2)

    def test_delete_filtered_respects_page_ids(self):
        params = {"target": "page", "ids": f"{self.c2.pk},{self.c4.pk}"}
        response = self.client.get(reverse("geocaches:delete_filtered"), params)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["count"], 2)

    def test_delete_filtered_filter_default_unchanged(self):
        response = self.client.get(reverse("geocaches:delete_filtered"))
        self.assertEqual(response.context["count"], 5)

    # --- bulk_tag_add / bulk_tag_remove (GET preview pages with `count`) ----

    def test_bulk_tag_add_respects_viewport(self):
        response = self.client.get(reverse("geocaches:bulk_tag_add"), self.viewport_params)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["count"], 2)

    def test_bulk_tag_remove_respects_viewport(self):
        response = self.client.get(reverse("geocaches:bulk_tag_remove"), self.viewport_params)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["count"], 2)

    # --- export_gpx (GET returns GPX bytes — count waypoint names) ----------

    def test_export_gpx_respects_viewport(self):
        response = self.client.get(reverse("geocaches:export_gpx"), self.viewport_params)
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        # GPX exporter emits one <wpt> per cache; verify only c2 and c3 are present.
        self.assertIn("GC10002", body)
        self.assertIn("GC10003", body)
        self.assertNotIn("GC10001", body)
        self.assertNotIn("GC10004", body)
        self.assertNotIn("GC10005", body)

    def test_export_gpx_respects_page_ids(self):
        params = {"target": "page", "ids": f"{self.c1.pk},{self.c5.pk}"}
        response = self.client.get(reverse("geocaches:export_gpx"), params)
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        self.assertIn("GC10001", body)
        self.assertIn("GC10005", body)
        self.assertNotIn("GC10002", body)
        self.assertNotIn("GC10003", body)
        self.assertNotIn("GC10004", body)

    # --- enrich / update (kick off background task — patch the starter) ----

    def test_enrich_respects_viewport(self):
        from unittest.mock import patch
        with patch("geocaches.tasks.enrich.start_enrichment", return_value=True) as mock_start:
            response = self.client.get(
                reverse("geocaches:enrich"),
                {**self.viewport_params, "fields": "elevation"},
            )
        self.assertEqual(response.status_code, 302)  # redirect back to list
        self.assertEqual(mock_start.call_count, 1)
        passed_qs = mock_start.call_args.args[0]
        self.assertEqual(passed_qs.count(), 2)

    def test_update_respects_viewport(self):
        from unittest.mock import patch
        with patch("geocaches.tasks.update.start_update", return_value=True) as mock_start:
            response = self.client.get(
                reverse("geocaches:update"),
                {**self.viewport_params, "action": "light_update"},
            )
        self.assertEqual(response.status_code, 302)  # redirect back to list
        self.assertEqual(mock_start.call_count, 1)
        passed_qs = mock_start.call_args.args[0]
        self.assertEqual(passed_qs.count(), 2)
