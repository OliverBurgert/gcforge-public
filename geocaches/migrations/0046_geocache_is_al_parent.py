"""Add the explicit ``Geocache.is_al_parent`` flag and backfill it.

An Adventure Lab parent used to be recognised by the *absence* of an
ALStageDetail row (``services.adventures.is_al_parent``), which cost a query per
call whenever the relation wasn't selected and spread the same rule across a
dozen querysets.  The flag makes it explicit; ``services.save_alc``'s
``_upsert_parent_geocache`` — the only function that creates a parent row —
maintains it from here on.
"""
from django.db import migrations, models


def forwards(apps, schema_editor):
    """Flag every row that today's derived rule calls an AL parent.

    Deliberately not filtered by ``deleted_at``: trashed parents keep their
    stages and must come back as parents when restored.  ``_base_manager`` is
    the unfiltered manager on both the historical and the real model, so this
    sees soft-deleted rows either way — ``objects`` is a ``LiveCacheManager``
    that would silently skip them.
    """
    Geocache = apps.get_model("geocaches", "Geocache")
    Geocache._base_manager.filter(
        adventure__isnull=False, al_detail__isnull=True,
    ).update(is_al_parent=True)


class Migration(migrations.Migration):

    dependencies = [
        ("geocaches", "0045_normalise_oc_primary_source"),
    ]

    operations = [
        migrations.AddField(
            model_name="geocache",
            name="is_al_parent",
            field=models.BooleanField(db_index=True, default=False),
        ),
        migrations.RunPython(forwards, migrations.RunPython.noop),
    ]
