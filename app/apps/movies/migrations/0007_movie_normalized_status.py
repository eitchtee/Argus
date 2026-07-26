from django.db import migrations, models


def _normalized_status(raw_status):
    value = (raw_status or "").strip().casefold()

    if value == "released":
        return "Released"
    if value in {"canceled", "cancelled"}:
        return "Canceled"
    if value in {
        "upcoming",
        "rumored",
        "planned",
        "announced",
        "pre-production",
        "pre production",
        "pre_production",
        "in production",
        "in_production",
        "post production",
        "post-production",
        "post_production",
        "filming",
        "filming / post-production",
        "filming/post-production",
        "filming / post_production",
        "filming_post_production",
        "completed",
    }:
        return "Upcoming"
    return "Unknown"


def populate_normalized_status(apps, schema_editor):
    Movie = apps.get_model("movies", "Movie")
    movies = []
    for movie in Movie.objects.all().only("id", "status"):
        movie.normalized_status = _normalized_status(movie.status)
        movies.append(movie)

    if movies:
        Movie.objects.bulk_update(movies, ["normalized_status"])


def preserve_normalized_status(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("movies", "0006_normalize_default_titles"),
    ]

    operations = [
        migrations.AddField(
            model_name="movie",
            name="normalized_status",
            field=models.CharField(
                choices=[
                    ("Upcoming", "Upcoming"),
                    ("Released", "Released"),
                    ("Canceled", "Canceled"),
                    ("Unknown", "Unknown"),
                ],
                default="Unknown",
                max_length=8,
            ),
        ),
        migrations.RunPython(populate_normalized_status, preserve_normalized_status),
    ]
