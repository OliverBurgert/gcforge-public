"""Drop the unused ``Geocache.parent`` self-FK.

Added in 0001 for a stage -> parent link that was never built.  The Adventure
Lab tree is expressed through the shared ``Adventure`` record plus the
``is_al_parent`` flag (0046); nothing outside the admin form ever read this
column, and no code path ever set it.
"""
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("geocaches", "0046_geocache_is_al_parent"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="geocache",
            name="parent",
        ),
    ]
