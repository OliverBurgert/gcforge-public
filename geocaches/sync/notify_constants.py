"""Non-secret GC Instant Notification catalogues.

Cache-type and log-event lookup tables shared between the public notifications
UI (read-only rendering + form choices) and the private website automation in
``gcprivate.notify_web``. No access methods here, so it stays in the public tree.
"""

# Cache-type dropdown: numeric id -> display name.
CACHE_TYPES: dict[int, str] = {
    2: "Traditional Cache",
    3: "Multi-cache",
    4: "Virtual Cache",
    5: "Letterbox Hybrid",
    6: "Event Cache",
    8: "Mystery Cache",
    9: "Project APE Cache",
    11: "Webcam Cache",
    12: "Locationless (Reverse) Cache",
    13: "Cache In Trash Out Event",
    137: "Earthcache",
    453: "Mega-Event Cache",
    1304: "GPS Adventures Exhibit",
    1858: "Wherigo Cache",
    3653: "Community Celebration Event",
    3773: "Groundspeak HQ",
    3774: "Geocaching HQ Celebration",
    4738: "Geocaching HQ Block Party",
    7005: "Giga-Event Cache",
}

# Log-event checkbox list: event id -> display name, in the order the website
# renders them (column-major: left column first, then right).  The position
# IS the cblLogTypeList$<index> slot ASP.NET model-binding expects — get this
# wrong and the server records *no* event subscriptions and rejects the form
# with "You need to choose at least one log option."
LOG_EVENTS: list[tuple[int, str]] = [
    (2,  "Found it"),                     # $0
    (3,  "Didn't find it"),               # $1
    (4,  "Write note"),                   # $2
    (5,  "Archive"),                      # $3
    (7,  "Needs Archived"),               # $4
    (12, "Unarchive"),                    # $5
    (24, "Publish Listing"),              # $6
    (25, "Retract Listing"),              # $7
    (22, "Temporarily Disable Listing"),  # $8
    (23, "Enable Listing"),               # $9
    (47, "Update Coordinates"),           # $10
    (45, "Needs Maintenance"),            # $11
    (46, "Owner Maintenance"),            # $12
]

LOG_EVENT_NAMES: dict[int, str] = dict(LOG_EVENTS)
PUBLISH_EVENT_ID = 24
