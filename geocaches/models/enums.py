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

    @classmethod
    def event_types(cls) -> frozenset[str]:
        """Event-style cache types (all GC event variants).

        Canonical single source of truth for "is this an event?" checks on the
        normalized ``Geocache.cache_type`` value. Used by the calendar service,
        GPX importer/exporter, FTF/event tools, and the filter compiler.
        """
        return EVENT_CACHE_TYPES


# Frozen set of event cache-type values (normalized ``CacheType`` vocabulary).
# Defined as a module constant so it can be imported directly; also returned by
# ``CacheType.event_types()``.
EVENT_CACHE_TYPES: frozenset[str] = frozenset({
    CacheType.EVENT,
    CacheType.CITO,
    CacheType.MEGA_EVENT,
    CacheType.GIGA_EVENT,
    CacheType.COMMUNITY_CELEBRATION,
    CacheType.GC_HQ,
    CacheType.GC_HQ_CELEBRATION,
    CacheType.GC_HQ_BLOCK_PARTY,
})


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

    @classmethod
    def found_types(cls) -> frozenset[str]:
        """Log types that mark a cache as "found" by the finder.

        Canonical single source of truth for found-state checks (filters, FTF,
        log submission). Includes the event "Attended" and webcam variants.
        """
        return FOUND_LOG_TYPES


# Frozen set of log-type values that count as a "found". Module constant so it
# can be imported directly; also returned by ``LogType.found_types()``.
FOUND_LOG_TYPES: frozenset[str] = frozenset({
    LogType.FOUND,
    LogType.ATTENDED,
    LogType.WEBCAM_PHOTO,
})


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
