"""
Lookup tables and pure helper functions for converting between external
format strings (GPX, GC API, OC API) and GCForge model values.

No Django model imports — all functions are pure and fully testable
without a database.
"""

import html
from datetime import date, datetime
from typing import Optional

from geocaches.models import CacheSize, CacheStatus, CacheType, LogType, WaypointType

# ---------------------------------------------------------------------------
# XML namespace constants
# ---------------------------------------------------------------------------

NS_GPX = "http://www.topografix.com/GPX/1/0"
NS_GS = "http://www.groundspeak.com/cache/1/0/1"

# All known Opencaching 2-letter code prefixes.
OC_PREFIXES = frozenset({"OC", "OP", "OK", "ON", "OB", "OR", "OU"})


def gs(tag: str) -> str:
    """Return Clark-notation tag name for the Groundspeak namespace."""
    return f"{{{NS_GS}}}{tag}"


def gpx(tag: str) -> str:
    """Return Clark-notation tag name for the GPX namespace."""
    return f"{{{NS_GPX}}}{tag}"


# ---------------------------------------------------------------------------
# GC PQ GPX → CacheType
# Groundspeak uses slightly different type strings in GPX vs the API.
# ---------------------------------------------------------------------------

CACHE_TYPE_MAP: dict[str, str] = {
    "Traditional Cache":                CacheType.TRADITIONAL,
    "Multi-cache":                      CacheType.MULTI,
    "Virtual Cache":                    CacheType.VIRTUAL,
    "Letterbox Hybrid":                 CacheType.LETTERBOX,
    "Event Cache":                      CacheType.EVENT,
    "Unknown Cache":                    CacheType.MYSTERY,
    "Project APE Cache":                CacheType.PROJECT_APE,
    "Webcam Cache":                     CacheType.WEBCAM,
    "Locationless (Reverse) Cache":     CacheType.LOCATIONLESS,
    "Cache In Trash Out Event":         CacheType.CITO,
    "Earthcache":                       CacheType.EARTH,
    "Earth Cache":                      CacheType.EARTH,
    "Mega-Event Cache":                 CacheType.MEGA_EVENT,
    "GPS Adventures Exhibit":           CacheType.GPS_ADVENTURES,
    "Wherigo Cache":                    CacheType.WHERIGO,
    "Community Celebration Event":      CacheType.COMMUNITY_CELEBRATION,
    "Geocaching HQ":                    CacheType.GC_HQ,
    "Geocaching HQ Celebration":        CacheType.GC_HQ_CELEBRATION,
    "Geocaching HQ Block Party":        CacheType.GC_HQ_BLOCK_PARTY,
    "Giga-Event Cache":                 CacheType.GIGA_EVENT,
    "Lab Cache":                        CacheType.LAB,
}


def gpx_type_to_cache_type(gpx_type: str) -> str:
    """Map a GPX groundspeak:type string to a CacheType value. Falls back to UNKNOWN."""
    return CACHE_TYPE_MAP.get(gpx_type, CacheType.UNKNOWN)


# Reverse: CacheType value → canonical Groundspeak GPX type string
CACHE_TYPE_TO_GPX: dict[str, str] = {
    CacheType.TRADITIONAL:             "Traditional Cache",
    CacheType.MULTI:                   "Multi-cache",
    CacheType.VIRTUAL:                 "Virtual Cache",
    CacheType.LETTERBOX:               "Letterbox Hybrid",
    CacheType.EVENT:                   "Event Cache",
    CacheType.MYSTERY:                 "Unknown Cache",
    CacheType.PROJECT_APE:             "Project APE Cache",
    CacheType.WEBCAM:                  "Webcam Cache",
    CacheType.LOCATIONLESS:            "Locationless (Reverse) Cache",
    CacheType.CITO:                    "Cache In Trash Out Event",
    CacheType.EARTH:                   "Earthcache",
    CacheType.MEGA_EVENT:              "Mega-Event Cache",
    CacheType.GPS_ADVENTURES:          "GPS Adventures Exhibit",
    CacheType.WHERIGO:                 "Wherigo Cache",
    CacheType.COMMUNITY_CELEBRATION:   "Community Celebration Event",
    CacheType.GC_HQ:                   "Geocaching HQ",
    CacheType.GC_HQ_CELEBRATION:       "Geocaching HQ Celebration",
    CacheType.GC_HQ_BLOCK_PARTY:       "Geocaching HQ Block Party",
    CacheType.GIGA_EVENT:              "Giga-Event Cache",
    CacheType.LAB:                     "Lab Cache",
}


def cache_type_to_gpx(cache_type: str) -> str:
    """Map a CacheType value to the canonical Groundspeak GPX type string."""
    return CACHE_TYPE_TO_GPX.get(cache_type, cache_type)


# ---------------------------------------------------------------------------
# OC OKAPI type string → CacheType
# OKAPI returns short English strings; OC.de has two types GC doesn't:
# Drive-In and Math/Physics. "Quiz" maps to Mystery (same concept).
# "Other" and unmapped types fall back to UNKNOWN.
# ---------------------------------------------------------------------------

OKAPI_TYPE_MAP: dict[str, str] = {
    "Traditional":  CacheType.TRADITIONAL,
    "Multi":        CacheType.MULTI,
    "Quiz":         CacheType.MYSTERY,
    "Virtual":      CacheType.VIRTUAL,
    "Webcam":       CacheType.WEBCAM,
    "Event":        CacheType.EVENT,
    "Moving":       CacheType.MOVING,
    "Own":          CacheType.OWN,
    "Podcast":      CacheType.PODCAST,
    "Drive-In":     CacheType.DRIVE_IN,
    "Math/Physics": CacheType.MATH_PHYSICS,
    "Other":        CacheType.UNKNOWN,
}


def okapi_type_to_cache_type(okapi_type: str) -> str:
    """Map an OKAPI cache type string to a CacheType value. Falls back to UNKNOWN."""
    return OKAPI_TYPE_MAP.get(okapi_type, CacheType.UNKNOWN)


# ---------------------------------------------------------------------------
# GC PQ GPX container → CacheSize
# ---------------------------------------------------------------------------

CONTAINER_MAP: dict[str, str] = {
    "Nano":         CacheSize.NANO,
    "Micro":        CacheSize.MICRO,
    "Small":        CacheSize.SMALL,
    "Regular":      CacheSize.REGULAR,
    "Large":        CacheSize.LARGE,
    "Not chosen":   CacheSize.UNKNOWN,
    "Unknown":      CacheSize.UNKNOWN,
    "Virtual":      CacheSize.VIRTUAL,
    "Other":        CacheSize.OTHER,
}


def gpx_container_to_size(container: str) -> str:
    """Map a GPX groundspeak:container string to a CacheSize value. Falls back to UNKNOWN."""
    return CONTAINER_MAP.get(container, CacheSize.UNKNOWN)


# ---------------------------------------------------------------------------
# GPX archived/available attributes → CacheStatus
# ---------------------------------------------------------------------------

def gpx_attrs_to_status(archived: str, available: str) -> str:
    """
    Derive CacheStatus from the archived and available attributes on
    <groundspeak:cache>.

    archived="True"                → ARCHIVED
    archived="False", available="False" → DISABLED
    archived="False", available="True"  → ACTIVE
    """
    if archived.lower() == "true":
        return CacheStatus.ARCHIVED
    if available.lower() == "false":
        return CacheStatus.DISABLED
    return CacheStatus.ACTIVE


# ---------------------------------------------------------------------------
# GPX log type string → LogType
# ---------------------------------------------------------------------------

LOG_TYPE_MAP: dict[str, str] = {
    "Found it":                         LogType.FOUND,
    "Didn't find it":                   LogType.DNF,
    "Write note":                       LogType.NOTE,
    "Will Attend":                      LogType.WILL_ATTEND,
    "Attended":                         LogType.ATTENDED,
    "Webcam Photo Taken":               LogType.WEBCAM_PHOTO,
    "Needs Maintenance":                LogType.NEEDS_MAINTENANCE,
    "Owner Maintenance":                LogType.OWNER_MAINTENANCE,
    "Update Coordinates":               LogType.UPDATE_COORDINATES,
    "Temporarily Disable Listing":      LogType.TEMPORARILY_DISABLED,
    "Enable Listing":                   LogType.ENABLE,
    "Publish Listing":                  LogType.PUBLISH,
    "Retract Listing":                  LogType.RETRACT,
    "Archive":                          LogType.ARCHIVE,
    "Permanently Archived":             LogType.PERMANENTLY_ARCHIVED,
    "Needs Archived":                   LogType.NEEDS_ARCHIVED,
    "Unarchive":                        LogType.UNARCHIVE,
    "Post Reviewer Note":               LogType.REVIEWER_NOTE,
    "Announcement":                     LogType.ANNOUNCEMENT,
}


def gpx_log_type_to_log_type(gpx_log_type: str) -> str:
    """Map a GPX groundspeak:type (log) string to a LogType value. Falls back to NOTE."""
    return LOG_TYPE_MAP.get(gpx_log_type, LogType.NOTE)


# ---------------------------------------------------------------------------
# GPX waypoint sym → WaypointType
# ---------------------------------------------------------------------------

SYM_TO_WAYPOINT_TYPE: dict[str, str] = {
    "Parking Area":             WaypointType.PARKING,
    "Physical Stage":           WaypointType.STAGE,
    "Virtual Stage":            WaypointType.STAGE,
    "Stages of a Multicache":   WaypointType.STAGE,
    "Question to Answer":       WaypointType.QUESTION,
    "Final Location":           WaypointType.FINAL,
    "Trailhead":                WaypointType.TRAILHEAD,
    "Reference Point":          WaypointType.REFERENCE,
}


def gpx_sym_to_waypoint_type(sym: str) -> str:
    """Map a GPX <sym> string to a WaypointType value. Falls back to OTHER."""
    return SYM_TO_WAYPOINT_TYPE.get(sym, WaypointType.OTHER)


# ---------------------------------------------------------------------------
# Pure text helpers
# ---------------------------------------------------------------------------

def unescape(text: str) -> str:
    """
    Unescape HTML entities in a string.

    GC PQ GPX double-encodes some characters (e.g. &amp;#252; → &#252; after
    XML parsing → ü after html.unescape). A single pass of html.unescape
    handles both single and double encoding.
    """
    return html.unescape(text)


def parse_gpx_date(s: str) -> Optional[date]:
    """
    Parse an ISO 8601 date/datetime string from a GPX file to a date.

    Handles:
    - "2005-04-28T00:00:00"
    - "2023-01-15T19:00:00Z"
    - "2005-04-28"

    Returns None for empty or unparseable strings.
    """
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.rstrip("Z").split("T")[0]).date()
    except ValueError:
        return None
