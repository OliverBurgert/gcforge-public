"""Non-secret GC reference maps.

Plain lookup tables (cache-type / container-size id → GCForge value) shared by
the GC clients in ``gcprivate`` and by read-only rendering paths in the public
tree (e.g. the Treasures dashboard). They contain no access methods, so they
live here rather than inside ``gcprivate``.
"""

# GC API geocacheType.id → GCForge CacheType.value (DB string)
_TYPE_MAP = {
    2: "Traditional",
    3: "Multi-Cache",
    4: "Virtual",
    5: "Letterbox Hybrid",
    6: "Event",
    8: "Mystery",
    9: "Project A.P.E.",
    11: "Webcam",
    12: "Locationless",
    13: "CITO",
    137: "Earthcache",
    453: "Mega-Event",
    1304: "GPS Adventures Exhibit",
    1858: "Wherigo",
    3653: "Community Celebration Event",
    3773: "Geocaching HQ",
    3774: "Geocaching HQ Celebration",
    4738: "Geocaching HQ Block Party",
    7005: "Giga-Event",
    -1: "Adventure Lab",
}

_SIZE_MAP = {
    1: "Unknown",
    2: "Micro",
    3: "Regular",
    4: "Large",
    5: "Virtual",
    6: "Other",
    8: "Small",
}

# GC API geocacheLogType.id → GCForge LogType value (DB string)
_LOG_TYPE_MAP = {
    2: "Found it",
    3: "Didn't find it",
    4: "Write note",
    5: "Archive",
    6: "Permanently Archived",
    7: "Needs Archived",
    9: "Will Attend",
    10: "Attended",
    11: "Webcam Photo Taken",
    12: "Unarchive",
    18: "Post Reviewer Note",
    22: "Temporarily Disable Listing",
    23: "Enable Listing",
    24: "Publish Listing",
    25: "Retract Listing",
    45: "Needs Maintenance",
    46: "Owner Maintenance",
    47: "Update Coordinates",
    68: "Post Reviewer Note",
    74: "Announcement",
}

# Reverse: GCForge LogType value → GC log-type id (for submission). Shared by
# the official API client (gcprivate/gc_client.py) and the public web/tRPC
# client (geocaches/sync/gc_web/log_client.py) — same numeric ids, GC's DB
# backend is shared between the two surfaces.
_REVERSE_LOG_TYPE_MAP: dict[str, int] = {}
for _id, _val in _LOG_TYPE_MAP.items():
    if _val not in _REVERSE_LOG_TYPE_MAP:  # first id wins (18 before 68 for reviewer note)
        _REVERSE_LOG_TYPE_MAP[_val] = _id
