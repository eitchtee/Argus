from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("tv", "0014_seed_legacy_media_artwork"),
    ]

    operations = [
        migrations.AddField(
            model_name="show",
            name="original_title",
            field=models.CharField(blank=True, max_length=255),
        ),
        migrations.AddField(
            model_name="show",
            name="original_language",
            field=models.CharField(blank=True, max_length=16),
        ),
    ]
