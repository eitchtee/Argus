from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any


PROVIDER_DEFAULT_LANGUAGES = {
    "tvdb": "eng",
    "tmdb": "en-US",
}

LOCALIZED_FIELDS = {
    "Movie": ("title", "overview", "tagline"),
    "Show": ("name", "overview"),
    "Season": ("name", "overview"),
    "Episode": ("name", "overview"),
    "Genre": ("name",),
}


def merge_translation_maps(*maps):
    merged = {}
    for translations in maps:
        for language, values in (translations or {}).items():
            merged.setdefault(language, {}).update(
                {field_name: value for field_name, value in values.items() if value}
            )
    return merged


def metadata_language_for_user(user, provider: str) -> str:
    default = PROVIDER_DEFAULT_LANGUAGES[provider]
    return getattr(user.settings, f"{provider}_metadata_language", default)


def season_name(season_number: int) -> str:
    if season_number == 0:
        return "Specials"
    return f"Season {season_number}"


def episode_name(episode_number: int) -> str:
    return f"Episode {episode_number}"


def resolve_from_map(
    translations: Mapping[str, Mapping[str, str]],
    field_name: str,
    language: str,
    default_language: str,
    scalar: str = "",
) -> str:
    for code in dict.fromkeys((language, default_language)):
        value = translations.get(code, {}).get(field_name)
        if value:
            return value
    return scalar or ""


def resolve_field(record, field_name: str, language: str) -> str:
    record_type = type(record).__name__
    if field_name == "name" and record_type == "Season":
        return season_name(record.season_number)

    provider = getattr(record, "provider", None)
    if provider is None:
        provider = record.show.provider

    value = resolve_from_map(
        record.translations,
        field_name,
        language,
        PROVIDER_DEFAULT_LANGUAGES[provider],
        getattr(record, field_name, ""),
    )
    if value:
        return value
    if field_name == "name" and record_type == "Episode":
        return episode_name(record.episode_number)
    return ""


@dataclass(frozen=True)
class LocalizedRecord:
    source: Any
    language: str
    overrides: Mapping[str, Any] = field(default_factory=dict)

    def __getattr__(self, name: str):
        if name in self.overrides:
            return self.overrides[name]

        localized_fields = LOCALIZED_FIELDS.get(type(self.source).__name__, ())
        if name in localized_fields:
            return resolve_field(self.source, name, self.language)

        return getattr(self.source, name)
