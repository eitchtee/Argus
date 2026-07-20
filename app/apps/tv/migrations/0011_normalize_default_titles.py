from django.db import migrations


DEFAULT_LANGUAGES = {
    "tmdb": "en-US",
    "tvdb": "eng",
}


def normalize_show_titles(apps, schema_editor):
    Show = apps.get_model("tv", "Show")
    for show in Show.objects.all().iterator():
        language = DEFAULT_LANGUAGES.get(show.provider)
        if not language:
            continue
        translations = dict(show.translations or {})
        values = dict(translations.get(language, {}))
        name = values.get("name")
        if not name:
            continue
        values["name"] = name
        translations[language] = values
        Show.objects.filter(pk=show.pk).update(
            name=name,
            translations=translations,
        )


def preserve_show_titles(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("tv", "0010_trakt_sync_state"),
    ]

    operations = [
        migrations.RunPython(normalize_show_titles, preserve_show_titles),
    ]
