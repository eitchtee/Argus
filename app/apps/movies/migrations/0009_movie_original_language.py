from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("movies", "0008_seed_legacy_media_artwork"),
    ]

    operations = [
        migrations.AddField(
            model_name="movie",
            name="original_language",
            field=models.CharField(blank=True, max_length=16),
        ),
    ]
