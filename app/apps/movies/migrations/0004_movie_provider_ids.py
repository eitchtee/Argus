from django.db import migrations, models
from django.db.models import F


def populate_tmdb_ids(apps, schema_editor):
    Movie = apps.get_model("movies", "Movie")
    Movie.objects.filter(provider="tmdb", tmdb_id__isnull=True).update(
        tmdb_id=F("external_id")
    )


def preserve_tmdb_ids(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("movies", "0003_movie_translations"),
    ]

    operations = [
        migrations.AddField(
            model_name="movie",
            name="tmdb_id",
            field=models.CharField(blank=True, max_length=32, null=True),
        ),
        migrations.AddField(
            model_name="movie",
            name="tvdb_id",
            field=models.CharField(blank=True, max_length=32, null=True),
        ),
        migrations.RunPython(populate_tmdb_ids, preserve_tmdb_ids),
    ]
