from django.db import models


class CacheType(models.TextChoices):
    # GC API type IDs are noted in comments for import mapping
    TRADITIONAL = "Traditional", "Traditional"                                              # id: 2
    MULTI = "Multi-Cache", "Multi-Cache"                                                    # id: 3
    MYSTERY = "Mystery", "Mystery"                                                          # id: 8
    VIRTUAL = "Virtual", "Virtual"                                                          # id: 4
    LETTERBOX = "Letterbox Hybrid", "Letterbox Hybrid"                                      # id: 5
    EARTH = "Earthcache", "Earthcache"                                                      # id: 137
    EVENT = "Event", "Event"                                                                # id: 6
    CITO = "CITO", "Cache In Trash Out Event"                                               # id: 13
    WEBCAM = "Webcam", "Webcam"                                                             # id: 11
    WHERIGO = "Wherigo", "Wherigo"                                                          # id: 1858
    LAB = "Adventure Lab", "Adventure Lab"                                                  # id: -1
    MEGA_EVENT = "Mega-Event", "Mega-Event"                                                 # id: 453
    GIGA_EVENT = "Giga-Event", "Giga-Event"                                                 # id: 7005
    LOCATIONLESS = "Locationless", "Locationless (Reverse) Cache"                           # id: 12
    GPS_ADVENTURES = "GPS Adventures Exhibit", "GPS Adventures Exhibit"                     # id: 1304
    COMMUNITY_CELEBRATION = "Community Celebration Event", "Community Celebration Event"    # id: 3653
    GC_HQ = "Geocaching HQ", "Geocaching HQ"                                                # id: 3773
    GC_HQ_CELEBRATION = "Geocaching HQ Celebration", "Geocaching HQ Celebration"            # id: 3774
    GC_HQ_BLOCK_PARTY = "Geocaching HQ Block Party", "Geocaching HQ Block Party"            # id: 4738
    PROJECT_APE = "Project A.P.E.", "Project A.P.E."                                        # id: 9
    BENCHMARK = "NGS Benchmark", "NGS Benchmark"                                            # Retired 2023-01-04; GSAK code: G
    # OC-only types (no GC equivalent)
    DRIVE_IN = "Drive-In", "Drive-In Cache"                                                 # OC: Drive-In
    MATH_PHYSICS = "Math/Physics", "Math/Physics Cache"                                     # OC: Math/Physics
    MOVING = "Moving", "Moving Cache"                                                       # OC: Moving
    OWN = "Own", "Own Cache"                                                                # OC: Own
    PODCAST = "Podcast", "Podcast Cache"                                                    # OC: Podcast
    UNKNOWN = "Unknown", "Unknown"


class CacheSize(models.TextChoices):
    # GC API sizes (id noted); OC size2 values noted in comments
    # Ordered by physical size (ascending), then non-physical, then meta values
    NANO = "Nano", "Nano"               # OC only: nano
    MICRO = "Micro", "Micro"            # GC id:2; OC: micro
    SMALL = "Small", "Small"            # GC id:8; OC: small
    REGULAR = "Regular", "Regular"      # GC id:3; OC: regular
    LARGE = "Large", "Large"            # GC id:4; OC: large
    XLARGE = "XLarge", "X-Large"        # OC only: xlarge
    VIRTUAL = "Virtual", "Virtual"      # GC id:5; no physical container
    OTHER = "Other", "Other"            # GC id:6; OC: other
    UNKNOWN = "Unknown", "Unknown"      # GC id:1; shown when size not set
    NONE = "None", "None"               # OC only: none (e.g. EarthCache)


class CacheStatus(models.TextChoices):
    UNPUBLISHED = "Unpublished", "Unpublished"   # GC only
    ACTIVE = "Active", "Active"                  # GC: Active; OC: Available
    DISABLED = "Disabled", "Disabled"            # GC: Disabled; OC: Temporarily unavailable
    LOCKED = "Locked", "Locked"                  # GC only
    ARCHIVED = "Archived", "Archived"            # GC + OC


class LogType(models.TextChoices):
    # Finder logs
    FOUND = "Found it", "Found it"                                              # GC id:2; OC: Found it
    DNF = "Didn't find it", "Didn't find it"                                    # GC id:3; OC: Didn't find it
    NOTE = "Write note", "Write note"                                           # GC id:4; OC: Comment
    WILL_ATTEND = "Will Attend", "Will Attend"                                  # GC id:9
    ATTENDED = "Attended", "Attended"                                           # GC id:10; OC: Attended
    WEBCAM_PHOTO = "Webcam Photo Taken", "Webcam Photo Taken"                  # GC id:11
    # Owner/reviewer actions
    NEEDS_MAINTENANCE = "Needs Maintenance", "Needs Maintenance"                # GC id:45
    OWNER_MAINTENANCE = "Owner Maintenance", "Owner Maintenance"                # GC id:46
    UPDATE_COORDINATES = "Update Coordinates", "Update Coordinates"             # GC id:47
    TEMPORARILY_DISABLED = "Temporarily Disable Listing", "Temporarily Disable Listing"  # GC id:22; OC: Temporarily unavailable
    ENABLE = "Enable Listing", "Enable Listing"                                 # GC id:23; OC: Ready to search
    PUBLISH = "Publish Listing", "Publish Listing"                              # GC id:24
    RETRACT = "Retract Listing", "Retract Listing"                              # GC id:25
    ARCHIVE = "Archive", "Archive"                                              # GC id:5; OC: Archived
    PERMANENTLY_ARCHIVED = "Permanently Archived", "Permanently Archived"       # GC id:6
    NEEDS_ARCHIVED = "Needs Archived", "Needs Archived"                         # GC id:7
    UNARCHIVE = "Unarchive", "Unarchive"                                        # GC id:12
    REVIEWER_NOTE = "Post Reviewer Note", "Post Reviewer Note"                  # GC id:18/68
    ANNOUNCEMENT = "Announcement", "Announcement"                               # GC id:74
    SUBMIT_FOR_REVIEW = "submit for review", "Submit for Review"                # GC (lowercase canonical)
    # OC-specific
    OC_TEAM_COMMENT = "OC Team comment", "OC Team Comment"                      # OC X1


class NoteType(models.TextChoices):
    NOTE       = "note",       "Note"        # free-form user note
    FIELD_NOTE = "field_note", "Field note"  # GPS-app draft / field note → future log


class NoteFormat(models.TextChoices):
    PLAIN    = "plain", "Plain text (UTF-8)"
    HTML     = "html",  "HTML"
    MARKDOWN = "md",    "Markdown"


class WaypointType(models.TextChoices):
    PARKING = "Parking", "Parking Area"
    STAGE = "Stage", "Stage of a Multi-Cache"
    QUESTION = "Question", "Question to Answer"
    FINAL = "Final", "Final Location"
    TRAILHEAD = "Trailhead", "Trailhead"
    REFERENCE = "Reference", "Reference Point"
    OTHER = "Other", "Other"


class Tag(models.Model):
    name = models.CharField(max_length=100, unique=True)
    default_ref_point = models.ForeignKey(
        'preferences.ReferencePoint',
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name='default_for_tags',
    )

    def __str__(self):
        return self.name


class Attribute(models.Model):
    class Source(models.TextChoices):
        GC = "gc", "geocaching.com"
        OC = "oc", "opencaching.de"

    source = models.CharField(max_length=2, choices=Source, default=Source.GC)
    attribute_id = models.IntegerField()
    name = models.CharField(max_length=100)
    is_positive = models.BooleanField(default=True)

    class Meta:
        unique_together = ("source", "attribute_id", "is_positive")

    def __str__(self):
        return f"{self.name} ({'yes' if self.is_positive else 'no'})"


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
    last_found_date = models.DateField(null=True, blank=True)

    country = models.CharField(max_length=100, blank=True)
    iso_country_code = models.CharField(max_length=2, blank=True, db_index=True)
    state = models.CharField(max_length=100, blank=True)
    county = models.CharField(max_length=100, blank=True)
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

    # Adventure Lab fields (null for non-ALC caches)
    adventure = models.ForeignKey(
        "Adventure", null=True, blank=True, on_delete=models.SET_NULL, related_name="stages"
    )
    stage_number = models.PositiveSmallIntegerField(null=True, blank=True)
    question_text = models.TextField(blank=True)
    al_stage_uuid = models.CharField(max_length=36, blank=True, db_index=True)  # stage-level UUID from GSAK/lab2gpx
    al_answer_hash = models.CharField(max_length=64, blank=True)  # SHA-256 from AL API
    al_journal_text = models.TextField(blank=True)                # personal journal (GSAK / AL API)

    tags = models.ManyToManyField(Tag, blank=True, related_name="geocaches")
    attributes = models.ManyToManyField(Attribute, blank=True, related_name="geocaches")

    imported_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(gc_code__gt="") | models.Q(oc_code__gt="") | models.Q(al_code__gt=""),
                name="geocache_has_at_least_one_code",
            )
        ]

    # ------------------------------------------------------------------
    # External URL helpers
    # ------------------------------------------------------------------

    # Domain map for OpenCaching instances keyed by 2-letter code prefix.
    _OC_DOMAINS: dict[str, str] = {
        "OC": "www.opencaching.de",
        "OP": "www.opencaching.pl",
        "OK": "www.opencaching.us",
        "ON": "www.opencaching.nl",
        "OB": "opencache.uk",
        "OR": "www.opencaching.ro",
    }

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
            if self.adventure_id and self.adventure and self.adventure.url:
                result.append(("Adventure Lab", self.adventure.url))
        if self.gc_code:
            result.append((
                "geocaching.com",
                f"https://www.geocaching.com/geocache/{self.gc_code}",
            ))
        if self.oc_code:
            prefix = self.oc_code[:2].upper()
            domain = self._OC_DOMAINS.get(prefix, "www.opencaching.de")
            result.append((
                domain.replace("www.", ""),
                f"https://{domain}/viewcache.php?wp={self.oc_code}",
            ))
        return result

    _OC_PREFIX_TO_PLATFORM: dict[str, str] = {
        "OC": "oc_de",
        "OP": "oc_pl",
        "OU": "oc_us",
        "OB": "oc_nl",
        "OK": "oc_uk",
        "OR": "oc_ro",
    }

    @property
    def oc_platform(self) -> str:
        """Return the OC platform id (e.g. 'oc_de') based on the OC code prefix."""
        if not self.oc_code:
            return ""
        prefix = self.oc_code[:2].upper()
        return self._OC_PREFIX_TO_PLATFORM.get(prefix, "oc_de")

    @property
    def refresh_sources(self) -> list[tuple[str, str]]:
        """API sources this cache can be refreshed from: [(label, platform_id), …]."""
        result: list[tuple[str, str]] = []
        if self.gc_code:
            result.append(("GC", "gc"))
        if self.oc_code:
            domain = self._OC_DOMAINS.get(
                self.oc_code[:2].upper(), "www.opencaching.de"
            ).replace("www.", "")
            result.append((domain, self.oc_platform))
        return result

    def save(self, *args, **kwargs):
        # AL parent caches (adventure set, stage_number None) must never be directly
        # marked as found — completion is tracked via the `completed` flag instead.
        if self.found and self.adventure_id is not None and self.stage_number is None:
            raise ValueError(
                f"Cannot set found=True on Adventure Lab parent {self.al_code!r}. "
                "Mark individual stages as found; completed is set automatically."
            )
        super().save(*args, **kwargs)

    def __str__(self):
        code = self.gc_code or self.al_code or self.oc_code
        return f"{code} — {self.name}"


class CorrectedCoordinates(models.Model):
    geocache = models.OneToOneField(
        Geocache, on_delete=models.CASCADE, related_name="corrected_coordinates"
    )
    latitude = models.FloatField()
    longitude = models.FloatField()
    note = models.TextField(blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Corrected coords for {self.geocache}"


class Waypoint(models.Model):
    geocache = models.ForeignKey(Geocache, on_delete=models.CASCADE, related_name="waypoints")
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
    geocache = models.ForeignKey(Geocache, on_delete=models.CASCADE, related_name="logs")
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

    def __str__(self):
        return f"{self.log_type} by {self.user_name} on {self.logged_date}"


class Note(models.Model):
    geocache    = models.ForeignKey(Geocache, on_delete=models.CASCADE, related_name="notes")
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
    geocache = models.ForeignKey(Geocache, on_delete=models.CASCADE, related_name="custom_fields")
    key = models.CharField(max_length=100)
    value = models.TextField(blank=True)

    class Meta:
        unique_together = ("geocache", "key")

    def __str__(self):
        return f"{self.key}={self.value}"


class Image(models.Model):
    geocache = models.ForeignKey(Geocache, on_delete=models.CASCADE, related_name="images")
    url = models.URLField(max_length=500)
    name = models.CharField(max_length=255, blank=True)
    description = models.TextField(blank=True)

    def __str__(self):
        return self.name or self.url


class Adventure(models.Model):
    """Parent record for an Adventure Lab — groups all its stages."""
    code = models.CharField(max_length=20, unique=True, db_index=True)   # LC{base}, e.g. LC28NG
    adventure_guid = models.CharField(max_length=36, blank=True, db_index=True)  # UUID from AL API / lab2gpx

    title = models.CharField(max_length=255, blank=True)
    description = models.TextField(blank=True)
    owner = models.CharField(max_length=255, blank=True)
    latitude = models.FloatField(null=True, blank=True)
    longitude = models.FloatField(null=True, blank=True)
    url = models.URLField(max_length=500, blank=True)
    themes = models.JSONField(default=list, blank=True)
    stage_count = models.PositiveSmallIntegerField(null=True, blank=True)
    status = models.CharField(max_length=20, blank=True)

    imported_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.code} — {self.title or '(untitled)'}"


def recompute_adventure_completed(adventure) -> bool:
    """
    Check whether all stages of an Adventure are found and update the parent's
    `completed` flag accordingly.  Returns True if the adventure is now complete.

    Safe to call from importers and signals — only writes to the parent when
    the `completed` value actually needs to change.
    """
    stages = adventure.stages.filter(stage_number__isnull=False)
    if not stages.exists():
        return False
    all_found = not stages.filter(found=False).exists()
    parent = adventure.stages.filter(stage_number__isnull=True).first()
    if parent is not None and parent.completed != all_found:
        parent.completed = all_found
        parent.save(update_fields=["completed"])
    return all_found


class CacheFusionRecord(models.Model):
    """Tracks the relationship between GC and OC records for the same physical cache."""

    DECISION_FUSE = "fuse"
    DECISION_DONT_FUSE = "dont_fuse"
    DECISION_POSTPONE = "postpone"
    DECISION_CHOICES = [
        (DECISION_FUSE, "Fuse"),
        (DECISION_DONT_FUSE, "Don't fuse"),
        (DECISION_POSTPONE, "Postpone"),
    ]

    gc_code = models.CharField(max_length=20, db_index=True)
    oc_code = models.CharField(max_length=20, db_index=True)
    # True if the OC platform's own data references this GC code (owner-confirmed link).
    # False if the match was found by coordinate proximity only.
    auto_linked = models.BooleanField(default=False)
    user_decision = models.CharField(
        max_length=20, choices=DECISION_CHOICES, null=True, blank=True
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [("gc_code", "oc_code")]

    def __str__(self):
        decision = self.user_decision or "undecided"
        link = "auto" if self.auto_linked else "proximity"
        return f"{self.gc_code}/{self.oc_code} ({link}, {decision})"


class OCExtension(models.Model):
    geocache = models.OneToOneField(
        Geocache, on_delete=models.CASCADE, related_name="oc_extension"
    )
    rating = models.FloatField(null=True, blank=True)
    recommendations = models.IntegerField(null=True, blank=True)
    needs_maintenance = models.BooleanField(default=False)
    trip_time = models.FloatField(null=True, blank=True)
    trip_distance = models.FloatField(null=True, blank=True)
    user_recommended = models.BooleanField(null=True, blank=True)  # True if current user gave an OC recommendation
    req_passwd = models.BooleanField(default=False)
    passphrase = models.TextField(blank=True, default="")  # user-stored passphrase for req_passwd caches
    preview_image_url = models.URLField(max_length=500, blank=True)
    attribution_html = models.TextField(blank=True)       # OC copyright notice (mandatory per OC ToS)
    long_description = models.TextField(blank=True)       # OC description preserved when GC overwrites main
    short_description = models.TextField(blank=True)      # OC short description preserved when GC overwrites main
    # GC code as stated by the OC platform (owner-confirmed cross-reference).
    # Populated from oc:other_code in GPX imports and gc_code in OKAPI responses.
    # Preserved even when the pair is not yet fused.
    related_gc_code = models.CharField(max_length=20, blank=True)

    def __str__(self):
        return f"OC data for {self.geocache}"


class SavedFilter(models.Model):
    name = models.CharField(max_length=100, unique=True)
    params = models.JSONField()
    is_builtin = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["is_builtin", "name"]

    def __str__(self):
        return self.name


class SavedWhereClause(models.Model):
    name = models.CharField(max_length=100, blank=True)  # blank = recent/unnamed
    sql = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]

    def __str__(self):
        return self.name or f"Recent: {self.sql[:50]}"

    @classmethod
    def add_recent(cls, sql: str) -> None:
        """Record sql as most-recently-used; keep at most 10 unnamed entries."""
        from django.utils import timezone
        sql = sql.strip()
        if not sql:
            return
        # Don't duplicate an existing named entry
        if cls.objects.filter(name__gt="", sql=sql).exists():
            return
        obj, created = cls.objects.get_or_create(name="", sql=sql)
        if not created:
            cls.objects.filter(pk=obj.pk).update(updated_at=timezone.now())
        # Prune oldest unnamed beyond 10
        keep_ids = list(
            cls.objects.filter(name="").order_by("-updated_at").values_list("pk", flat=True)[:10]
        )
        cls.objects.filter(name="").exclude(pk__in=keep_ids).delete()


class CacheMapState(models.Model):
    """Persists the last map zoom/pan position for a cache's detail view."""
    geocache = models.OneToOneField(
        Geocache, on_delete=models.CASCADE, related_name="map_state"
    )
    zoom = models.SmallIntegerField()
    lat = models.FloatField()
    lon = models.FloatField()
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Map state for {self.geocache}"


class SavedAreaFilter(models.Model):
    """Named geographic area filter (union of rectangles and circles)."""
    name = models.CharField(max_length=100, unique=True)
    # [{"type":"rect","bbox":[s,w,n,e]}, {"type":"circle","center":[lat,lon],"radius_m":5000}]
    regions = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class DistanceCache(models.Model):
    """Pre-computed distance and bearing from a reference point to a geocache.

    Eliminates the need for per-row Python haversine callbacks in SQLite.
    Recomputed in bulk (~1 s for 50 k caches) when caches are imported or
    the reference point changes.
    """
    geocache = models.ForeignKey(Geocache, on_delete=models.CASCADE)
    ref_point = models.ForeignKey(
        "preferences.ReferencePoint", on_delete=models.CASCADE,
    )
    distance_km = models.FloatField()
    bearing_deg = models.FloatField()

    class Meta:
        unique_together = ("geocache", "ref_point")
        indexes = [
            models.Index(fields=["ref_point", "distance_km"]),
        ]

    def __str__(self):
        return f"{self.geocache} → {self.ref_point}: {self.distance_km:.1f} km"


class SyncState(models.Model):
    """Tracks per-cache sync metadata for each platform."""
    geocache = models.ForeignKey(Geocache, on_delete=models.CASCADE, related_name="sync_states")
    platform = models.CharField(max_length=20)
    last_synced = models.DateTimeField()
    last_modified = models.DateTimeField(null=True, blank=True)
    sync_mode = models.CharField(max_length=10, blank=True)  # light / full
    sync_error = models.CharField(max_length=500, blank=True)

    class Meta:
        unique_together = ("geocache", "platform")

    def __str__(self):
        return f"{self.geocache.gc_code or self.geocache.al_code or self.geocache.oc_code} @ {self.platform}"


class SyncQuota(models.Model):
    """Daily API quota tracking per platform and sync mode."""
    platform = models.CharField(max_length=20)
    mode = models.CharField(max_length=10)        # light / full
    date = models.DateField()
    used = models.IntegerField(default=0)
    limit = models.IntegerField()

    class Meta:
        unique_together = ("platform", "mode", "date")

    def __str__(self):
        return f"{self.platform}/{self.mode} {self.date}: {self.used}/{self.limit}"
