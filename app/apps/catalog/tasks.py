from django.core.cache import cache
from procrastinate.contrib.django import app

from apps.catalog.languages import (
    FALLBACK_LANGUAGE_OPTIONS,
    language_catalog_cache_key,
    language_catalog_refresh_key,
)
from apps.catalog.localization import (
    PROVIDER_DEFAULT_LANGUAGES,
    merge_translation_maps,
)
from apps.catalog.models import Genre
from apps.catalog.providers.registry import get_provider


@app.task(name="refresh_language_catalog")
def refresh_language_catalog(provider_name: str):
    try:
        options = get_provider(provider_name).list_languages()
        names_by_code = {option.code: option.name for option in options}
        default_code, default_name = FALLBACK_LANGUAGE_OPTIONS[provider_name][0]
        names_by_code.setdefault(default_code, default_name)
        ordered_codes = [default_code] + sorted(
            code for code in names_by_code if code != default_code
        )
        payload = [
            {"code": code, "name": names_by_code[code]}
            for code in ordered_codes
        ]
        cache.set(language_catalog_cache_key(provider_name), payload, timeout=None)
        return payload
    finally:
        cache.delete(language_catalog_refresh_key(provider_name))


@app.task(name="refresh_genre_catalog")
def refresh_genre_catalog(provider_name: str):
    """Translate the provider's genre list once for the whole catalog.

    Genres are a small set shared by every title, but their names are the only
    part of a detail response that varies by language. Collecting them here
    means importing a title no longer has to be repeated once per selectable
    language just to learn what its genres are called.
    """
    provider = get_provider(provider_name)
    default_language = PROVIDER_DEFAULT_LANGUAGES[provider_name]
    if provider.translates_genres:
        languages = dict.fromkeys(
            [default_language, *(option.code for option in provider.list_languages())]
        )
    else:
        languages = {default_language: None}

    collected: dict[str, dict] = {}
    for media_type in ("movie", "tv"):
        for language in languages:
            for dto in provider.fetch_genres(
                media_type=media_type,
                language=language,
            ):
                entry = collected.setdefault(
                    dto.external_id,
                    {"name": "", "translations": {}},
                )
                entry["translations"] = merge_translation_maps(
                    entry["translations"],
                    dto.translations,
                )
                if dto.name and (language == default_language or not entry["name"]):
                    entry["name"] = dto.name

    if not collected:
        return 0

    stored = {
        genre.external_id: genre
        for genre in Genre.objects.filter(provider=provider_name)
    }
    created = []
    updated = []
    for external_id, entry in collected.items():
        genre = stored.get(external_id)
        if genre is None:
            created.append(
                Genre(
                    provider=provider_name,
                    external_id=external_id,
                    name=entry["name"],
                    translations=entry["translations"],
                )
            )
            continue
        genre.translations = merge_translation_maps(
            genre.translations,
            entry["translations"],
        )
        genre.name = entry["name"] or genre.name
        updated.append(genre)

    if created:
        Genre.objects.bulk_create(created, ignore_conflicts=True)
    if updated:
        Genre.objects.bulk_update(updated, ["name", "translations"])
    return len(collected)


@app.periodic(cron="0 5 * * *")
@app.task(name="refresh_genre_catalogs")
def refresh_genre_catalogs(timestamp: int | None = None):
    return [
        refresh_genre_catalog.defer(provider_name=provider_name)
        for provider_name in PROVIDER_DEFAULT_LANGUAGES
    ]
