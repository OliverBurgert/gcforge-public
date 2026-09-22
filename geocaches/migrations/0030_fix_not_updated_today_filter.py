from django.db import migrations

_TREE = {
    "g": "and",
    "c": [{"f": "updated_at", "op": "not_today", "v": None}],
}


def fix_filter(apps, schema_editor):
    SavedFilter = apps.get_model("geocaches", "SavedFilter")
    SavedFilter.objects.filter(name="Not updated today", is_builtin=True).update(tree=_TREE)


class Migration(migrations.Migration):

    dependencies = [
        ("geocaches", "0029_alter_ocnotification_options_ocnotification_name_and_more"),
    ]

    operations = [
        migrations.RunPython(fix_filter, migrations.RunPython.noop),
    ]
