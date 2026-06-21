"""Dashboard "Maps" tab configuration — which choropleth maps show, and in
what order.

Stored as a single ``UserPreference`` JSON blob (key ``dashboard_maps``) so no
migration is needed.  ``world`` and ``continent`` render from the bundled world
GeoJSON; ``country`` and ``county`` require on-demand boundary downloads added
in later phases.
"""
from __future__ import annotations

from .models import UserPreference

DASHBOARD_MAPS_KEY = "dashboard_maps"

# Levels in their default display order.
MAP_LEVELS = ("world", "continent", "country", "county")
# Levels that work offline from the bundled GeoJSON (no download required).
BUNDLED_LEVELS = ("world", "continent")


def _as_int(value, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def default_config() -> list[dict]:
    """Default: only the world map is shown."""
    return normalize([])


def normalize(stored) -> list[dict]:
    """Merge a stored blob over the known levels so added levels/fields are
    always present and ordering is deterministic."""
    items = stored.get("maps", []) if isinstance(stored, dict) else (stored or [])
    by_type = {
        it["type"]: it
        for it in items
        if isinstance(it, dict) and it.get("type") in MAP_LEVELS
    }
    out = []
    for i, lvl in enumerate(MAP_LEVELS):
        st = by_type.get(lvl, {})
        entry = {
            "type": lvl,
            "visible": bool(st.get("visible", lvl == "world")),
            "order": _as_int(st.get("order"), i),
        }
        # "country" + "county" levels carry an optional list of ISO2 codes to
        # show.  None means "all countries that have finds".
        if lvl in ("country", "county"):
            raw = st.get("countries")
            entry["countries"] = raw if isinstance(raw, list) else None
        out.append(entry)
    out.sort(key=lambda m: (m["order"], MAP_LEVELS.index(m["type"])))
    return out


def get_config() -> list[dict]:
    return normalize(UserPreference.get(DASHBOARD_MAPS_KEY))


def save_config(config: list[dict]) -> None:
    UserPreference.set(DASHBOARD_MAPS_KEY, {"maps": normalize(config)})
