"""
Migration 0005: Move stage_number and al_stage_uuid from Geocache to ALStageDetail.

Data migration: for every Geocache with adventure set and stage_number not null,
create/update the corresponding ALStageDetail row.
"""

from django.db import migrations, models


def migrate_stage_fields_forward(apps, schema_editor):
    Geocache = apps.get_model("geocaches", "Geocache")
    ALStageDetail = apps.get_model("geocaches", "ALStageDetail")

    for gc in Geocache.objects.filter(
        adventure__isnull=False, stage_number__isnull=False
    ).iterator():
        detail, _ = ALStageDetail.objects.get_or_create(geocache=gc)
        changed = False
        if detail.stage_number != gc.stage_number:
            detail.stage_number = gc.stage_number
            changed = True
        if detail.al_stage_uuid != gc.al_stage_uuid:
            detail.al_stage_uuid = gc.al_stage_uuid
            changed = True
        if changed:
            detail.save()


def migrate_stage_fields_backward(apps, schema_editor):
    Geocache = apps.get_model("geocaches", "Geocache")
    ALStageDetail = apps.get_model("geocaches", "ALStageDetail")

    for detail in ALStageDetail.objects.select_related("geocache").iterator():
        gc = detail.geocache
        changed = False
        if gc.stage_number != detail.stage_number:
            gc.stage_number = detail.stage_number
            changed = True
        if gc.al_stage_uuid != detail.al_stage_uuid:
            gc.al_stage_uuid = detail.al_stage_uuid
            changed = True
        if changed:
            gc.save()


class Migration(migrations.Migration):

    dependencies = [
        ("geocaches", "0004_adventure_median_time"),
    ]

    operations = [
        # 1. Add fields to ALStageDetail
        migrations.AddField(
            model_name="alstagedetail",
            name="stage_number",
            field=models.PositiveSmallIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="alstagedetail",
            name="al_stage_uuid",
            field=models.CharField(blank=True, db_index=True, max_length=36),
        ),
        # 2. Copy data
        migrations.RunPython(migrate_stage_fields_forward, migrate_stage_fields_backward),
        # 3. Remove fields from Geocache
        migrations.RemoveField(
            model_name="geocache",
            name="stage_number",
        ),
        migrations.RemoveField(
            model_name="geocache",
            name="al_stage_uuid",
        ),
    ]
