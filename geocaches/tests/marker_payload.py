"""Decoder for the compact /map/markers/ wire format, for tests.

The endpoint sends ``{"fields": [...], "rows": [[...], ...], "count": N}``
(see geocaches/views/map.py MARKER_FIELDS). ``decode_markers()`` expands that
back into one dict per marker — the shape assertions read most naturally
against — mirroring what ``_gcfDecodeMarkers`` does in static/js/cache-map.js.
"""
import json


def decode_markers(response) -> list[dict]:
    """Expand a map_markers response into a list of per-marker dicts."""
    payload = json.loads(response.content)
    fields = payload["fields"]
    # Trailing nulls are trimmed server side, so rows are allowed to be
    # shorter than ``fields`` — zip stops at the shorter of the two.
    return [
        dict(zip(fields, row, strict=False))
        for row in payload["rows"]
    ]
