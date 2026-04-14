import json

from django.db import models


class UserPreference(models.Model):
    key = models.CharField(max_length=100, unique=True)
    value = models.TextField()

    @classmethod
    def get(cls, key, default=None):
        try:
            return json.loads(cls.objects.get(key=key).value)
        except cls.DoesNotExist:
            return default

    @classmethod
    def set(cls, key, value):
        cls.objects.update_or_create(key=key, defaults={"value": json.dumps(value)})

    def __str__(self):
        return f"{self.key}"


class ReferencePoint(models.Model):
    name = models.CharField(max_length=100)
    latitude = models.FloatField()
    longitude = models.FloatField()
    is_default = models.BooleanField(default=False)
    is_home = models.BooleanField(default=False)
    # valid_from enables temporal history: multiple "Home" records with different dates
    # allow calculating "distance from home at time of find" for statistics.
    valid_from = models.DateField(null=True, blank=True)
    note = models.CharField(max_length=255, blank=True)

    def save(self, *args, **kwargs):
        if self.is_default:
            ReferencePoint.objects.exclude(pk=self.pk).update(is_default=False)
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name

    class Meta:
        ordering = ["name", "-valid_from"]


class ColumnPreset(models.Model):
    name = models.CharField(max_length=100, unique=True)
    columns = models.JSONField()
    is_builtin = models.BooleanField(default=False)

    def __str__(self):
        return self.name

    class Meta:
        ordering = ["name"]


GPX_EXPORT_DEFAULTS = {
    # Notes / user notes
    "notes_gcforge":    True,   # GCForge user notes → fake log entries
    "notes_gc_user":    True,   # geocaching.com personal note (gc_note field)
    "notes_field_notes": True,  # field notes
    "notes_corrected":  True,   # corrected-coordinates summary
    "notes_fuse":       True,   # True = fuse all notes into one log entry
    # Child waypoints
    "wp_hidden":              False,  # export hidden waypoints
    "wp_completed":           True,   # export solved/completed waypoints
    "wp_completed_as_hidden": True,   # mark completed WPs as hidden (c:geo/Locus — not yet implemented)
    # Corrected coordinates
    "cc_original_as_wp": False,  # export original coords as a child waypoint
    # Logs
    "logs_max":        "",    # max regular logs to export; "" = all
    "logs_my_on_top":  True,  # sort own logs to top (below notes)
    # Adventure Labs
    "alc_stages":    "child_and_export",  # "child_only" | "child_and_export" | "dont_export"
    "alc_completed": "found_invisible",   # "found_invisible" | "found_visible" | "dont_export"
    # Events
    "events_exclude_past": False,  # if True, skip events whose hidden_date < today
    "events_days_ahead":   "",     # max days ahead to include; "" = no limit
}


class GpxExportPreset(models.Model):
    name     = models.CharField(max_length=100, unique=True)
    settings = models.JSONField()

    def __str__(self):
        return self.name

    class Meta:
        ordering = ["name"]
