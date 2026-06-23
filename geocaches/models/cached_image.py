"""Cached-image registry. See `geocaches/services/image_cache.py` for the
download + serve logic; this model is the persistence layer only."""
from django.db import models


class CachedImage(models.Model):
    CATEGORY_CHOICES = [
        ("tb_icon",       "Trackable icon"),
        ("tb_listing",    "Trackable listing image"),
        ("tb_log",        "Trackable log image"),
        ("cache_listing", "Cache listing image"),
        ("cache_log",     "Cache log image"),
        ("alc",           "Adventure Lab image"),
    ]

    category      = models.CharField(max_length=20, choices=CATEGORY_CHOICES, db_index=True)
    source_url    = models.URLField(max_length=1024)
    filename      = models.CharField(max_length=128)
    mime_type     = models.CharField(max_length=80, blank=True)
    bytes         = models.PositiveIntegerField(default=0)
    downloaded_at = models.DateTimeField(auto_now_add=True)
    last_seen_at  = models.DateTimeField(auto_now=True)

    # Owner links — when every linked entity is deleted, the row + file
    # are purged via post_delete signals (see geocaches/signals.py).
    linked_geocaches      = models.ManyToManyField(
        "geocaches.Geocache",     blank=True, related_name="cached_images_in",
    )
    linked_trackables     = models.ManyToManyField(
        "geocaches.Trackable",    blank=True, related_name="cached_images_in",
    )
    linked_adventures     = models.ManyToManyField(
        "geocaches.Adventure",    blank=True, related_name="cached_images_in",
    )
    linked_logs           = models.ManyToManyField(
        "geocaches.Log",          blank=True, related_name="cached_images_in",
    )
    linked_trackable_logs = models.ManyToManyField(
        "geocaches.TrackableLog", blank=True, related_name="cached_images_in",
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["category", "source_url"],
                name="cached_image_unique_category_url",
            ),
        ]
        indexes = [
            models.Index(fields=["category", "downloaded_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.category}:{self.filename}"

    def has_any_link(self) -> bool:
        return (
            self.linked_geocaches.exists()
            or self.linked_trackables.exists()
            or self.linked_adventures.exists()
            or self.linked_logs.exists()
            or self.linked_trackable_logs.exists()
        )
