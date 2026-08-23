from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from django.utils.formats import get_format


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

_TIME_FORMAT_DIRECTIVES = frozenset("HhGgisuaAOPTZ")


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


def _format_for_user(user, setting_name: str, default: str) -> str:
    configured = getattr(getattr(user, "settings", None), setting_name, default)
    configured = configured or default

    if configured in {"SHORT_DATE_FORMAT", "SHORT_DATETIME_FORMAT"}:
        return get_format(configured, use_l10n=True)
    return configured


def date_format_for_user(user) -> str:
    return _format_for_user(user, "date_format", "SHORT_DATE_FORMAT")


def datetime_format_for_user(user) -> str:
    return _format_for_user(user, "datetime_format", "SHORT_DATETIME_FORMAT")


def time_format_for_user(user=None) -> str:
    """Return the time portion of the user's automatic or chosen datetime format."""
    configured = getattr(
        getattr(user, "settings", None),
        "datetime_format",
        "SHORT_DATETIME_FORMAT",
    )
    if configured == "SHORT_DATETIME_FORMAT":
        return get_format("TIME_FORMAT", use_l10n=True)

    format_string = datetime_format_for_user(user)
    parts = format_string.split()
    time_parts = [
        part
        for part in parts
        if any(
            char in _TIME_FORMAT_DIRECTIVES and (index == 0 or part[index - 1] != "\\")
            for index, char in enumerate(part)
        )
    ]
    if not time_parts:
        return get_format("TIME_FORMAT", use_l10n=True)
    if time_parts[-1] in {"A", "a"} and len(time_parts) > 1:
        return " ".join(time_parts[-2:])
    return time_parts[0]


def season_name(season_number: int) -> str:
    if season_number == 0:
        return "Specials"
    return f"Season {season_number}"


def episode_name(episode_number: int) -> str:
    return f"Episode {episode_number}"


def regional_siblings(
    translations: Mapping[str, Mapping[str, str]],
    language: str,
) -> tuple[str, ...]:
    """Return other stored codes sharing this code's base language.

    Providers publish text per region rather than per language: TMDB has
    ``ar-SA`` but no ``ar``, so a viewer reading ``ar-AE`` has no exact match
    even though Arabic text exists. Sorted for a stable pick when a base
    language has several regions.
    """
    base = str(language or "").split("-", 1)[0]
    if not base:
        return ()
    return tuple(
        sorted(
            code
            for code in translations
            if code != language and code.split("-", 1)[0] == base
        )
    )


def resolve_from_map(
    translations: Mapping[str, Mapping[str, str]],
    field_name: str,
    language: str,
    default_language: str,
    scalar: str = "",
) -> str:
    # A regional sibling of the requested language is a closer match than the
    # provider default, so it is preferred over falling all the way back.
    for code in dict.fromkeys((language, default_language)):
        value = translations.get(code, {}).get(field_name)
        if value:
            return value
        for sibling in regional_siblings(translations, code):
            value = translations.get(sibling, {}).get(field_name)
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


def resolve_title(record, language: str, *, use_original_title: bool = False) -> str:
    field_name = "title" if hasattr(record, "title") else "name"
    if use_original_title:
        original_title = getattr(record, "original_title", "")
        if original_title:
            return original_title
    return resolve_field(record, field_name, language)


@dataclass(frozen=True)
class LocalizedRecord:
    source: Any
    language: str
    overrides: Mapping[str, Any] = field(default_factory=dict)
    use_original_title: bool = False

    @property
    def search_title(self) -> str:
        field_name = "title" if hasattr(self.source, "title") else "name"
        translated_title = resolve_field(self.source, field_name, self.language)
        original_title = getattr(self.source, "original_title", "")
        return " ".join(value for value in (translated_title, original_title) if value)

    def __getattr__(self, name: str):
        if name in self.overrides:
            return self.overrides[name]

        localized_fields = LOCALIZED_FIELDS.get(type(self.source).__name__, ())
        if name in localized_fields:
            if self.use_original_title and name in {"title", "name"}:
                original_title = getattr(self.source, "original_title", "")
                if original_title:
                    return original_title
            return resolve_field(self.source, name, self.language)

        return getattr(self.source, name)
