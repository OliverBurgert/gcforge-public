from django.core.exceptions import ValidationError
from django.db import models

from .enums import CacheStatus


class IgnoreSource(models.TextChoices):
    INTERNAL = "internal", "Internal"
    GSAK     = "gsak",     "GSAK Import"
    GC       = "gc",       "Geocaching.com"
    OC       = "oc",       "Opencaching"


class IgnoreListEntry(models.Model):
    code                = models.CharField(max_length=20)
    source              = models.CharField(max_length=16, choices=IgnoreSource.choices)
    oc_platform         = models.CharField(max_length=10, blank=True, default="")
    name                = models.CharField(max_length=255, blank=True, default="")
    status              = models.CharField(max_length=16, choices=CacheStatus.choices, blank=True, default="")
    last_status_refresh = models.DateTimeField(null=True, blank=True)
    notes               = models.TextField(blank=True, default="")
    added               = models.DateTimeField(auto_now_add=True)
    updated             = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["source", "oc_platform", "code"],
                name="uniq_ignore_entry",
            )
        ]
        indexes = [
            models.Index(fields=["code"]),
            models.Index(fields=["source", "oc_platform"]),
        ]

    def clean(self):
        if self.source == IgnoreSource.OC and not self.oc_platform:
            raise ValidationError({"oc_platform": "oc_platform is required for OC entries."})

    def __str__(self):
        return f"{self.code} ({self.get_source_display()})"
