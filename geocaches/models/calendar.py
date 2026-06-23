from django.db import models


class CalendarEntry(models.Model):
    """A single entry in GCForge's managed local calendar.

    The calendar is served live as one ``.ics`` (token-gated feed) that the user
    subscribes to from Thunderbird/Outlook/etc.  Entries are upserted by a stable
    :attr:`uid`, so re-running a populate action never creates duplicates and
    re-running after a find logs drops the now-satisfied reminder.

    Two kinds today:
      * ``event`` — an upcoming event cache (UID ``evt-<code>@gcforge``).
      * ``missing_day`` — a reminder on an upcoming calendar day where finds of a
        chosen cache type are still below the target (UID
        ``miss-<type>-<YYYYMMDD>@gcforge``).
    """

    KIND_EVENT = "event"
    KIND_MISSING_DAY = "missing_day"
    KIND_CHOICES = [
        (KIND_EVENT, "Event"),
        (KIND_MISSING_DAY, "Missing day"),
    ]

    uid = models.CharField(max_length=255, unique=True)
    kind = models.CharField(max_length=20, choices=KIND_CHOICES)
    title = models.CharField(max_length=255)
    start_date = models.DateField()
    # Null start_time => all-day entry (DATE value in the .ics).
    start_time = models.TimeField(null=True, blank=True)
    end_time = models.TimeField(null=True, blank=True)
    location = models.CharField(max_length=255, blank=True)
    url = models.CharField(max_length=500, blank=True)
    description = models.TextField(blank=True)
    # IANA timezone of the event location (for timed events); empty => floating.
    tz_name = models.CharField(max_length=64, blank=True)
    # Emit a VALARM reminder for this entry in the .ics feed.
    alarm = models.BooleanField(default=False)
    # For missing_day entries: the cache type the reminder is about.
    cache_type = models.CharField(max_length=50, blank=True)
    geocache = models.ForeignKey(
        "geocaches.Geocache", on_delete=models.SET_NULL,
        null=True, blank=True, related_name="calendar_entries",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["start_date", "start_time"]

    def __str__(self) -> str:
        return f"{self.start_date} {self.title}"
