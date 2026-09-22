"""
Icon mapping registry for GCForge.

Maps cache types, waypoint types, and attributes to icon filenames
for each supported icon set. The active icon set is controlled by the
``icon_set`` user preference.
"""

from django.templatetags.static import static

# ---------------------------------------------------------------------------
# Icon set registry
# ---------------------------------------------------------------------------

ICON_SET_CHOICES = [
    ("text", "Text (no icons)"),
    ("cgeo", "c:geo icons"),
]

# ---------------------------------------------------------------------------
# c:geo cache type colors (from res/values/colors.xml + CacheType.java)
# Android #AARRGGBB → CSS hex (drop alpha prefix)
# ---------------------------------------------------------------------------

_CGEO_TYPE_COLORS = {
    "Traditional":                      "#388E3C",   # cacheType_traditional
    "Multi-Cache":                      "#F57C00",   # cacheType_multi
    "Mystery":                          "#303f9f",   # cacheType_mystery
    "Virtual":                          "#0288d1",   # cacheType_virtual
    "Letterbox Hybrid":                 "#303f9f",   # cacheType_mystery
    "Earthcache":                       "#0288d1",   # cacheType_virtual
    "Event":                            "#d32f2f",   # cacheType_event
    "CITO":                             "#d32f2f",   # cacheType_event
    "Webcam":                           "#0288d1",   # cacheType_virtual
    "Wherigo":                          "#303f9f",   # cacheType_mystery
    "Adventure Lab":                    "#7b1fa2",   # cacheType_lab
    "Mega-Event":                       "#d32f2f",   # cacheType_event
    "Giga-Event":                       "#d32f2f",   # cacheType_event
    "Locationless":                     "#616161",   # cacheType_unknown
    "GPS Adventures Exhibit":           "#afb42b",   # cacheType_special
    "Community Celebration Event":      "#d32f2f",   # cacheType_event
    "Geocaching HQ":                    "#afb42b",   # cacheType_special
    "Geocaching HQ Celebration":        "#d32f2f",   # cacheType_event
    "Geocaching HQ Block Party":        "#d32f2f",   # cacheType_event
    "Project A.P.E.":                   "#afb42b",   # cacheType_special
    "NGS Benchmark":                    "#616161",   # cacheType_unknown
    "Drive-In":                         "#616161",   # cacheType_unknown
    "Math/Physics":                     "#303f9f",   # cacheType_mystery
    "Moving":                           "#616161",   # cacheType_unknown
    "Own":                              "#f7991d",   # cacheType_cgeo
    "Podcast":                          "#616161",   # cacheType_unknown
    "Unknown":                          "#616161",   # cacheType_unknown
}

# ---------------------------------------------------------------------------
# c:geo cache type mapping   CacheType.value -> filename (without .svg)
# ---------------------------------------------------------------------------

_CGEO_CACHE_TYPES = {
    "Traditional":                      "traditional",
    "Multi-Cache":                      "multi",
    "Mystery":                          "mystery",
    "Virtual":                          "virtual",
    "Letterbox Hybrid":                 "letterbox",
    "Earthcache":                       "earth",
    "Event":                            "event",
    "CITO":                             "cito",
    "Webcam":                           "webcam",
    "Wherigo":                          "wherigo",
    "Adventure Lab":                    "advlab",
    "Mega-Event":                       "mega",
    "Giga-Event":                       "giga",
    "Locationless":                     "locationless",
    "GPS Adventures Exhibit":           "maze",
    "Community Celebration Event":      "specialevent",
    "Geocaching HQ":                    "hq",
    "Geocaching HQ Celebration":        "event_hq",
    "Geocaching HQ Block Party":        "event_blockparty",
    "Project A.P.E.":                   "ape",
    "NGS Benchmark":                    "benchmark",
    "Drive-In":                         "drivein",
    "Math/Physics":                     "mathphysics",
    "Moving":                           "moving",
    "Own":                              "own",
    "Podcast":                          "podcast",
    "Unknown":                          "unknown",
}

# ---------------------------------------------------------------------------
# c:geo waypoint type mapping   WaypointType.value -> filename
# ---------------------------------------------------------------------------

_CGEO_WAYPOINT_TYPES = {
    "Parking":    "pkg",
    "Stage":      "stage",
    "Question":   "puzzle",
    "Final":      "flag",
    "Trailhead":  "trailhead",
    "Reference":  "waypoint",
    "Other":      "waypoint",
}

# ---------------------------------------------------------------------------
# c:geo attribute mapping   (source, attribute_id) -> internal_name
#
# Built from docs/cgeo-master/main/project/attributes/iconlist.txt
# GC attrs: keyed by ("gc", gcid)
# OC attrs: keyed by ("oc", acode)   (acode = OKAPI A-code numeric part)
# ---------------------------------------------------------------------------

_CGEO_ATTRIBUTES: dict[tuple[str, int], str] = {
    # GC attributes (source="gc", attribute_id=gcid)
    ("gc", 1):  "dogs",
    ("gc", 2):  "fee",
    ("gc", 3):  "rappelling",
    ("gc", 4):  "boat",
    ("gc", 5):  "scuba",
    ("gc", 6):  "kids",
    ("gc", 7):  "onehour",
    ("gc", 8):  "scenic",
    ("gc", 9):  "hiking",
    ("gc", 10): "climbing",
    ("gc", 11): "wading",
    ("gc", 12): "swimming",
    ("gc", 13): "available",
    ("gc", 14): "night",
    ("gc", 15): "winter",
    ("gc", 17): "poisonoak",
    ("gc", 18): "dangerousanimals",
    ("gc", 19): "ticks",
    ("gc", 20): "mine",
    ("gc", 21): "cliff",
    ("gc", 22): "hunting",
    ("gc", 23): "danger",
    ("gc", 24): "wheelchair",
    ("gc", 25): "parking",
    ("gc", 26): "public",
    ("gc", 27): "water",
    ("gc", 28): "restrooms",
    ("gc", 29): "phone",
    ("gc", 30): "picnic",
    ("gc", 31): "camping",
    ("gc", 32): "bicycles",
    ("gc", 33): "motorcycles",
    ("gc", 34): "quads",
    ("gc", 35): "jeeps",
    ("gc", 36): "snowmobiles",
    ("gc", 37): "horses",
    ("gc", 38): "campfires",
    ("gc", 39): "thorn",
    ("gc", 40): "stealth",
    ("gc", 41): "stroller",
    ("gc", 42): "firstaid",
    ("gc", 43): "cow",
    ("gc", 44): "flashlight",
    ("gc", 45): "landf",
    ("gc", 46): "rv",
    ("gc", 47): "field_puzzle",
    ("gc", 48): "uv",
    ("gc", 49): "snowshoes",
    ("gc", 50): "skiis",
    ("gc", 51): "s_tool",
    ("gc", 52): "nightcache",
    ("gc", 53): "parkngrab",
    ("gc", 54): "abandonedbuilding",
    ("gc", 55): "hike_short",
    ("gc", 56): "hike_med",
    ("gc", 57): "hike_long",
    ("gc", 58): "fuel",
    ("gc", 59): "food",
    ("gc", 60): "wirelessbeacon",
    ("gc", 61): "partnership",
    ("gc", 62): "seasonal",
    ("gc", 63): "touristok",
    ("gc", 64): "treeclimbing",
    ("gc", 65): "frontyard",
    ("gc", 66): "teamwork",
    ("gc", 67): "geotour",
    ("gc", 69): "bonuscache",
    ("gc", 70): "powertrail",
    ("gc", 71): "challengecache",
    ("gc", 72): "hqsolutionchecker",
    # OC attributes (source="oc", attribute_id=acode)
    ("oc", 1):  "oc_only",
    ("oc", 2):  "survey_marker",
    ("oc", 3):  "wherigo",
    ("oc", 4):  "letterbox",
    ("oc", 5):  "geohotel",
    ("oc", 6):  "magnetic",
    ("oc", 7):  "audio_cache",
    ("oc", 8):  "offset_cache",
    ("oc", 9):  "wirelessbeacon",
    ("oc", 10): "usb_cache",
    ("oc", 11): "moving_target",
    ("oc", 12): "webcam",
    ("oc", 13): "other_cache",
    ("oc", 14): "investigation",
    ("oc", 15): "puzzle",
    ("oc", 16): "arithmetic",
    ("oc", 17): "ask_owner",
    ("oc", 18): "wheelchair",
    ("oc", 19): "parkngrab",
    ("oc", 20): "pedestrian_only",
    ("oc", 21): "hiking",
    ("oc", 22): "swamp",
    ("oc", 23): "hills",
    ("oc", 24): "easy_climbing",
    ("oc", 25): "swimming",
    ("oc", 26): "fee",
    ("oc", 27): "bicycles",
    ("oc", 28): "nature_cache",
    ("oc", 29): "historic_site",
    ("oc", 30): "poi",
    ("oc", 31): "inside",
    ("oc", 32): "in_water",
    ("oc", 33): "parking",
    ("oc", 34): "public",
    ("oc", 35): "water",
    ("oc", 36): "restrooms",
    ("oc", 37): "phone",
    ("oc", 38): "syringe",
    ("oc", 39): "available",
    ("oc", 40): "specific_times",
    ("oc", 41): "day",
    ("oc", 42): "night",
    ("oc", 43): "nightcache",
    ("oc", 44): "all_seasons",
    ("oc", 45): "seasonal",
    ("oc", 46): "breeding",
    ("oc", 47): "snow_proof",
    ("oc", 48): "tide",
    ("oc", 49): "compass",
    ("oc", 50): "byop",
    ("oc", 51): "shovel",
    ("oc", 52): "flashlight",
    ("oc", 53): "rappelling",
    ("oc", 54): "cave",
    ("oc", 55): "scuba",
    ("oc", 56): "s_tool",
    ("oc", 57): "boat",
    ("oc", 58): "no_gps",
    ("oc", 59): "danger",
    ("oc", 60): "railway",
    ("oc", 61): "cliff",
    ("oc", 62): "hunting",
    ("oc", 63): "thorn",
    ("oc", 64): "ticks",
    ("oc", 65): "mine",
    ("oc", 66): "poisonoak",
    ("oc", 67): "dangerousanimals",
    ("oc", 68): "quick_cache",
    ("oc", 69): "overnight",
    ("oc", 70): "kids_2",
    ("oc", 71): "kids",
    ("oc", 72): "safari_cache",
    ("oc", 73): "specific_access",
    ("oc", 74): "stealth",
    ("oc", 75): "aircraft",
    ("oc", 76): "handicaching",
    ("oc", 77): "munzee",
    ("oc", 78): "ads",
    ("oc", 79): "military_area",
    ("oc", 80): "video_surveil",
    ("oc", 81): "trackables",
    ("oc", 82): "abandonedbuilding",
    ("oc", 83): "uv",
    ("oc", 84): "winter",
    ("oc", 85): "dogs",
    ("oc", 86): "rv",
    ("oc", 87): "historic",
    ("oc", 88): "treeclimbing",
    ("oc", 89): "blind_people",
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_icon_set_choices():
    return ICON_SET_CHOICES


def get_cache_type_color(cache_type: str) -> str:
    """Return the c:geo background color hex for a cache type."""
    return _CGEO_TYPE_COLORS.get(cache_type, "#616161")


def get_cache_type_icon_url(cache_type: str, icon_set: str) -> str | None:
    if icon_set == "text" or not icon_set:
        return None
    if icon_set == "cgeo":
        name = _CGEO_CACHE_TYPES.get(cache_type)
        if name:
            return static(f"icons/cgeo/types/{name}.svg")
    return None


def get_waypoint_type_icon_url(wpt_type: str, icon_set: str) -> str | None:
    if icon_set == "text" or not icon_set:
        return None
    if icon_set == "cgeo":
        name = _CGEO_WAYPOINT_TYPES.get(wpt_type)
        if name:
            return static(f"icons/cgeo/waypoints/{name}.svg")
    return None


def get_attribute_icon_url(source: str, attribute_id: int, icon_set: str) -> str | None:
    if icon_set == "text" or not icon_set:
        return None
    if icon_set == "cgeo":
        name = _CGEO_ATTRIBUTES.get((source, attribute_id))
        if name:
            return static(f"icons/cgeo/attributes/{name}.svg")
    return None


def get_cache_type_icon_name(cache_type: str, icon_set: str) -> str | None:
    """Return the bare icon filename (no path/extension) for JS map usage."""
    if icon_set == "text" or not icon_set:
        return None
    if icon_set == "cgeo":
        return _CGEO_CACHE_TYPES.get(cache_type)
    return None
