"""OC platform lookup helpers — shared constants and grouping utilities."""

OC_DOMAINS: dict[str, str] = {
    "OC": "www.opencaching.de",
    "OP": "www.opencaching.pl",
    "OU": "www.opencaching.us",
    "ON": "www.opencaching.nl",
    "OB": "www.opencaching.nl",
    "OK": "opencache.uk",
    "OR": "www.opencaching.ro",
}

OC_PREFIX_TO_PLATFORM: dict[str, str] = {
    "OC": "oc_de",
    "OP": "oc_pl",
    "OU": "oc_us",
    "OB": "oc_nl",
    "OK": "oc_uk",
    "OR": "oc_ro",
}


def platform_for_code(code: str) -> str:
    """Return the OC platform id (e.g. 'oc_de') for an OC cache code."""
    return OC_PREFIX_TO_PLATFORM.get(code[:2].upper(), "oc_de")


def group_by_platform(codes) -> dict[str, list[str]]:
    """Group OC cache codes by platform id.

    Returns a dict mapping platform_id → list of codes.
    Codes that don't match a known prefix default to 'oc_de'.
    """
    result: dict[str, list[str]] = {}
    for code in codes:
        result.setdefault(platform_for_code(code), []).append(code)
    return result
