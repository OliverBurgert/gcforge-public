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
