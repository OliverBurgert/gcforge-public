"""Adventure Lab theme display.

The AL API returns themes as PascalCase tokens (``FoodDrink``, ``WalkingTour``,
…) which we store raw on :class:`~geocaches.models.Adventure`.  This module
turns a token into a human label + an icon for display, in one place so the
dashboard breakdown and the cache-detail page stay consistent.

Icons are plain emoji so no icon-font asset is needed.  Unknown / newly added
tokens fall back to a spaced-out label (``FooBar`` → "Foo Bar") and a generic
tag icon, so themes we haven't catalogued still read sensibly.
"""

import re

# token -> (label, icon)
THEME_META: dict[str, tuple[str, str]] = {
    "Architecture": ("Architecture", "🏛️"),
    "Art":          ("Art", "🎨"),
    "Cemetery":     ("Cemetery", "🪦"),
    "DrivingTour":  ("Driving Tour", "🚗"),
    "Educational":  ("Educational", "🎓"),
    "FoodDrink":    ("Food & Drink", "🍴"),
    "ForKids":      ("For Kids", "🧒"),
    "Haunted":      ("Haunted", "👻"),
    "Historical":   ("Historical", "🏰"),
    "History":      ("History", "🏰"),
    "Humor":        ("Humor", "😄"),
    "Indoor":       ("Indoor", "🏠"),
    "Music":        ("Music", "🎵"),
    "Mystery":      ("Mystery", "🔍"),
    "Nature":       ("Nature", "🌳"),
    "Nightlife":    ("Nightlife", "🌃"),
    "Park":         ("Park", "🏞️"),
    "Religious":    ("Religious", "⛪"),
    "Scenic":       ("Scenic", "🌄"),
    "Shopping":     ("Shopping", "🛍️"),
    "Sightseeing":  ("Sightseeing", "📷"),
    "Sports":       ("Sports", "⚽"),
    "Trail":        ("Trail", "🥾"),
    "Travel":       ("Travel", "✈️"),
    "WalkingTour":  ("Walking Tour", "🚶"),
    "Water":        ("Water", "🌊"),
    "Wildlife":     ("Wildlife", "🦌"),
}

FALLBACK_ICON = "🏷️"


def theme_display(token: str) -> tuple[str, str]:
    """Return ``(label, icon)`` for one raw theme token."""
    meta = THEME_META.get(token)
    if meta:
        return meta
    label = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", token).strip() or token
    return (label, FALLBACK_ICON)


def theme_badges(themes) -> list[dict]:
    """Map a list of raw theme tokens to ``[{value, label, icon}, …]``.

    Empties are skipped; order is preserved.
    """
    out = []
    for token in themes or []:
        if not token:
            continue
        label, icon = theme_display(token)
        out.append({"value": token, "label": label, "icon": icon})
    return out
