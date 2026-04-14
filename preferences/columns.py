"""
Column definitions and built-in presets for the geocache list view.
"""

AVAILABLE_COLUMNS = [
    {"key": "gc_code",         "label": "Code",       "sort": "gc_code"},
    {"key": "name",            "label": "Name",       "sort": "name"},
    {"key": "cache_type",      "label": "Type",       "sort": "cache_type"},
    {"key": "size",            "label": "Size",       "sort": "size"},
    {"key": "difficulty",      "label": "D",          "sort": "difficulty"},
    {"key": "terrain",         "label": "T",          "sort": "terrain"},
    {"key": "status",          "label": "Status",     "sort": "status"},
    {"key": "fav_points",      "label": "♥",          "sort": "fav_points"},
    {"key": "owner",           "label": "Owner",      "sort": "owner"},
    {"key": "placed_by",       "label": "Placed by",  "sort": "placed_by"},
    {"key": "country",         "label": "Country",    "sort": "country"},
    {"key": "state",           "label": "State",      "sort": "state"},
    {"key": "county",          "label": "County",     "sort": "county"},
    {"key": "elevation",       "label": "Elevation",  "sort": "elevation"},
    {"key": "hidden_date",     "label": "Hidden",     "sort": "hidden_date"},
    {"key": "last_found_date", "label": "Last found", "sort": "last_found_date"},
    {"key": "found_date",      "label": "Found",      "sort": "found_date"},
    {"key": "updated_at",      "label": "Updated",    "sort": "updated_at"},
    {"key": "tags",            "label": "Tags",       "sort": None},
    {"key": "flags",           "label": "Flags",      "sort": None},
    {"key": "distance",        "label": "Dist (km)",  "sort": "distance_km"},
    {"key": "bearing",         "label": "Bearing",    "sort": "bearing_deg"},
]

COLUMN_BY_KEY = {c["key"]: c for c in AVAILABLE_COLUMNS}

BUILTIN_PRESETS = {
    "Standard": [
        "gc_code", "name", "cache_type", "size", "difficulty", "terrain",
        "status", "fav_points", "country", "hidden_date", "last_found_date", "tags", "flags",
        "distance", "bearing",
    ],
    "Personal": [
        "gc_code", "name", "cache_type", "difficulty", "terrain",
        "found_date", "tags", "flags", "distance", "bearing",
    ],
    "Compact": ["gc_code", "name", "difficulty", "terrain", "tags", "flags", "distance"],
    "Full":    [c["key"] for c in AVAILABLE_COLUMNS],
}

DEFAULT_PRESET = "Standard"
