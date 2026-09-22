"""Normalise the legacy ``primary_source = "oc"`` marker to the OC platform id.

The OC API normaliser stamped every OC-sourced cache with the literal ``"oc"``
from before per-node platform ids (``oc_de``, ``oc_pl``, …) existed, and the OC
GPX importer hardcoded ``oc_de`` regardless of the code prefix. Code paths that
route by platform (FTF check, notifications, per-node clients) expect the id.
Derive it from the OC code prefix, the same rule ``Geocache.oc_platform`` uses.
"""
from django.db import migrations

# Mirror of geocaches.oc_platforms.OC_PREFIX_TO_PLATFORM at migration time —
# kept inline so the migration stays stable if the module changes shape.
_PREFIX_TO_PLATFORM = {
    "OC": "oc_de",
    "OP": "oc_pl",
    "OK": "oc_uk",
    "OB": "oc_nl",
    "OU": "oc_us",
    "OR": "oc_ro",
}


def forwards(apps, schema_editor):
    Geocache = apps.get_model("geocaches", "Geocache")
    for prefix, platform in _PREFIX_TO_PLATFORM.items():
        Geocache.objects.filter(primary_source="oc", oc_code__istartswith=prefix).update(primary_source=platform)
    # Anything left with "oc" has an unknown prefix (or no OC code): fall back to oc_de,
    # matching platform_for_code()'s default.
    Geocache.objects.filter(primary_source="oc").update(primary_source="oc_de")


class Migration(migrations.Migration):

    dependencies = [
        ("geocaches", "0044_unique_live_cache_codes"),
    ]

    operations = [
        migrations.RunPython(forwards, migrations.RunPython.noop),
    ]
