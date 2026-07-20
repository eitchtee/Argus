from django.db import migrations


DEFAULT_LANGUAGES = {
    "tmdb": "en-US",
    "tvdb": "eng",
}


def normalize_movie_titles(apps, schema_editor):
    Movie = apps.get_model("movies", "Movie")
    for movie in Movie.objects.all().iterator():
        language = DEFAULT_LANGUAGES.get(movie.provider)
        if not language:
            continue
        translations = dict(movie.translations or {})
        values = dict(translations.get(language, {}))
        title = values.get("title") or movie.original_title
        if not title:
            continue
        values["title"] = title
        translations[language] = values
        Movie.objects.filter(pk=movie.pk).update(
            title=title,
            translations=translations,
        )


def preserve_movie_titles(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("movies", "0005_movie_trakt_id"),
    ]

    operations = [
        migrations.RunPython(normalize_movie_titles, preserve_movie_titles),
    ]
