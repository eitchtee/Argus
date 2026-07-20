from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("movies", "0004_movie_provider_ids"),
    ]

    operations = [
        migrations.AddField(
            model_name="movie",
            name="trakt_id",
            field=models.CharField(blank=True, max_length=32, null=True, unique=True),
        ),
    ]
