from django.db import migrations, models


def seed_country_tags(apps, schema_editor):
    """Seed the 'Countries' tag (available from the start) and apply it to the
    souvenirs the old category heuristic had marked as countries."""
    Souvenir = apps.get_model("geocaches", "Souvenir")
    SouvenirTag = apps.get_model("geocaches", "SouvenirTag")
    country_tag, _ = SouvenirTag.objects.get_or_create(name="Countries")
    for s in Souvenir.objects.filter(category="country"):
        s.tags.add(country_tag)


def drop_country_tags(apps, schema_editor):
    SouvenirTag = apps.get_model("geocaches", "SouvenirTag")
    SouvenirTag.objects.filter(name="Countries").delete()


class Migration(migrations.Migration):

    dependencies = [
        ('geocaches', '0033_alter_ocnotification_platform'),
    ]

    operations = [
        migrations.CreateModel(
            name='SouvenirTag',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=100, unique=True)),
            ],
            options={
                'ordering': ['name'],
            },
        ),
        migrations.AddField(
            model_name='souvenir',
            name='tags',
            field=models.ManyToManyField(blank=True, related_name='souvenirs', to='geocaches.souvenirtag'),
        ),
        migrations.RunPython(seed_country_tags, drop_country_tags),
        migrations.RemoveField(
            model_name='souvenir',
            name='category',
        ),
    ]
