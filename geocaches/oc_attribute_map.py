"""
OC attribute id normalisation — GPX legacy ids -> OKAPI A-codes.

opencaching.de exposes cache attributes under two numbering schemes:

  * **GPX export** uses Groundspeak-equivalent ids for attributes that have
    a GC counterpart (e.g. 19 = "Ticks") and opencaching.de's own internal
    ids for OC-specific attributes (e.g. 110 = "Active railway nearby").
  * **OKAPI** uses native A-codes (A1..A89).

We canonicalise OC attributes on the **A-code** numbering because that is
what the OKAPI live sync produces and what the c:geo icon set is keyed by
(see ``icons.py``).  The OC GPX importer calls :func:`resolve_oc_attribute`
to translate each parsed ``(gpx_id, is_positive)`` to its A-code + display
name before storing, so GPX- and OKAPI-sourced rows share one key space.

Data is static (opencaching.de's attribute definitions are stable) so the
importer stays offline-friendly.  ``OC_ACODE_NAMES`` and
``_GC_EQUIV_TO_ACODE`` were generated from OKAPI ``services/attrs/
attribute_index`` (name + gc_equivs).  ``_INTERNAL_TO_ACODE`` covers the
OC-specific ids the GPX export uses for attributes with no GC equivalent;
OKAPI doesn't expose those internal ids, so they're mapped by hand.
"""

# A-code numeric id -> English display name (OKAPI attribute_index).
OC_ACODE_NAMES = {
    1: 'Listed at Opencaching only',
    2: 'Near a Survey Marker',
    3: 'Wherigo Cache',
    4: 'Letterbox Cache',
    5: 'GeoHotel Cache',
    6: 'Magnetic Cache',
    7: 'Description contains an audio file',
    8: 'Offset cache',
    9: "Garmin's wireless beacon",
    10: 'Dead Drop USB cache',
    11: 'Has a moving target',
    12: 'a webcam is involved',
    13: 'Other cache type',
    14: 'Investigation required',
    15: 'Field Puzzle / Mystery',
    16: 'Mathematical problem',
    17: 'Ask owner for start conditions',
    18: 'Wheelchair accessible',
    19: 'Park and grab',
    20: 'Access only on foot',
    21: 'Long walk',
    22: 'Swamp, marsh or wading',
    23: 'Hilly area',
    24: 'Some climbing (no gear needed)',
    25: 'Swimming required',
    26: 'Access or parking fee',
    27: 'Bikes allowed',
    28: 'Hidden in natural surroundings (forests, mountains, etc.)',
    29: 'Historic site',
    30: 'Point of interest',
    31: 'Hidden wihin enclosed rooms (caves, buildings etc.)',
    32: 'Hidden under water',
    33: 'Parking area nearby',
    34: 'Public transportation',
    35: 'Drinking water nearby',
    36: 'Public restrooms nearby',
    37: 'Public phone nearby',
    38: 'First aid available',
    39: 'Available 24/7',
    40: 'Not available 24/7',
    41: 'Not recommended at night',
    42: 'Recommended at night',
    43: 'Only at night',
    44: 'All seasons',
    45: 'Only available during specified seasons',
    46: 'Nature preserve / Breeding season',
    47: 'Available during winter',
    48: 'Not available during high tide',
    49: 'Compass required',
    50: 'Bring your own pen',
    51: 'You may need a shovel',
    52: 'Flashlight required',
    53: 'Climbing gear required',
    54: 'Cave equipment required',
    55: 'Diving equipment required',
    56: 'Special tool required',
    57: 'Boat required',
    58: 'No GPS required',
    59: 'Dangerous area',
    60: 'Active railway nearby',
    61: 'Cliff / Rocks',
    62: 'Hunting grounds',
    63: 'Look out for thorns',
    64: 'Look out for ticks',
    65: 'Abandoned mines',
    66: 'Poisonous plants',
    67: 'Dangerous animals',
    68: 'Quick cache',
    69: 'Overnight stay necessary',
    70: 'Bring your children',
    71: 'Suitable for children (10-12 years)',
    72: 'Safari Cache',
    73: 'Available at specified hours (may require access fee)',
    74: 'Stealth required',
    75: 'Aircraft required',
    76: 'Rated on Handicaching.com',
    77: 'Contains a Munzee',
    78: 'Contains advertising',
    79: 'Military training area',
    80: 'Video surveillance',
    81: 'Trackables',
    82: 'Ruin',
    83: 'UV Light Required',
    84: 'NOT available during winter',
    85: 'No dogs',
    86: 'Truck / RV',
    87: 'Historic',
    88: 'Treeclimbing',
    89: 'Handicap: Blind',
}

# (gc_equiv_id, is_positive) -> A-code  (from OKAPI gc_equivs).
_GC_EQUIV_TO_ACODE = {
    (1, False): 85,
    (2, True): 26,
    (3, True): 53,
    (4, True): 57,
    (5, True): 55,
    (6, True): 70,
    (9, True): 21,
    (10, True): 24,
    (11, True): 22,
    (12, True): 25,
    (13, False): 40,
    (13, True): 39,
    (14, False): 41,
    (14, True): 42,
    (15, False): 84,
    (15, True): 47,
    (17, True): 66,
    (18, True): 67,
    (19, True): 64,
    (20, True): 65,
    (21, True): 61,
    (22, True): 62,
    (23, True): 59,
    (24, True): 18,
    (25, True): 33,
    (26, True): 34,
    (27, True): 35,
    (28, True): 36,
    (29, True): 37,
    (32, True): 27,
    (39, True): 63,
    (40, True): 74,
    (44, True): 52,
    (46, True): 86,
    (47, True): 15,
    (48, True): 83,
    (51, True): 56,
    (52, True): 43,
    (53, True): 19,
    (54, True): 82,
    (60, True): 9,
    (62, False): 44,
    (62, True): 45,
    (64, True): 88,
}

# opencaching.de internal id -> A-code, for OC-specific attributes with no
# GC equivalent (OKAPI doesn't expose these internal ids — mapped by hand,
# verified against the OKAPI display names).
_INTERNAL_TO_ACODE = {
    106: 1,    # Listed at Opencaching only
    110: 60,   # Active railway nearby
    127: 23,   # Hilly area
    130: 30,   # Point of interest
    131: 11,   # Has a moving target
    132: 12,   # a webcam is involved
    133: 31,   # Hidden within enclosed rooms
    134: 32,   # Hidden under water
    135: 58,   # No GPS required
    142: 48,   # Not available during high tide
    147: 49,   # Compass required
    154: 14,   # Investigation required
    156: 16,   # Mathematical problem
    161: 72,   # Safari Cache
}


def resolve_oc_attribute(gpx_id: int, is_positive: bool) -> tuple[int, str] | None:
    """Translate an OC GPX attribute id to ``(acode, display_name)``.

    Tries the GC-equivalent map first (with polarity), then the OC-internal
    map.  Returns ``None`` when the id isn't known — the caller keeps the
    original id/name so no data is lost (rare; only affects attributes
    absent from both maps).
    """
    acode = _GC_EQUIV_TO_ACODE.get((gpx_id, is_positive))
    if acode is None:
        acode = _INTERNAL_TO_ACODE.get(gpx_id)
    if acode is None:
        return None
    return acode, OC_ACODE_NAMES.get(acode, f"A{acode}")
