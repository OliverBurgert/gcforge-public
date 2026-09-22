from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('geocaches', '0020_map_hidden_always'),
    ]

    operations = [
        migrations.AddField(
            model_name='geocache',
            name='event_start_time',
            field=models.TimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='geocache',
            name='event_end_time',
            field=models.TimeField(blank=True, null=True),
        ),
    ]
