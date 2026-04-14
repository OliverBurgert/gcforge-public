"""
Country name / ISO 3166-1 alpha-2 utilities.

Thin wrapper around pycountry with a Latin-script check for Nominatim results.
"""
from __future__ import annotations

import re

import pycountry

# Matches any character outside Latin script (Basic Latin + Latin Extended blocks,
# which cover all diacritics including e.g. Skagafjörður).
# Cyrillic, CJK, Arabic, Devanagari etc. will trigger this.
_NON_LATIN_RE = re.compile(r"[^\u0000-\u024F\u1E00-\u1EFF]")


def is_latin(text: str) -> bool:
    """Return True if *text* contains only Latin-script characters."""
    return not bool(_NON_LATIN_RE.search(text))


# Local-language country names not in pycountry's ISO English dataset.
# Primarily covers names returned by Nominatim in local languages.
_LOCAL_NAMES: dict[str, str] = {
    "Deutschland":              "DE",
    "Allemagne":                "DE",  # French
    "Alemania":                 "DE",  # Spanish
    "Österreich":               "AT",
    "Autriche":                 "AT",  # French
    "Schweiz":                  "CH",
    "Suisse":                   "CH",  # French
    "Svizzera":                 "CH",  # Italian
    "España":                   "ES",
    "Espagne":                  "ES",  # French
    "Polska":                   "PL",
    "Italia":                   "IT",  # Italian
    "Nederland":                "NL",
    "Belgique":                 "BE",  # French
    "België":                   "BE",  # Dutch
    "Tschechien":               "CZ",
    "Tschechische Republik":    "CZ",
    "Czechia":                  "CZ",
    "Frankreich":               "FR",  # German
    "Dänemark":                 "DK",  # German
    "Norwegen":                 "NO",  # German
    "Schweden":                 "SE",  # German
    "Finnland":                 "FI",  # German
    "Ungarn":                   "HU",  # German
    "Kroatien":                 "HR",  # German
    "Slowakei":                 "SK",  # German
    "Slowenien":                "SI",  # German
    "Rumänien":                 "RO",  # German
    "Bulgarien":                "BG",  # German
    "Griechenland":             "GR",  # German
}


def name_to_iso(name: str) -> str:
    """Convert a country name to ISO 3166-1 alpha-2 code (uppercase).

    Tries local-name overrides first, then exact pycountry name/common_name
    match, then pycountry fuzzy search.
    Returns "" if no match is found.
    """
    if not name:
        return ""
    override = _LOCAL_NAMES.get(name)
    if override:
        return override
    country = pycountry.countries.get(name=name)
    if country:
        return country.alpha_2
    country = pycountry.countries.get(common_name=name)
    if country:
        return country.alpha_2
    try:
        results = pycountry.countries.search_fuzzy(name)
        if results:
            return results[0].alpha_2
    except LookupError:
        pass
    return ""


# Per-country suffixes (and prefixes) to strip from state/county names so that
# stored values are short and consistent (e.g. "Los Angeles" not "Los Angeles County").
# Format: (iso_alpha2, field) → list of strings to try stripping.
# Suffixes are stripped first; entries beginning with a space are suffixes,
# entries ending with a space are prefixes.
_ADMIN_AFFIXES: dict[tuple[str, str], list[str]] = {
    # United States — counties
    ("US", "county"): [
        " County", " Parish", " Borough", " Census Area",
        " City and Borough", " Municipality",
    ],
    # Japan — prefectures stored in the state field
    ("JP", "state"): [
        " Prefecture", " Metropolis", " Circuit", " Urban Prefecture",
        " Subprefecture",
    ],
    # Australia — shires/councils in the county field
    ("AU", "county"): [" Shire", " Council", " Region", " Local Government Area"],
    # Canada — regional districts
    ("CA", "county"): [" County", " Regional District", " Region", " District"],
    # United Kingdom
    ("GB", "county"): [" County", " District", " Borough", " Council"],
    # Ireland — "County Cork" prefix style
    ("IE", "county"): ["County "],   # prefix
    # South Africa
    ("ZA", "state"): [" Province"],
    ("ZA", "county"): [" Local Municipality", " Metropolitan Municipality", " District Municipality"],
    # New Zealand
    ("NZ", "county"): [" District", " City", " Region"],
    # Germany — "Landkreis X" prefix only; "Kreis " is not stripped because
    # compound names like "Ilm-Kreis" are legitimate and "Kreis " as a
    # standalone prefix is rare in OSM data.
    ("DE", "county"): ["Landkreis "],  # prefix
}


def strip_admin_suffix(value: str, iso_country_code: str, field: str) -> str:
    """Remove known administrative suffixes/prefixes for cleaner stored names.

    E.g. strip_admin_suffix("Los Angeles County", "US", "county") → "Los Angeles"
         strip_admin_suffix("Osaka Prefecture",   "JP", "state")  → "Osaka"
         strip_admin_suffix("County Cork",        "IE", "county") → "Cork"

    Returns the original value unchanged if no rule matches or stripping would
    leave an empty string.
    """
    if not value:
        return value
    affixes = _ADMIN_AFFIXES.get((iso_country_code.upper(), field), [])
    for affix in affixes:
        if affix.endswith(" "):          # prefix to strip
            if value.startswith(affix):
                stripped = value[len(affix):]
                if stripped:
                    return stripped
        else:                            # suffix to strip
            if value.endswith(affix):
                stripped = value[: -len(affix)]
                if stripped:
                    return stripped
    return value


def iso_to_name(code: str) -> str:
    """Convert ISO 3166-1 alpha-2 code to English country name.

    Returns the code itself as a fallback so displays are never blank.
    """
    if not code:
        return ""
    country = pycountry.countries.get(alpha_2=code.upper())
    if country:
        return getattr(country, "common_name", None) or country.name
    return code
