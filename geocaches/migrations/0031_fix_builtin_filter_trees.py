from django.db import migrations

_FIXES = {
    "To-go": {
        "g": "and",
        "c": [
            {"f": "found", "op": "is_false", "v": True},
            {"f": "completed", "op": "is_false", "v": True},
            {"f": "status", "op": "in", "v": ["Active"]},
            {"g": "or", "c": [
                {"f": "cache_type", "op": "not_in", "v": ["Mystery", "Math/Physics"]},
                {"f": "has_corrected_coordinates", "op": "is_true", "v": True},
            ]},
        ],
    },
    "FTF possible": {
        "g": "and",
        "c": [{"f": "ftf_possible", "op": "is_true", "v": None}],
    },
    "Fused (GC+OC)": {
        "g": "and",
        "c": [
            {"f": "gc_code", "op": "is_not_empty", "v": None},
            {"f": "oc_code", "op": "is_not_empty", "v": None},
        ],
    },
}


def fix_filters(apps, schema_editor):
    SavedFilter = apps.get_model("geocaches", "SavedFilter")
    for name, tree in _FIXES.items():
        SavedFilter.objects.filter(name=name, is_builtin=True).update(tree=tree)


class Migration(migrations.Migration):

    dependencies = [
        ("geocaches", "0030_fix_not_updated_today_filter"),
    ]

    operations = [
        migrations.RunPython(fix_filters, migrations.RunPython.noop),
    ]
