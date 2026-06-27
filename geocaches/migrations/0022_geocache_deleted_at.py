from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("geocaches", "0021_geocache_event_times"),
    ]

    operations = [
        migrations.AddField(
            model_name="geocache",
            name="deleted_at",
            field=models.DateTimeField(null=True, blank=True, default=None),
        ),
    ]
