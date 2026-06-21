from django.db import models
from django.db.models import Q

from .enums import LogType, NoteFormat, NoteType, WaypointType


class Waypoint(models.Model):
    geocache = models.ForeignKey("geocaches.Geocache", on_delete=models.CASCADE, related_name="waypoints")
    waypoint_type = models.CharField(max_length=20, choices=WaypointType, default=WaypointType.OTHER)
    prefix = models.CharField(max_length=10, blank=True)
    name = models.CharField(max_length=255, blank=True)
    lookup = models.CharField(max_length=20, blank=True)
    latitude = models.FloatField(null=True, blank=True)
    longitude = models.FloatField(null=True, blank=True)
    note = models.TextField(blank=True)
    is_user_created = models.BooleanField(default=False)
    is_hidden = models.BooleanField(default=False)
    is_completed = models.BooleanField(default=False)
    is_user_modified = models.BooleanField(default=False)

    def __str__(self):
        return f"{self.waypoint_type}: {self.name or self.lookup}"


class Log(models.Model):
    geocache = models.ForeignKey("geocaches.Geocache", on_delete=models.CASCADE, related_name="logs")
    log_type = models.CharField(max_length=50, choices=LogType)
    user_name = models.CharField(max_length=255, blank=True)
    user_id = models.CharField(max_length=50, blank=True)  # platform-assigned user ID (GC: numeric; OC: uuid)
    logged_date = models.DateField()
    logged_at = models.DateTimeField(null=True, blank=True)  # full datetime (user-created logs)
    text = models.TextField(blank=True)
    source_id = models.CharField(max_length=50, blank=True)
    source = models.CharField(max_length=20, blank=True)  # 'gc', 'oc_de', 'oc_pl', etc.
    sequence_number = models.PositiveIntegerField(null=True, blank=True, db_index=True)
    is_local = models.BooleanField(default=False)  # True for user-created logs

    class Meta:
        ordering = ["-logged_at", "-logged_date"]
        constraints = [
            models.UniqueConstraint(
                fields=["geocache", "source_id"],
                condition=~Q(source_id=""),
                name="uniq_log_geocache_source_id",
            ),
        ]

    def __str__(self):
        return f"{self.log_type} by {self.user_name} on {self.logged_date}"


class Note(models.Model):
    geocache    = models.ForeignKey("geocaches.Geocache", on_delete=models.CASCADE, related_name="notes")
    note_type   = models.CharField(max_length=20, choices=NoteType, default=NoteType.NOTE)
    format      = models.CharField(max_length=10, choices=NoteFormat, default=NoteFormat.PLAIN)
    body        = models.TextField(blank=True)
    # Optional: log type for field notes, and a user-assigned date for any note
    log_type    = models.CharField(max_length=50, choices=LogType, blank=True)
    logged_at   = models.DateTimeField(null=True, blank=True)
    # Nullable: unknown for GSAK-imported notes; set explicitly by the UI
    created_at  = models.DateTimeField(null=True, blank=True)
    updated_at  = models.DateTimeField(null=True, blank=True)
    submitted_at = models.DateTimeField(null=True, blank=True)  # set when field note is submitted as a log
    # Bulk logging state
    bulk_draft      = models.BooleanField(default=False)
    bulk_dismissed  = models.BooleanField(default=False)  # removed from pending queue without submitting
    submit_error    = models.TextField(blank=True, default="")
    sequence_number = models.PositiveIntegerField(null=True, blank=True)
    # Draft log text (separate from the original imported body, which is never overwritten)
    draft_body      = models.TextField(blank=True, default="")

    def __str__(self):
        date = self.created_at.strftime("%Y-%m-%d") if self.created_at else "undated"
        return f"{self.get_note_type_display()} for {self.geocache} ({date})"


class CustomField(models.Model):
    geocache = models.ForeignKey("geocaches.Geocache", on_delete=models.CASCADE, related_name="custom_fields")
    key = models.CharField(max_length=100)
    value = models.TextField(blank=True)

    class Meta:
        unique_together = ("geocache", "key")

    def __str__(self):
        return f"{self.key}={self.value}"


class Image(models.Model):
    geocache = models.ForeignKey("geocaches.Geocache", on_delete=models.CASCADE, related_name="images")
    url = models.URLField(max_length=500)
    name = models.CharField(max_length=255, blank=True)
    description = models.TextField(blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["geocache", "url"],
                name="uniq_image_geocache_url",
            ),
        ]

    def __str__(self):
        return self.name or self.url
