"""Tests for the trackables-list view-layer additions:

- _apply_filters()'s tag filter (named tag / NO_TAG_VALUE "no tag at all")
  and the tracking_code search field.
- trackable_sync_one()'s comma-separated `tag` field (Refresh filtered can
  apply both "discovered" and "moved" to the same TB in one call).
- trackable_refresh_filtered_plan() — resolves the currently-filtered refs
  and intersects them against the account's discovered/moved history.
- trackable_log_by_code() (geocaches.views.trackables) — the "Log TB"
  bulk button's Discover/Retrieve endpoint; validation is tested here,
  submit_standalone_tb_action()'s own behaviour is covered in
  gcprivate/tests/test_trackable_log_chain.py.

No live network calls — gc_access.get_trackable_api_client() and
submit_standalone_tb_action() are mocked throughout.
"""
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse

from geocaches.models import Tag, Trackable
from geocaches.views.trackable_list import NO_TAG_VALUE, _apply_filters


class ApplyFiltersTagTests(TestCase):
    def setUp(self):
        self.tagged = Trackable.objects.create(reference_code="TB1AAAA", name="Tagged")
        self.untagged = Trackable.objects.create(reference_code="TB2BBBB", name="Untagged")
        tag, _ = Tag.objects.get_or_create(name="discovered")
        self.tagged.tags.add(tag)

    def _fv(self, **overrides):
        base = {"q": "", "state": "", "kind": "", "series": "", "tag": "", "mine": "", "held": ""}
        base.update(overrides)
        return base

    def test_no_tag_filter_returns_only_untagged(self):
        qs = _apply_filters(Trackable.objects.all(), self._fv(tag=NO_TAG_VALUE), "")
        self.assertEqual(list(qs.values_list("reference_code", flat=True)), ["TB2BBBB"])

    def test_named_tag_filter_returns_only_tagged(self):
        qs = _apply_filters(Trackable.objects.all(), self._fv(tag="discovered"), "")
        self.assertEqual(list(qs.values_list("reference_code", flat=True)), ["TB1AAAA"])

    def test_empty_tag_filter_returns_everything(self):
        qs = _apply_filters(Trackable.objects.all(), self._fv(tag=""), "")
        self.assertEqual(
            sorted(qs.values_list("reference_code", flat=True)), ["TB1AAAA", "TB2BBBB"],
        )


class ApplyFiltersTrackingCodeSearchTests(TestCase):
    def test_q_matches_tracking_code(self):
        Trackable.objects.create(reference_code="TB1AAAA", name="Alpha", tracking_code="CVWPRJ")
        Trackable.objects.create(reference_code="TB2BBBB", name="Beta", tracking_code="XYZABC")
        fv = {"q": "CVWPRJ", "state": "", "kind": "", "series": "", "tag": "", "mine": "", "held": ""}
        qs = _apply_filters(Trackable.objects.all(), fv, "")
        self.assertEqual(list(qs.values_list("reference_code", flat=True)), ["TB1AAAA"])


class TrackableSyncOneMultiTagTests(TestCase):
    def test_comma_separated_tags_are_all_applied(self):
        tb = Trackable.objects.create(reference_code="TB1AAAA", name="Both")
        with patch("geocaches.services.trackable_sync.sync_trackable", return_value=tb):
            resp = self.client.post(
                reverse("geocaches:trackable_sync_one"),
                {"ref": "TB1AAAA", "tag": "discovered,moved"},
            )
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["ok"])
        self.assertEqual(
            sorted(tb.tags.values_list("name", flat=True)), ["discovered", "moved"],
        )

    def test_single_tag_still_works(self):
        tb = Trackable.objects.create(reference_code="TB1AAAA", name="Single")
        with patch("geocaches.services.trackable_sync.sync_trackable", return_value=tb):
            resp = self.client.post(
                reverse("geocaches:trackable_sync_one"),
                {"ref": "TB1AAAA", "tag": "discovered"},
            )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(list(tb.tags.values_list("name", flat=True)), ["discovered"])

    def test_no_tag_field_applies_nothing(self):
        tb = Trackable.objects.create(reference_code="TB1AAAA", name="None")
        with patch("geocaches.services.trackable_sync.sync_trackable", return_value=tb):
            resp = self.client.post(
                reverse("geocaches:trackable_sync_one"), {"ref": "TB1AAAA"},
            )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(list(tb.tags.values_list("name", flat=True)), [])


class RefreshFilteredPlanTests(TestCase):
    def setUp(self):
        # "Marked" distinguishes the in-scope set from TB9ZZZZ below — "TB"
        # alone would match every reference_code and defeat the point of
        # these tests (that the plan intersects with the *filtered* set, not
        # just the account's whole discovered/moved history).
        Trackable.objects.create(reference_code="TB1AAAA", name="Marked discovered one")
        Trackable.objects.create(reference_code="TB2BBBB", name="Marked moved one")
        Trackable.objects.create(reference_code="TB3CCCC", name="Marked neither")
        # Not part of the filtered set — must not leak into the plan.
        Trackable.objects.create(reference_code="TB9ZZZZ", name="Unrelated excluded")

    def test_plan_returns_filtered_refs_with_discovered_moved_split(self):
        fake_client = type("FakeClient", (), {
            "get_my_discovered_refs": lambda self: ["TB1AAAA", "TB9ZZZZ"],
            "get_my_moved_refs": lambda self: ["TB2BBBB"],
        })()
        with patch("geocaches.sync.gc_access.get_trackable_api_client", return_value=fake_client):
            resp = self.client.post(
                reverse("geocaches:trackable_refresh_filtered_plan"), {"q": "Marked"},
            )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body["ok"])
        self.assertEqual(
            sorted(body["refs"]), ["TB1AAAA", "TB2BBBB", "TB3CCCC"],
        )
        # TB9ZZZZ is in the discovered history but not in the filtered set —
        # must not appear in "discovered" even though get_my_discovered_refs()
        # returned it.
        self.assertEqual(body["discovered"], ["TB1AAAA"])
        self.assertEqual(body["moved"], ["TB2BBBB"])

    def test_plan_respects_the_filter_not_just_discovered_moved_history(self):
        fake_client = type("FakeClient", (), {
            "get_my_discovered_refs": lambda self: ["TB1AAAA"],
            "get_my_moved_refs": lambda self: [],
        })()
        with patch("geocaches.sync.gc_access.get_trackable_api_client", return_value=fake_client):
            resp = self.client.post(
                reverse("geocaches:trackable_refresh_filtered_plan"), {"q": "TB1AAAA"},
            )
        body = resp.json()
        self.assertEqual(body["refs"], ["TB1AAAA"])

    def test_tag_lookup_failure_degrades_gracefully(self):
        with patch("geocaches.sync.gc_access.get_trackable_api_client", side_effect=RuntimeError("no token")):
            resp = self.client.post(
                reverse("geocaches:trackable_refresh_filtered_plan"), {"q": "TB"},
            )
        body = resp.json()
        self.assertTrue(body["ok"])
        self.assertEqual(sorted(body["refs"]), ["TB1AAAA", "TB2BBBB", "TB3CCCC", "TB9ZZZZ"])
        self.assertEqual(body["discovered"], [])
        self.assertEqual(body["moved"], [])

    def test_no_matches_returns_empty_refs(self):
        resp = self.client.post(
            reverse("geocaches:trackable_refresh_filtered_plan"), {"q": "NOPE"},
        )
        body = resp.json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["refs"], [])


class TrackableLogByCodeViewTests(TestCase):
    """View-level validation for geocaches.views.trackables.trackable_log_by_code.

    submit_standalone_tb_action()'s own behaviour (verify/submit/tag/persist)
    is covered by gcprivate/tests/test_trackable_log_chain.py's
    StandaloneTbActionTests — these only check the view's own request
    handling and response shape.
    """

    def _url(self):
        return reverse("geocaches:trackable_log_by_code")

    def test_missing_code_is_rejected(self):
        resp = self.client.post(self._url(), {"action": "discover", "text": "hi"})
        body = resp.json()
        self.assertFalse(body["ok"])
        self.assertIn("empty code", body["error"])

    def test_missing_text_is_rejected(self):
        resp = self.client.post(self._url(), {"action": "discover", "tracking_code": "CVWPRJ"})
        body = resp.json()
        self.assertFalse(body["ok"])
        self.assertIn("log text required", body["error"])

    def test_unsupported_action_is_rejected(self):
        resp = self.client.post(
            self._url(), {"action": "grab", "tracking_code": "CVWPRJ", "text": "hi"},
        )
        body = resp.json()
        self.assertFalse(body["ok"])
        self.assertIn("unsupported action", body["error"])

    def test_success_delegates_to_submit_standalone_tb_action(self):
        from geocaches.sync.log_submit import TrackableLogSubmitResult

        result = TrackableLogSubmitResult(ref_code="TB7WZ44", action="discover", success=True)
        with patch(
            "geocaches.sync.log_submit.submit_standalone_tb_action", return_value=result,
        ) as mock_submit:
            resp = self.client.post(
                self._url(),
                {"action": "discover", "tracking_code": "cvwprj", "text": "Found it"},
            )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["ref_code"], "TB7WZ44")
        mock_submit.assert_called_once_with("discover", "cvwprj", "Found it")

    def test_failure_result_is_reported(self):
        from geocaches.sync.log_submit import TrackableLogSubmitResult

        result = TrackableLogSubmitResult(
            ref_code="", action="discover", success=False, error="verify failed: nope",
        )
        with patch(
            "geocaches.sync.log_submit.submit_standalone_tb_action", return_value=result,
        ):
            resp = self.client.post(
                self._url(),
                {"action": "discover", "tracking_code": "NOPE99", "text": "hi"},
            )
        body = resp.json()
        self.assertFalse(body["ok"])
        self.assertIn("verify failed", body["error"])
