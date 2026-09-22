from datetime import date, timedelta

from django.test import TestCase
from django.urls import reverse

from geocaches.models import CacheType, Geocache, IgnoreListEntry

URL = reverse("geocaches:tools_remove_unattended_past_events")
YESTERDAY = date.today() - timedelta(days=1)
TODAY = date.today()
TOMORROW = date.today() + timedelta(days=1)


def _event(gc_code, hidden_date, found=False, cache_type=CacheType.EVENT):
    return Geocache.objects.create(
        gc_code=gc_code,
        name=f"Event {gc_code}",
        cache_type=cache_type,
        latitude=51.0,
        longitude=10.0,
        hidden_date=hidden_date,
        found=found,
    )


class RemoveUnattendedPastEventsGetTests(TestCase):

    def test_get_empty(self):
        resp = self.client.get(URL)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "No past unattended events found")

    def test_get_shows_only_past_unattended(self):
        past_unattended = _event("GC0001", YESTERDAY, found=False)
        _event("GC0002", YESTERDAY, found=True)   # attended — must not appear
        _event("GC0003", TOMORROW, found=False)   # future — must not appear

        resp = self.client.get(URL)
        self.assertEqual(resp.status_code, 200)
        events = list(resp.context["events"])
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].pk, past_unattended.pk)

    def test_get_excludes_today(self):
        _event("GC0004", TODAY, found=False)
        resp = self.client.get(URL)
        self.assertContains(resp, "No past unattended events found")

    def test_get_includes_all_event_types(self):
        for i, ct in enumerate([
            CacheType.EVENT, CacheType.CITO, CacheType.MEGA_EVENT,
            CacheType.GIGA_EVENT, CacheType.COMMUNITY_CELEBRATION,
        ]):
            _event(f"GC{i:04d}", YESTERDAY, found=False, cache_type=ct)

        resp = self.client.get(URL)
        self.assertEqual(len(resp.context["events"]), 5)

    def test_get_excludes_non_event_cache_types(self):
        _event("GC9001", YESTERDAY, found=False, cache_type=CacheType.TRADITIONAL)
        resp = self.client.get(URL)
        self.assertContains(resp, "No past unattended events found")


class RemoveUnattendedPastEventsPostTests(TestCase):

    def test_post_deletes_past_unattended_and_redirects(self):
        _event("GC1001", YESTERDAY, found=False)
        _event("GC1002", YESTERDAY, found=False)

        resp = self.client.post(URL)
        self.assertRedirects(resp, reverse("geocaches:tools_event_calendar"))
        self.assertEqual(Geocache.objects.count(), 0)

    def test_post_preserves_attended_events(self):
        _event("GC2001", YESTERDAY, found=False)
        attended = _event("GC2002", YESTERDAY, found=True)

        self.client.post(URL)
        self.assertEqual(Geocache.objects.count(), 1)
        self.assertTrue(Geocache.objects.filter(pk=attended.pk).exists())

    def test_post_preserves_future_events(self):
        _event("GC3001", YESTERDAY, found=False)
        future = _event("GC3002", TOMORROW, found=False)

        self.client.post(URL)
        self.assertEqual(Geocache.objects.count(), 1)
        self.assertTrue(Geocache.objects.filter(pk=future.pk).exists())

    def test_post_success_message_count_reflects_geocaches_only(self):
        from geocaches.models import Log, LogType
        ev = _event("GC4001", YESTERDAY, found=False)
        Log.objects.create(
            geocache=ev, log_type=LogType.NOTE,
            user_name="someone", logged_date=YESTERDAY,
        )

        resp = self.client.post(URL, follow=True)
        messages = [str(m) for m in resp.context["messages"]]
        self.assertEqual(len(messages), 1)
        self.assertIn("Deleted 1 past event cache", messages[0])

    def test_post_empty_is_harmless(self):
        resp = self.client.post(URL, follow=True)
        self.assertEqual(resp.status_code, 200)
        messages = [str(m) for m in resp.context["messages"]]
        self.assertIn("Deleted 0 past event caches", messages[0])

    def test_post_with_ignore_checkbox_adds_internal_entries(self):
        _event("GC5001", YESTERDAY, found=False)
        _event("GC5002", YESTERDAY, found=False)

        resp = self.client.post(URL, {"add_to_ignore_list": "on"}, follow=True)
        self.assertEqual(Geocache.objects.count(), 0)
        codes = set(IgnoreListEntry.objects.values_list("code", flat=True))
        self.assertEqual(codes, {"GC5001", "GC5002"})
        messages = [str(m) for m in resp.context["messages"]]
        self.assertIn("added them to the local ignore list", messages[0])

    def test_post_without_ignore_checkbox_does_not_add_entries(self):
        _event("GC6001", YESTERDAY, found=False)

        resp = self.client.post(URL, follow=True)
        self.assertEqual(IgnoreListEntry.objects.count(), 0)
        messages = [str(m) for m in resp.context["messages"]]
        self.assertNotIn("ignore list", messages[0])

    def test_post_ignore_checkbox_blocks_reimport(self):
        from geocaches.services.save import save_geocache

        _event("GC7001", YESTERDAY, found=False)
        self.client.post(URL, {"add_to_ignore_list": "on"})
        self.assertFalse(Geocache.objects.filter(gc_code="GC7001").exists())

        result = save_geocache(gc_code="GC7001", fields={"name": "Event GC7001", "cache_type": CacheType.EVENT})
        self.assertIsNone(result.geocache)
        self.assertFalse(Geocache.objects.filter(gc_code="GC7001").exists())
