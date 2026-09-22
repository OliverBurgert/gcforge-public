from django.db import migrations


def clear_stale_local_icons(apps, schema_editor):
    Trackable = apps.get_model("geocaches", "Trackable")
    Trackable.objects.filter(icon_url__startswith="/trackables/icons/").update(icon_url="")


class Migration(migrations.Migration):

    dependencies = [
        ("geocaches", "0013_cached_image_model"),
    ]

    operations = [
        migrations.RunPython(clear_stale_local_icons, migrations.RunPython.noop),
    ]
