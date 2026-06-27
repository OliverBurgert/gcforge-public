"""Instant Notifications — mirrors what geocaching.com offers under
``geocaching.com/notify``.

One ``GCNotification`` row corresponds to exactly one server-side notification
(a single cache type + a single location + a set of log-event subscriptions).
OC support is planned for a later phase — the ``source`` field is the seam.
"""
from django.db import models


class GCNotification(models.Model):
    SOURCE_CHOICES = [
        ("gc", "Geocaching.com"),
    ]

    # GC's "Publish Listing" log type — every new notification subscribes to
    # this one by default.  Kept here so the model file is self-contained.
    DEFAULT_LOG_EVENT_IDS = [24]

    source = models.CharField(max_length=8, choices=SOURCE_CHOICES, default="gc")
    server_id = models.CharField(max_length=32, blank=True, db_index=True)
    name = models.CharField(max_length=200)

    latitude = models.FloatField()
    longitude = models.FloatField()
    radius_km = models.PositiveSmallIntegerField(default=20)

    cache_type_id = models.PositiveIntegerField()
    log_event_ids = models.JSONField(default=list)
    recipient_email = models.EmailField(blank=True)

    enabled = models.BooleanField(default=True)
    location = models.ForeignKey(
        "preferences.ReferencePoint",
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="gc_notifications",
    )
    notes = models.TextField(blank=True)

    last_synced_at = models.DateTimeField(null=True, blank=True)
    server_hash = models.CharField(max_length=64, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["location__name", "name", "cache_type_id"]
        constraints = [
            models.UniqueConstraint(
                fields=["source", "server_id"],
                condition=models.Q(server_id__gt=""),
                name="uniq_notification_server_id",
            ),
        ]

    def __str__(self):
        return f"{self.name} (type={self.cache_type_id}, NID={self.server_id or '-'})"


class OCNotification(models.Model):
    """Mirror of an Opencaching profile's notification settings.

    OC exposes only one notification rule per user — a reference point and a
    radius (0..150 km, 0 = disabled) — set via ``myprofile.php`` on the node's
    website.  We keep one row per OC platform.  ``enabled=False`` pushes
    radius=0 to the server while preserving the user's preferred radius
    locally so re-enabling restores it.
    """

    PLATFORM_CHOICES = [
        ("oc_de", "opencaching.de"),
        ("oc_pl", "opencaching.pl"),
        ("oc_uk", "opencache.uk"),
        ("oc_nl", "opencaching.nl"),
        ("oc_us", "opencaching.us"),
    ]
    MAX_RADIUS_KM = 150

    FREQUENCY_HOURLY = "hourly"
    FREQUENCY_DAILY = "daily"
    FREQUENCY_WEEKLY = "weekly"
    FREQUENCY_CHOICES = [
        (FREQUENCY_HOURLY, "Once per hour"),
        (FREQUENCY_DAILY, "Once per day"),
        (FREQUENCY_WEEKLY, "Once per week"),
    ]

    # Platforms whose model uses just (radius, oconly) — the German fork.
    # Only opencaching.de itself uses this shape; .nl / .pl / .uk / .us all
    # share the multi-neighbourhood schema.
    DE_FAMILY = {"oc_de"}

    platform = models.CharField(max_length=8, choices=PLATFORM_CHOICES)
    # opencaching.us-only: the "neighbourhood index" assigned by the server.
    # ``"0"`` is the default neighbourhood, ``"1"``…``"N"`` are additional
    # ones. Empty for .de family (one rule per platform).
    server_id = models.CharField(max_length=16, blank=True, default="")
    # opencaching.us-only: human-readable name for nbh >= 1.
    name = models.CharField(max_length=100, blank=True, default="")
    latitude = models.FloatField()
    longitude = models.FloatField()
    radius_km = models.PositiveSmallIntegerField(default=20)
    enabled = models.BooleanField(default=True)
    # .de-family: "also notify on newly OConly-marked caches"; n/a for .us
    notify_oconly = models.BooleanField(default=False)
    # .us only: "also notify on new logs in watched caches"
    notify_logs = models.BooleanField(default=False)
    # .us only: digest frequency
    frequency = models.CharField(max_length=8, choices=FREQUENCY_CHOICES,
                                  default=FREQUENCY_DAILY)
    location = models.ForeignKey(
        "preferences.ReferencePoint",
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="oc_notifications",
    )
    last_synced_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["platform", "server_id"]
        unique_together = [("platform", "server_id")]

    def __str__(self):
        state = "off" if not self.enabled else f"{self.radius_km}km"
        suffix = f" [{self.name or 'nbh ' + self.server_id}]" if self.server_id else ""
        return f"{self.platform}{suffix} ({state})"

    @property
    def is_default_nbh(self) -> bool:
        """True for the .us default neighbourhood (server_id==0)."""
        return self.platform == "oc_us" and self.server_id == "0"

    @property
    def is_additional_nbh(self) -> bool:
        """True for .us additional neighbourhoods (server_id>=1)."""
        return self.platform == "oc_us" and self.server_id.isdigit() and int(self.server_id) >= 1
