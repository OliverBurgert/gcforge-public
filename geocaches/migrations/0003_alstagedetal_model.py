"""
Migration 0003: Introduce ALStageDetail model, migrate data from Geocache,
then remove the moved fields from Geocache.
"""
import django.db.models.deletion
from django.db import migrations, models


def migrate_stage_data_forward(apps, schema_editor):
    Geocache = apps.get_model("geocaches", "Geocache")
    ALStageDetail = apps.get_model("geocaches", "ALStageDetail")
    ALJournalEntry = apps.get_model("geocaches", "ALJournalEntry")
    for gc in Geocache.objects.filter(stage_number__isnull=False):
        ALStageDetail.objects.get_or_create(
            geocache=gc,
            defaults={
                "question_text":    getattr(gc, "question_text", ""),
                "answer_hash":      getattr(gc, "al_answer_hash", ""),
                "key_image_url":    getattr(gc, "al_key_image_url", ""),
                "geofencing_radius": getattr(gc, "al_geofencing_radius", None),
                "challenge_type":   getattr(gc, "al_challenge_type", ""),
                "is_final":         getattr(gc, "al_is_final", None),
            },
        )
        journal_text = getattr(gc, "al_journal_text", "") or ""
        if journal_text:
            ALJournalEntry.objects.get_or_create(
                geocache=gc,
                defaults={"journal_message": journal_text},
            )


def migrate_stage_data_backward(apps, schema_editor):
    ALStageDetail = apps.get_model("geocaches", "ALStageDetail")
    for d in ALStageDetail.objects.select_related("geocache").iterator():
        gc = d.geocache
        gc.question_text        = d.question_text
        gc.al_answer_hash       = d.answer_hash
        gc.al_key_image_url     = d.key_image_url
        gc.al_geofencing_radius = d.geofencing_radius
        gc.al_challenge_type    = d.challenge_type
        gc.al_is_final          = d.is_final
        gc.save(update_fields=[
            "question_text", "al_answer_hash", "al_key_image_url",
            "al_geofencing_radius", "al_challenge_type", "al_is_final",
        ])


class Migration(migrations.Migration):

    dependencies = [
        ("geocaches", "0002_alc_stage_fields"),
    ]

    operations = [
        # 1. Create ALStageDetail
        migrations.CreateModel(
            name="ALStageDetail",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("geocache", models.OneToOneField(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="al_detail",
                    to="geocaches.geocache",
                )),
                ("question_text", models.TextField(blank=True)),
                ("answer_hash", models.CharField(blank=True, max_length=64)),
                ("answer_choices", models.JSONField(blank=True, default=list)),
                ("key_image_url", models.URLField(blank=True, max_length=500)),
                ("geofencing_radius", models.IntegerField(blank=True, null=True)),
                ("challenge_type", models.CharField(blank=True, max_length=50)),
                ("is_final", models.BooleanField(blank=True, null=True)),
                ("user_answer", models.TextField(blank=True)),
                ("answer_is_correct", models.BooleanField(blank=True, null=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
        ),
        # 2. Migrate existing data
        migrations.RunPython(migrate_stage_data_forward, migrate_stage_data_backward),
        # 3. Remove moved fields from Geocache
        migrations.RemoveField(model_name="geocache", name="question_text"),
        migrations.RemoveField(model_name="geocache", name="al_answer_hash"),
        migrations.RemoveField(model_name="geocache", name="al_journal_text"),
        migrations.RemoveField(model_name="geocache", name="al_key_image_url"),
        migrations.RemoveField(model_name="geocache", name="al_geofencing_radius"),
        migrations.RemoveField(model_name="geocache", name="al_challenge_type"),
        migrations.RemoveField(model_name="geocache", name="al_is_final"),
    ]
