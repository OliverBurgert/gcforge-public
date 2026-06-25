import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0001_initial'),
        ('geocaches', '0031_fix_builtin_filter_trees'),
    ]

    operations = [
        migrations.CreateModel(
            name='Souvenir',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('gc_id', models.IntegerField(help_text='GC API souvenir id', unique=True)),
                ('title', models.CharField(max_length=255)),
                ('description', models.TextField(blank=True)),
                ('image_path', models.CharField(blank=True, max_length=500)),
                ('thumb_image_path', models.CharField(blank=True, max_length=500)),
                ('url', models.CharField(blank=True, max_length=500)),
                ('found_date', models.DateTimeField(blank=True, null=True)),
                ('category', models.CharField(blank=True, db_index=True, max_length=50)),
                ('extra', models.JSONField(blank=True, default=dict)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('account', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='souvenirs', to='accounts.useraccount')),
            ],
            options={
                'ordering': ['-found_date', 'title'],
            },
        ),
    ]
