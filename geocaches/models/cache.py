from django.db import models

from geocaches.oc_platforms import OC_DOMAINS, platform_for_code

from .enums import CacheSize, CacheStatus, CacheType


class LiveCacheManager(models.Manager):
    def get_queryset(self):
        return super().get_queryset().filter(deleted_at__isnull=True)


class Geocache(models.Model):
    gc_code = models.CharField(max_length=20, blank=True, db_index=True)
    oc_code = models.CharField(max_length=20, blank=True, db_index=True)
    al_code = models.CharField(max_length=20, blank=True, db_index=True)

    name = models.CharField(max_length=255)
    owner = models.CharField(max_length=255, blank=True)
    placed_by = models.CharField(max_length=255, blank=True)
    owner_gc_id = models.IntegerField(null=True, blank=True)
    parent = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.SET_NULL, related_name="children"
    )
    cache_type = models.CharField(max_length=50, choices=CacheType)
    size = models.CharField(max_length=20, choices=CacheSize, default=CacheSize.UNKNOWN)
    size_override = models.CharField(max_length=20, choices=CacheSize, null=True, blank=True)
    status = models.CharField(max_length=20, choices=CacheStatus, default=CacheStatus.ACTIVE)

    latitude = models.FloatField()
    longitude = models.FloatField()

    difficulty = models.FloatField(null=True, blank=True)
    terrain = models.FloatField(null=True, blank=True)

    short_description = models.TextField(null=True, blank=True)
    long_description = models.TextField(null=True, blank=True)
    hint = models.TextField(null=True, blank=True)

    hidden_date = models.DateField(null=True, blank=True)
    event_start_time = models.TimeField(null=True, blank=True)
    event_end_time = models.TimeField(null=True, blank=True)
    last_found_date = models.DateField(null=True, blank=True)

    country = models.CharField(max_length=100, blank=True)
    iso_country_code = models.CharField(max_length=2, blank=True, db_index=True)
    state = models.CharField(max_length=100, blank=True)
    county = models.CharField(max_length=100, blank=True)
    # When set, the country/state/county fields were edited by hand and must
    # not be overwritten by the online or offline enrichment passes.
    manual_location = models.BooleanField(default=False)
    elevation = models.FloatField(null=True, blank=True)        # from external source (DEM/SRTM)
    elevation_user = models.FloatField(null=True, blank=True)   # user-defined; takes priority, never overwritten by refresh

    fav_points = models.IntegerField(null=True, blank=True)   # null = not provided by source; 0 = confirmed zero
    user_favorited = models.BooleanField(null=True, blank=True)  # True if current user gave a GC favourite point
    recommendations = models.IntegerField(null=True, blank=True)  # OC recommendations (≈ GC fav points)
    platform_log_count = models.IntegerField(default=0)
    is_premium = models.BooleanField(default=False)
    has_trackable = models.BooleanField(default=False)
    needs_maintenance = models.BooleanField(default=False)  # OC: from API; GC: derived from log history
    has_corrected_coordinates = models.BooleanField(default=False)
    background_image_url = models.URLField(max_length=500, blank=True)

    # Source tracking
    primary_source = models.CharField(max_length=20, blank=True)  # 'gc', 'oc_de', 'oc_pl', etc.; see UserAccount.PLATFORM_CHOICES

    # Personal/user tracking
    found = models.BooleanField(default=False)
    found_date = models.DateField(null=True, blank=True)
    # Adventure Lab completion: set True when all stages are found.
    # AL parent caches must never have found=True; use this flag instead.
    completed = models.BooleanField(default=False)
    found_count = models.PositiveSmallIntegerField(default=0)
    ftf = models.BooleanField(default=False)
    dnf = models.BooleanField(default=False)
    dnf_date = models.DateField(null=True, blank=True)
    user_flag = models.BooleanField(default=False)
    watch = models.BooleanField(default=False)
    gc_note = models.TextField(blank=True)
    user_sort = models.IntegerField(null=True, blank=True)
    color = models.CharField(max_length=20, blank=True)

    # Import/sync management
    last_gpx_date = models.DateTimeField(null=True, blank=True)
    import_locked = models.BooleanField(default=False)
    is_placeholder = models.BooleanField(default=False)  # True for field-note-only stubs not yet synced from API

    # Map visibility — persistent "hide on map" flag. Session-scoped hides
    # live in request.session["map_hidden_session"]; the two stores are
    # mutually exclusive at the service layer.
    map_hidden_always = models.BooleanField(default=False, db_index=True)

    # Adventure Lab fields (null for non-ALC caches)
    adventure = models.ForeignKey(
        "geocaches.Adventure", null=True, blank=True, on_delete=models.SET_NULL, related_name="stages"
    )

    tags = models.ManyToManyField("geocaches.Tag", blank=True, related_name="geocaches")
    attributes = models.ManyToManyField("geocaches.Attribute", blank=True, related_name="geocaches")

    imported_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    deleted_at = models.DateTimeField(null=True, blank=True, default=None)

    objects = LiveCacheManager()
    all_objects = models.Manager()

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(gc_code__gt="") | models.Q(oc_code__gt="") | models.Q(al_code__gt=""),
                name="geocache_has_at_least_one_code",
            )
        ]
        indexes = [
            # Composite geo index: bbox / viewport range queries filter on both axes together.
            models.Index(fields=["latitude", "longitude"], name="geocache_lat_lon_idx"),
            # deleted_at is filtered on every query via LiveCacheManager.
            models.Index(fields=["deleted_at"], name="geocache_deleted_at_idx"),
            # High-frequency list filter columns.
            models.Index(fields=["found"], name="geocache_found_idx"),
            models.Index(fields=["completed"], name="geocache_completed_idx"),
            models.Index(fields=["cache_type"], name="geocache_cache_type_idx"),
            models.Index(fields=["status"], name="geocache_status_idx"),
        ]

    # ------------------------------------------------------------------
    # External URL helpers
    # ------------------------------------------------------------------

    @property
    def effective_latitude(self) -> float:
        """Corrected latitude if available, otherwise original."""
        if self.has_corrected_coordinates:
            cc = getattr(self, "corrected_coordinates", None)
            if cc:
                return cc.latitude
        return self.latitude

    @property
    def effective_longitude(self) -> float:
        """Corrected longitude if available, otherwise original."""
        if self.has_corrected_coordinates:
            cc = getattr(self, "corrected_coordinates", None)
            if cc:
                return cc.longitude
        return self.longitude

    @property
    def display_code(self) -> str:
        """The code to show in the UI: gc_code, al_code, or oc_code."""
        return self.gc_code or self.al_code or self.oc_code

    @property
    def external_url(self) -> str | None:
        """Primary external URL for this cache (GC takes precedence over OC)."""
        urls = self.external_urls
        return urls[0][1] if urls else None

    @property
    def external_urls(self) -> list[tuple[str, str]]:
        """All known external URLs as [(label, url), …], GC first."""
        result: list[tuple[str, str]] = []
        if self.al_code:
            if self.adventure_id and self.adventure and self.adventure.smart_link:
                result.append(("AL app", f"https://labs.geocaching.com/goto/{self.adventure.smart_link}"))
            elif self.adventure_id and self.adventure and self.adventure.url:
                result.append(("Adventure Lab", self.adventure.url))
        if self.gc_code:
            result.append((
                "geocaching.com",
                f"https://www.geocaching.com/geocache/{self.gc_code}",
            ))
        if self.oc_code:
            prefix = self.oc_code[:2].upper()
            domain = OC_DOMAINS.get(prefix, "www.opencaching.de")
            result.append((
                domain.replace("www.", ""),
                f"https://{domain}/viewcache.php?wp={self.oc_code}",
            ))
        return result

    @property
    def oc_platform(self) -> str:
        """Return the OC platform id (e.g. 'oc_de') based on the OC code prefix."""
        if not self.oc_code:
            return ""
        return platform_for_code(self.oc_code)

    @property
    def refresh_sources(self) -> list[tuple[str, str]]:
        """API sources this cache can be refreshed from: [(label, platform_id), …]."""
        result: list[tuple[str, str]] = []
        if self.gc_code and not self.al_code:
            result.append(("GC", "gc"))
        if self.oc_code:
            domain = OC_DOMAINS.get(
                self.oc_code[:2].upper(), "www.opencaching.de"
            ).replace("www.", "")
            result.append((domain, self.oc_platform))
        if self.al_code and self.adventure_id:
            result.append(("Adventure Lab", "al"))
        return result

    def __str__(self):
        code = self.gc_code or self.al_code or self.oc_code
        return f"{code} — {self.name}"
