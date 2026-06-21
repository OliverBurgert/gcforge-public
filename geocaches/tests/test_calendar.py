"""Managed-calendar service + feed view tests."""

import datetime as _dt

from django.test import TestCase
from django.urls import reverse

from geocaches.models import (
    CacheSize, CacheStatus, CacheType, CalendarEntry, Geocache,
)
from geocaches.services import calendar as cal


def _cache(gc_code, **kw):
    defaults = dict(
        name="C", cache_type=CacheType.TRADITIONAL, size=CacheSize.SMALL,
        status=CacheStatus.ACTIVE, latitude=48.0, longitude=9.0,
        difficulty=2.0, terrain=1.5,
    )
    defaults.update(kw)
    return Geocache.objects.create(gc_code=gc_code, **defaults)


class SyncEventsTests(TestCase):
    def setUp(self):
        self.future = _dt.date.today() + _dt.timedelta(days=20)
        self.past = _dt.date.today() - _dt.timedelta(days=5)
        _cache("GCEV1", name="Future Event", cache_type=CacheType.EVENT,
               hidden_date=self.future)
        _cache("GCEV2", name="Past Event", cache_type=CacheType.EVENT,
               hidden_date=self.past)
        _cache("GCT1", name="Trad", cache_type=CacheType.TRADITIONAL,
               hidden_date=self.future)

    def test_only_future_events_added(self):
        cal.sync_events()
        entries = CalendarEntry.objects.filter(kind=CalendarEntry.KIND_EVENT)
        self.assertEqual(entries.count(), 1)
        self.assertEqual(entries.first().title, "Future Event")

    def test_idempotent_no_duplicates(self):
        cal.sync_events()
        cal.sync_events()
        self.assertEqual(
            CalendarEntry.objects.filter(kind=CalendarEntry.KIND_EVENT).count(), 1
        )

    def test_prunes_stale_events(self):
        cal.sync_events()
        # The future event becomes past => next sync removes it.
        Geocache.objects.filter(gc_code="GCEV1").update(hidden_date=self.past)
        cal.sync_events()
        self.assertEqual(
            CalendarEntry.objects.filter(kind=CalendarEntry.KIND_EVENT).count(), 0
        )


class AddMissingDaysTests(TestCase):
    def test_minimum_and_range(self):
        # One Multi found today's MM-DD a year ago -> that day is filled (count 1).
        today = _dt.date.today()
        last_year = today.replace(year=today.year - 1)
        _cache("GCM1", found=True, found_date=last_year, cache_type=CacheType.MULTI)

        res = cal.add_missing_days(CacheType.MULTI, minimum=1, days=30)
        # Reminders are created for the missing days in the next 30 days, but not
        # for today's MM-DD which already has a Multi find.
        dates = set(
            CalendarEntry.objects
            .filter(kind=CalendarEntry.KIND_MISSING_DAY)
            .values_list("start_date", flat=True)
        )
        self.assertNotIn(today, dates)
        self.assertTrue(dates)  # other upcoming days are still missing

    def test_replaces_on_rerun(self):
        cal.add_missing_days(CacheType.MULTI, minimum=1, days=30)
        first = CalendarEntry.objects.filter(kind=CalendarEntry.KIND_MISSING_DAY).count()
        cal.add_missing_days(CacheType.MULTI, minimum=1, days=30)
        second = CalendarEntry.objects.filter(kind=CalendarEntry.KIND_MISSING_DAY).count()
        self.assertEqual(first, second)

    def test_alc_path_uses_lab_finds_regardless_of_setting(self):
        from geocaches.models import ALStageDetail, Adventure
        from preferences.models import UserPreference

        today = _dt.date.today()
        last_year = today.replace(year=today.year - 1)
        adv = Adventure.objects.create(code="LCZ", adventure_guid="z", title="Z")
        gc = Geocache.objects.create(
            al_code="LCZ-1", name="Z stage 1", cache_type=CacheType.LAB,
            adventure=adv, found=True, found_date=last_year,
            latitude=48.0, longitude=9.0,
        )
        ALStageDetail.objects.create(geocache=gc, stage_number=1)

        # Even with Adventure Labs excluded from the Statistics tab, the alc path
        # treats today's MM-DD as filled (a lab stage was found that day) and
        # types its reminders as Adventure Lab.
        UserPreference.set("stats_include_al", False)
        cal.add_missing_days(CacheType.LAB, minimum=1, days=30, alc=True)
        dates = set(
            CalendarEntry.objects
            .filter(kind=CalendarEntry.KIND_MISSING_DAY)
            .values_list("start_date", flat=True)
        )
        self.assertNotIn(today, dates)
        self.assertTrue(dates)
        self.assertTrue(
            CalendarEntry.objects.filter(cache_type=CacheType.LAB).exists()
        )

    def test_satisfied_day_drops_on_rerun(self):
        today = _dt.date.today()
        cal.add_missing_days(CacheType.MULTI, minimum=1, days=10)
        self.assertTrue(
            CalendarEntry.objects.filter(
                kind=CalendarEntry.KIND_MISSING_DAY, start_date=today
            ).exists()
        )
        # Log a Multi find on today's MM-DD -> the reminder for today disappears.
        _cache("GCM2", found=True, found_date=today, cache_type=CacheType.MULTI)
        cal.add_missing_days(CacheType.MULTI, minimum=1, days=10)
        self.assertFalse(
            CalendarEntry.objects.filter(
                kind=CalendarEntry.KIND_MISSING_DAY, start_date=today
            ).exists()
        )


class AgendaTests(TestCase):
    def test_ten_day_window(self):
        today = _dt.date.today()
        CalendarEntry.objects.create(
            uid="a@gcforge", kind=CalendarEntry.KIND_MISSING_DAY,
            title="in range", start_date=today + _dt.timedelta(days=3),
        )
        CalendarEntry.objects.create(
            uid="b@gcforge", kind=CalendarEntry.KIND_MISSING_DAY,
            title="out of range", start_date=today + _dt.timedelta(days=40),
        )
        groups = cal.agenda(10)
        titles = [e.title for g in groups for e in g["entries"]]
        self.assertIn("in range", titles)
        self.assertNotIn("out of range", titles)


class BuildIcsTests(TestCase):
    def test_emits_valid_vcalendar(self):
        CalendarEntry.objects.create(
            uid="x@gcforge", kind=CalendarEntry.KIND_EVENT,
            title="Picnic; food, drinks", start_date=_dt.date(2030, 6, 1),
            start_time=_dt.time(14, 30),
        )
        ics = cal.build_ics()
        self.assertIn("BEGIN:VCALENDAR", ics)
        self.assertIn("BEGIN:VEVENT", ics)
        self.assertIn("UID:x@gcforge", ics)
        self.assertIn("DTSTAMP:", ics)  # RFC 5545 required, strict parsers need it
        self.assertIn("DTSTART:20300601T143000", ics)
        # Special characters escaped per RFC 5545.
        self.assertIn("SUMMARY:Picnic\\; food\\, drinks", ics)
        self.assertTrue(ics.endswith("END:VCALENDAR\r\n"))

    def test_all_day_entry_uses_date_value(self):
        CalendarEntry.objects.create(
            uid="y@gcforge", kind=CalendarEntry.KIND_MISSING_DAY,
            title="all day", start_date=_dt.date(2030, 6, 2),
        )
        ics = cal.build_ics()
        self.assertIn("DTSTART;VALUE=DATE:20300602", ics)

    def test_timed_event_rendered_in_utc(self):
        # 14:00 Europe/Berlin in June (CEST, +02:00) => 12:00 UTC.
        CalendarEntry.objects.create(
            uid="tz@gcforge", kind=CalendarEntry.KIND_EVENT, title="Berlin event",
            start_date=_dt.date(2030, 6, 1), start_time=_dt.time(14, 0),
            tz_name="Europe/Berlin",
        )
        ics = cal.build_ics()
        self.assertIn("DTSTART:20300601T120000Z", ics)

    def test_floating_when_tz_unknown(self):
        CalendarEntry.objects.create(
            uid="fl@gcforge", kind=CalendarEntry.KIND_EVENT, title="floating",
            start_date=_dt.date(2030, 6, 1), start_time=_dt.time(14, 0),
        )
        self.assertIn("DTSTART:20300601T140000", cal.build_ics())

    def test_valarm_only_when_enabled(self):
        CalendarEntry.objects.create(
            uid="al@gcforge", kind=CalendarEntry.KIND_MISSING_DAY, title="todo",
            start_date=_dt.date(2030, 6, 2), alarm=True,
        )
        self.assertIn("BEGIN:VALARM", cal.build_ics())


class EntryActionTests(TestCase):
    def _entry(self):
        return CalendarEntry.objects.create(
            uid="e@gcforge", kind=CalendarEntry.KIND_MISSING_DAY,
            title="todo", start_date=_dt.date.today(),
        )

    def test_toggle_alarm(self):
        e = self._entry()
        self.assertFalse(e.alarm)
        cal.toggle_alarm(e.pk)
        e.refresh_from_db()
        self.assertTrue(e.alarm)
        cal.toggle_alarm(e.pk)
        e.refresh_from_db()
        self.assertFalse(e.alarm)

    def test_delete_entry(self):
        e = self._entry()
        self.assertTrue(cal.delete_entry(e.pk))
        self.assertFalse(CalendarEntry.objects.filter(pk=e.pk).exists())

    def test_day_candidates_where_sql(self):
        d = _dt.date.today() + _dt.timedelta(days=1)
        ev = _cache("GCDAY", cache_type=CacheType.EVENT, hidden_date=d)
        CalendarEntry.objects.create(
            uid="ev@gcforge", kind=CalendarEntry.KIND_EVENT, title="ev",
            start_date=d, geocache=ev,
        )
        CalendarEntry.objects.create(
            uid="md@gcforge", kind=CalendarEntry.KIND_MISSING_DAY, title="todo",
            start_date=d, cache_type=CacheType.MULTI,
        )
        sql = cal.day_candidates_where_sql(d)
        self.assertIn(f"id IN ({ev.pk})", sql)
        self.assertIn("cache_type IN ('Multi-Cache')", sql)
        self.assertIn("found = 0", sql)

    def test_day_candidates_empty_day(self):
        self.assertEqual(cal.day_candidates_where_sql(_dt.date(2099, 1, 1)), "1 = 0")

    def test_day_candidates_view_redirects_to_list(self):
        d = _dt.date.today() + _dt.timedelta(days=1)
        CalendarEntry.objects.create(
            uid="md2@gcforge", kind=CalendarEntry.KIND_MISSING_DAY, title="todo",
            start_date=d, cache_type=CacheType.MULTI,
        )
        resp = self.client.get(reverse("geocaches:calendar_day_candidates"),
                               {"date": d.isoformat()})
        self.assertEqual(resp.status_code, 302)
        self.assertIn("where_sql=", resp["Location"])

    def test_agenda_days_pref_roundtrip(self):
        cal.set_agenda_days(21)
        self.assertEqual(cal.get_agenda_days(), 21)
        # Clamped to a sane range.
        self.assertEqual(cal.set_agenda_days(9999), 366)
        self.assertEqual(cal.set_agenda_days(0), 1)


class ViewTests(TestCase):
    def test_agenda_get_stores_days(self):
        url = reverse("geocaches:calendar_agenda")
        self.client.get(url, {"days": 25})
        self.assertEqual(cal.get_agenda_days(), 25)

    def test_add_missing_returns_confirmation(self):
        resp = self.client.post(reverse("geocaches:calendar_add_missing"),
                                {"stat_type": CacheType.MULTI, "minimum": 1, "days": "30"})
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "to-do")
        # Must NOT self-reload — that would immediately wipe the confirmation.
        self.assertNotIn("HX-Trigger", resp)

    def test_toggle_and_delete_endpoints(self):
        e = CalendarEntry.objects.create(
            uid="v@gcforge", kind=CalendarEntry.KIND_MISSING_DAY,
            title="todo", start_date=_dt.date.today(),
        )
        self.client.post(reverse("geocaches:calendar_toggle_alarm", args=[e.pk]))
        e.refresh_from_db()
        self.assertTrue(e.alarm)
        self.client.post(reverse("geocaches:calendar_delete_entry", args=[e.pk]))
        self.assertFalse(CalendarEntry.objects.filter(pk=e.pk).exists())

    def test_event_agenda_shows_tags_and_distance(self):
        from geocaches.models import Tag
        from preferences.models import ReferencePoint
        ref = ReferencePoint.objects.create(name="Home", latitude=48.0, longitude=9.0,
                                             is_default=True)
        ev = _cache("GCEVT", name="Tagged event", cache_type=CacheType.EVENT,
                    hidden_date=_dt.date.today() + _dt.timedelta(days=2),
                    latitude=49.0, longitude=9.0)
        tag = Tag.objects.create(name="Mega weekend", default_ref_point=ref)
        ev.tags.add(tag)
        cal.sync_events()
        resp = self.client.get(reverse("geocaches:calendar_agenda"))
        self.assertContains(resp, "Mega weekend")
        self.assertContains(resp, "km")  # distance rendered


class FeedViewTests(TestCase):
    def test_valid_token_serves_calendar(self):
        token = cal.get_or_create_token()
        resp = self.client.get(reverse("geocaches:calendar_feed", args=[token]))
        self.assertEqual(resp.status_code, 200)
        self.assertIn("text/calendar", resp["Content-Type"])
        self.assertIn(b"BEGIN:VCALENDAR", resp.content)

    def test_bad_token_404s(self):
        cal.get_or_create_token()
        resp = self.client.get(reverse("geocaches:calendar_feed", args=["nope"]))
        self.assertEqual(resp.status_code, 404)
