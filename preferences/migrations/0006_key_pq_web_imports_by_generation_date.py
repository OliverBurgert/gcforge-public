import json
from datetime import datetime
from zoneinfo import ZoneInfo

from django.db import migrations

_GC_TZ = ZoneInfo("America/Los_Angeles")


def key_pq_imports_by_generation_date(apps, schema_editor):
    """Migrate bare-identifier keys in the 'pq_imported' preference to
    "<identifier>@<pst-date>" — see geocaches.pq.service.pq_import_key().

    Both a website-scraped GUID and an official-API referenceCode are
    *stable* per saved Pocket Query — neither changes when the PQ
    regenerates (confirmed live: the official API returned the exact same
    referenceCode for the same PQ on two occasions 3+ weeks apart). Keying
    'pq_imported' on the bare identifier meant the "Imported" badge stuck
    forever after the first successful import, even once a later
    regeneration was never actually re-downloaded (2026-09-06 report: 2
    undownloaded PQs in a 7-PQ pattern trigger still showed "Imported" from
    a run weeks earlier — and, on the first version of this migration, the 5
    PQs that *were* genuinely re-imported that same day still showed
    "not imported" next page load, because only GUID-shaped keys were
    migrated and their fresh referenceCode-keyed entries were untouched).

    The date comes from each entry's own already-recorded import timestamp,
    converted to PST — non-lossy, since that's the same "which PST day did
    this happen on" conversion the app already uses everywhere for PQ
    readiness (GC allows one PQ run per PST day).
    """
    UserPreference = apps.get_model("preferences", "UserPreference")
    row = UserPreference.objects.filter(key="pq_imported").first()
    if not row:
        return

    imported = json.loads(row.value)
    migrated = {}
    changed = False
    for key, timestamp in imported.items():
        if "@" in key:
            migrated[key] = timestamp
            continue
        try:
            dt = datetime.fromisoformat(timestamp)
        except ValueError:
            migrated[key] = timestamp
            continue
        pst_date = dt.astimezone(_GC_TZ).date().isoformat()
        migrated[f"{key}@{pst_date}"] = timestamp
        changed = True

    if changed:
        row.value = json.dumps(migrated)
        row.save()


def unkey_pq_imports(apps, schema_editor):
    """Reverse: strip the "@<date>" suffix back off every key."""
    UserPreference = apps.get_model("preferences", "UserPreference")
    row = UserPreference.objects.filter(key="pq_imported").first()
    if not row:
        return

    imported = json.loads(row.value)
    reverted = {}
    changed = False
    for key, timestamp in imported.items():
        identifier, sep, _date = key.rpartition("@")
        if sep:
            reverted[identifier] = timestamp
            changed = True
        else:
            reverted[key] = timestamp

    if changed:
        row.value = json.dumps(reverted)
        row.save()


class Migration(migrations.Migration):
    dependencies = [
        ("preferences", "0005_alter_logtemplate_scope"),
    ]

    operations = [
        migrations.RunPython(key_pq_imports_by_generation_date, unkey_pq_imports),
    ]
