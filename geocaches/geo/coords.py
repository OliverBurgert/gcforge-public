"""
Coordinate parsing and formatting utilities.

Supported display formats:
  dd   — Decimal Degrees:        48.303150  8.981267
  dmm  — Degrees Decimal Minutes: N 48° 18.189  E 8° 58.876
  dms  — Degrees Minutes Seconds: N 48° 18' 11.3"  E 8° 58' 52.6"

Accepted input formats (parser is format-agnostic):
  48.303150                   plain decimal
  N 48 18.189                 DMM with hemisphere prefix
  N 48° 18.189'               DMM with symbols
  N 48° 18' 11.34"            DMS with symbols
  48 18.189 N                 hemisphere suffix also accepted
"""

import re
from typing import Optional


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

def _dd_parts(deg: float, pos: str, neg: str) -> tuple[str, float]:
    hemisphere = pos if deg >= 0 else neg
    return hemisphere, abs(deg)


def format_dd(lat: float, lon: float) -> tuple[str, str]:
    return f"{lat:.6f}", f"{lon:.6f}"


def format_dmm(lat: float, lon: float) -> tuple[str, str]:
    def _fmt(deg: float, pos: str, neg: str) -> str:
        h, d = _dd_parts(deg, pos, neg)
        minutes = (d - int(d)) * 60
        return f"{h} {int(d):02d}° {minutes:06.3f}'"
    return _fmt(lat, "N", "S"), _fmt(lon, "E", "W")


def format_dms(lat: float, lon: float) -> tuple[str, str]:
    def _fmt(deg: float, pos: str, neg: str) -> str:
        h, d = _dd_parts(deg, pos, neg)
        m_total = (d - int(d)) * 60
        m = int(m_total)
        s = (m_total - m) * 60
        return f"{h} {int(d):02d}° {m:02d}' {s:04.1f}\""
    return _fmt(lat, "N", "S"), _fmt(lon, "E", "W")


def format_coords(lat: float, lon: float, fmt: str = "dd") -> tuple[str, str]:
    """Format a lat/lon pair into the chosen display format."""
    if fmt == "dmm":
        return format_dmm(lat, lon)
    if fmt == "dms":
        return format_dms(lat, lon)
    return format_dd(lat, lon)


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

_HEMI_RE = re.compile(
    r'^([NSEWnsew])\s*(.*?)\s*$|^(.*?)\s*([NSEWnsew])\s*$'
)
_SPLIT_RE = re.compile(r'[°\'"\s,]+')


def parse_coordinate(s: str) -> Optional[float]:
    """
    Parse a single coordinate string (latitude or longitude) to decimal degrees.

    Accepts:
      48.303150                plain decimal (positive = N/E)
      -48.303150               plain decimal (negative = S/W)
      N 48 18.189              DMM hemisphere-prefix
      N 48° 18.189'            DMM with symbols
      N 48° 18' 11.34"         DMS with symbols
      48 18.189 N              hemisphere-suffix
    """
    s = s.strip()
    if not s:
        return None

    # Plain float
    try:
        return float(s)
    except ValueError:
        pass

    # Detect hemisphere
    negative = False
    m = _HEMI_RE.match(s)
    if m:
        if m.group(1):            # prefix hemisphere
            hemi = m.group(1).upper()
            body = m.group(2)
        else:                     # suffix hemisphere
            hemi = m.group(4).upper()
            body = m.group(3)
        negative = hemi in ("S", "W")
    else:
        body = s

    # Strip degree/minute/second symbols and split
    parts = [p for p in _SPLIT_RE.split(body) if p]

    try:
        if len(parts) == 1:
            v = float(parts[0])
        elif len(parts) == 2:
            v = float(parts[0]) + float(parts[1]) / 60
        elif len(parts) >= 3:
            v = float(parts[0]) + float(parts[1]) / 60 + float(parts[2]) / 3600
        else:
            return None
    except ValueError:
        return None

    return -v if negative else v


def parse_lat_lon(lat_str: str, lon_str: str) -> Optional[tuple[float, float]]:
    """Parse two coordinate strings; return (lat, lon) or None on error."""
    lat = parse_coordinate(lat_str)
    lon = parse_coordinate(lon_str)
    if lat is None or lon is None:
        return None
    if not (-90 <= lat <= 90) or not (-180 <= lon <= 180):
        return None
    return lat, lon
