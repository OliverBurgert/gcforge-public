from django.db import migrations


def create_filter(apps, schema_editor):
    SavedFilter = apps.get_model("geocaches", "SavedFilter")
    SavedFilter.objects.update_or_create(
        name="ALC stages loggable at center point",
        defaults={
            "params": {"flag": "alc_loggable_at_center"},
            "is_builtin": True,
        },
    )


def remove_filter(apps, schema_editor):
    SavedFilter = apps.get_model("geocaches", "SavedFilter")
    SavedFilter.objects.filter(
        name="ALC stages loggable at center point",
        is_builtin=True,
    ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("geocaches", "0007_al_review"),
    ]

    operations = [
        migrations.RunPython(create_filter, remove_filter),
    ]
