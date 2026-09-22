from django.db import migrations


def add_alc_columns(apps, schema_editor):
    ColumnPreset = apps.get_model("preferences", "ColumnPreset")

    # Insert alc_rating + alc_highly_recommended after answer_is_correct in ALC preset
    alc = ColumnPreset.objects.filter(name="ALC").first()
    if alc:
        cols = list(alc.columns)
        if "alc_rating" not in cols:
            try:
                idx = cols.index("answer_is_correct") + 1
            except ValueError:
                idx = len(cols)
            cols.insert(idx, "alc_rating")
            cols.insert(idx + 1, "alc_highly_recommended")
            alc.columns = cols
            alc.save()

    # Append to Full preset as well
    full = ColumnPreset.objects.filter(name="Full").first()
    if full:
        cols = list(full.columns)
        changed = False
        for key in ("alc_rating", "alc_highly_recommended"):
            if key not in cols:
                cols.append(key)
                changed = True
        if changed:
            full.columns = cols
            full.save()


def remove_alc_columns(apps, schema_editor):
    ColumnPreset = apps.get_model("preferences", "ColumnPreset")
    for name in ("ALC", "Full"):
        preset = ColumnPreset.objects.filter(name=name).first()
        if preset:
            cols = [c for c in preset.columns if c not in ("alc_rating", "alc_highly_recommended")]
            preset.columns = cols
            preset.save()


class Migration(migrations.Migration):
    dependencies = [
        ("preferences", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(add_alc_columns, remove_alc_columns),
    ]
