from django.db import migrations


def create_filter(apps, schema_editor):
    SavedFilter = apps.get_model("geocaches", "SavedFilter")
    SavedFilter.objects.update_or_create(
        name="ALC started but incomplete",
        defaults={
            "params": {"flag": "alc_in_progress"},
            "is_builtin": True,
        },
    )


def remove_filter(apps, schema_editor):
    SavedFilter = apps.get_model("geocaches", "SavedFilter")
    SavedFilter.objects.filter(
        name="ALC started but incomplete",
        is_builtin=True,
    ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("geocaches", "0008_builtin_alc_loggable_filter"),
    ]

    operations = [
        migrations.RunPython(create_filter, remove_filter),
    ]
