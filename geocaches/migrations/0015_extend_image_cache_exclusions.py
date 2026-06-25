"""Merge new default exclusions into the existing user preference.

Adds tokens for smileys, GC attribute icons, OC TinyMCE assets, and stat
badges. Preserves any custom patterns the user has already added.

UserPreference.value is JSON-encoded — we parse before merging and
re-encode on save.
"""
import json

from django.db import migrations


_NEW_TOKENS = [
    "/images/icons/",
    "/images/attributes/",
    "/resource2/tinymce/",
    "/StatBar/",
]


def _clean(line: str) -> str:
    """Strip whitespace and the empty-JSON-string artifact ``""`` that an
    earlier buggy version of this migration left at the top of recovered
    values."""
    token = line.strip()
    return "" if token in ('""', "''") else token


def merge_new_tokens(apps, schema_editor):
    UserPreference = apps.get_model("preferences", "UserPreference")
    pref = UserPreference.objects.filter(key="image_cache.exclusions").first()
    if pref is None:
        return

    # Recover from prior bad write that injected raw text into a JSON field.
    try:
        current = json.loads(pref.value)
        if not isinstance(current, str):
            current = ""
    except (json.JSONDecodeError, TypeError):
        # Treat the raw value as the multi-line list (best-effort recovery)
        current = pref.value or ""

    existing = [_clean(line) for line in current.splitlines()]
    existing = [line for line in existing if line]
    seen = set(existing)
    merged = list(existing)
    for tok in _NEW_TOKENS:
        if tok not in seen:
            merged.append(tok)
            seen.add(tok)

    pref.value = json.dumps("\n".join(merged))
    pref.save(update_fields=["value"])


class Migration(migrations.Migration):

    dependencies = [
        ("geocaches", "0014_clear_stale_tb_icon_urls"),
        ("preferences", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(merge_new_tokens, migrations.RunPython.noop),
    ]
