"""Trackable (Travel Bug / Geocoin) models.

Phase 1 scope (log-time TB handling):
  - CacheTrackableMention: per-cache GPX-derived list of TBs currently in the cache.
  - Trackable: stub rows created on first interaction; carries enough to render
    a log row. Fully enriched in Phase 2.
  - TrackableLog: per-event log mirror, with nullable FK to Geocache + always-set
    string fallback (we never auto-import caches just because a TB log references
    them).
  - TrackableImage: gallery + per-log attachments (used from Phase 2 onwards;
    declared now to avoid a later migration).

See docs/trackables-plan.md for the phased plan.
"""
from django.db import models


class TrackableKind(models.TextChoices):
    TRAVEL_BUG = "travel_bug", "Travel Bug"
    GEOCOIN    = "geocoin",    "Geocoin"
    OTHER      = "other",      "Other"


class TrackableHolderState(models.TextChoices):
    UNKNOWN       = "unknown",        "Unknown"
    IN_CACHE      = "in_cache",       "In a cache"
    HELD_BY_USER  = "held_by_user",   "In my inventory"
    HELD_BY_OTHER = "held_by_other",  "Held by someone else"
    COLLECTION    = "collection",     "In my collection"
    MISSING       = "missing",        "Missing"


class TrackableLogType(models.TextChoices):
    # GC trackable log types — names match the GC API where possible.
    DISCOVERED         = "Discovered It",           "Discovered It"
    RETRIEVED          = "Retrieve It from a Cache", "Retrieved It"
    DROPPED            = "Dropped Off",             "Dropped Off"     # also rendered as "placed it"
    GRABBED            = "Grab It (Not from a Cache)", "Grabbed It"
    NOTE               = "Write note",              "Write Note"
    VISITED            = "Visited",                 "Visited"
    MARK_MISSING       = "Mark Missing",            "Mark Missing"
    MOVE_TO_COLLECTION = "Move to Collection",      "Move to Collection"
    MOVE_TO_INVENTORY  = "Move to Inventory",       "Move to Inventory"


class CacheTrackableMention(models.Model):
    """Lightweight per-cache record of trackables present in the cache.

    Populated from the GPX <groundspeak:travelbugs> block on import / refresh.
    Independent of the full Trackable model: a mention may exist without us
    ever upserting a Trackable row, and vice versa.
    """
    geocache     = models.ForeignKey(
        "geocaches.Geocache", on_delete=models.CASCADE, related_name="trackable_mentions"
    )
    gc_id        = models.IntegerField(null=True, blank=True)   # groundspeak numeric id
    ref_code     = models.CharField(max_length=20, db_index=True)
    name         = models.CharField(max_length=255, blank=True)
    last_seen_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [("geocache", "ref_code")]
        ordering = ["ref_code"]

    def __str__(self):
        return f"{self.ref_code} in {self.geocache_id}"


class Trackable(models.Model):
    reference_code = models.CharField(max_length=20, unique=True, db_index=True)  # TB#######
    tracking_code  = models.CharField(max_length=20, blank=True)  # private; only when we hold/own it

    name        = models.CharField(max_length=255)
    series      = models.CharField(max_length=255, blank=True)
    kind        = models.CharField(max_length=20, choices=TrackableKind, default=TrackableKind.TRAVEL_BUG)
    owner_name  = models.CharField(max_length=255, blank=True)
    owner_gc_id = models.IntegerField(null=True, blank=True)
    icon_url    = models.URLField(max_length=500, blank=True)

    released_date  = models.DateField(null=True, blank=True)
    origin         = models.CharField(max_length=255, blank=True)
    goal           = models.TextField(blank=True)
    about          = models.TextField(blank=True)
    is_collectible = models.BooleanField(null=True, blank=True)
    is_active      = models.BooleanField(default=True)
    is_archived    = models.BooleanField(default=False)

    holder_state = models.CharField(
        max_length=20, choices=TrackableHolderState, default=TrackableHolderState.UNKNOWN
    )

    # Current location — never auto-imports a cache. FK is filled only when the
    # cache happens to be in our local DB.
    current_geocache       = models.ForeignKey(
        "geocaches.Geocache",
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="trackables_here",
    )
    current_geocache_code  = models.CharField(max_length=20, blank=True)
    current_geocache_name  = models.CharField(max_length=255, blank=True)
    current_holder_name    = models.CharField(max_length=255, blank=True)

    # Denormalised current coords for the trackables map view.
    # Falls back to user home coords (ReferencePoint.is_home=True) at render time
    # when null. `coords_user_override` blocks sync from clobbering manual edits.
    current_lat          = models.FloatField(null=True, blank=True)
    current_lon          = models.FloatField(null=True, blank=True)
    coords_user_override = models.BooleanField(default=False)

    total_distance_km = models.FloatField(null=True, blank=True)
    total_visits      = models.IntegerField(null=True, blank=True)
    last_log_date     = models.DateField(null=True, blank=True)

    # Auto-visit — when this TB is in our inventory and we log a cache,
    # auto-add a "Visited" trackable log with `auto_visit_text` as the body
    # (placeholders expanded per cache). User-edited from the profile-page
    # inventory panel so the log dialog stays uncluttered.
    auto_visit_enabled = models.BooleanField(default=False)
    auto_visit_text    = models.TextField(blank=True)

    tags = models.ManyToManyField("geocaches.Tag", blank=True, related_name="trackables")

    imported_at = models.DateTimeField(auto_now_add=True)
    updated_at  = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return f"{self.reference_code} — {self.name}"

    @property
    def resolved_geocache(self):
        """Prefer the FK; fall back to a lookup by gc_code. None if unknown."""
        if self.current_geocache_id:
            return self.current_geocache
        if self.current_geocache_code:
            from geocaches.models.cache import Geocache
            return Geocache.objects.filter(gc_code=self.current_geocache_code).first()
        return None


class TrackableLog(models.Model):
    trackable   = models.ForeignKey(Trackable, on_delete=models.CASCADE, related_name="logs")
    log_type    = models.CharField(max_length=50, choices=TrackableLogType)
    logged_date = models.DateField()
    logged_at   = models.DateTimeField(null=True, blank=True)
    text        = models.TextField(blank=True)
    user_name   = models.CharField(max_length=255, blank=True)
    user_id     = models.CharField(max_length=50, blank=True)

    # Same pattern as Trackable.current_geocache_* — never auto-imports.
    geocache          = models.ForeignKey(
        "geocaches.Geocache",
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="trackable_logs",
    )
    geocache_ref_code = models.CharField(max_length=20, blank=True)
    geocache_lat      = models.FloatField(null=True, blank=True)
    geocache_lon      = models.FloatField(null=True, blank=True)

    source_id = models.CharField(max_length=50, blank=True)   # GC log GUID
    is_local  = models.BooleanField(default=False)

    class Meta:
        ordering = ["-logged_at", "-logged_date"]

    def __str__(self):
        return f"{self.log_type} on {self.trackable_id} ({self.logged_date})"

    @property
    def resolved_geocache(self):
        if self.geocache_id:
            return self.geocache
        if self.geocache_ref_code:
            from geocaches.models.cache import Geocache
            return Geocache.objects.filter(gc_code=self.geocache_ref_code).first()
        return None


class TrackableImage(models.Model):
    """Gallery / per-log images for a trackable.

    Either `trackable` or `log` (or both) is set: image attached to the TB
    overall, or to a specific log entry.
    """
    trackable   = models.ForeignKey(
        Trackable, on_delete=models.CASCADE, null=True, blank=True, related_name="images"
    )
    log         = models.ForeignKey(
        TrackableLog, on_delete=models.CASCADE, null=True, blank=True, related_name="images"
    )

    # `source_id` is the GC image referenceCode — stable per upload, used to
    # dedup on re-sync. Empty for locally-uploaded images that haven't been
    # round-tripped through the API yet.
    source_id     = models.CharField(max_length=50, blank=True, db_index=True)
    url           = models.URLField(max_length=500, blank=True)
    thumbnail_url = models.URLField(max_length=500, blank=True)
    large_url     = models.URLField(max_length=500, blank=True)
    local_path    = models.CharField(max_length=500, blank=True)
    caption       = models.CharField(max_length=255, blank=True)
    description   = models.TextField(blank=True)
    uploaded_at   = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-uploaded_at", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["trackable", "source_id"],
                condition=models.Q(trackable__isnull=False) & ~models.Q(source_id=""),
                name="trackableimage_unique_source_per_trackable",
            ),
        ]
