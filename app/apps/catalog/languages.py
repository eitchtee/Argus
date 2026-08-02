from django.conf.locale import LANG_INFO
from django.core.cache import cache


FALLBACK_LANGUAGE_OPTIONS = {
    "tvdb": (("eng", "English"),),
    "tmdb": (("en-US", "English (United States)"),),
}

_ISO_639_2_TO_1 = {
    "ara": "ar",
    "ben": "bn",
    "bul": "bg",
    "cat": "ca",
    "ces": "cs",
    "chi": "zh",
    "cze": "cs",
    "dan": "da",
    "deu": "de",
    "dut": "nl",
    "ell": "el",
    "eng": "en",
    "est": "et",
    "eus": "eu",
    "fas": "fa",
    "fin": "fi",
    "fra": "fr",
    "fre": "fr",
    "glg": "gl",
    "gre": "el",
    "heb": "he",
    "hin": "hi",
    "hrv": "hr",
    "hun": "hu",
    "ind": "id",
    "isl": "is",
    "ita": "it",
    "jpn": "ja",
    "kan": "kn",
    "kor": "ko",
    "lav": "lv",
    "lit": "lt",
    "mal": "ml",
    "mar": "mr",
    "msa": "ms",
    "may": "ms",
    "nld": "nl",
    "nor": "no",
    "pol": "pl",
    "por": "pt",
    "ron": "ro",
    "rum": "ro",
    "rus": "ru",
    "slk": "sk",
    "slo": "sk",
    "slv": "sl",
    "spa": "es",
    "srp": "sr",
    "swe": "sv",
    "tam": "ta",
    "tel": "te",
    "tha": "th",
    "tur": "tr",
    "ukr": "uk",
    "urd": "ur",
    "vie": "vi",
    "zho": "zh",
}


def language_display_name(code: str, fallback: str | None = None) -> str:
    """Return a human-readable name for provider or ISO language codes."""
    normalized = str(code or "").replace("_", "-").casefold()
    if not normalized:
        return fallback or ""

    exact = LANG_INFO.get(normalized)
    if exact and exact.get("name"):
        return exact["name"]

    # Preserve a provider's region-specific label when Django has no exact
    # locale entry (for example, TMDB's ``en-US``).
    if "-" in normalized and fallback and fallback.casefold() != normalized:
        return str(fallback)

    base = normalized.split("-", 1)[0]
    language_code = _ISO_639_2_TO_1.get(base, base)
    language = LANG_INFO.get(language_code)
    if language and language.get("name"):
        return language["name"]
    return str(fallback or code)


def language_choice_display_name(code: str, fallback: str | None = None) -> str:
    """Return a distinct display name for a selectable language variant."""
    normalized = str(code or "").replace("_", "-").casefold()
    if "-" not in normalized:
        return language_display_name(code, fallback)

    base = language_base_code(code)
    base_name = language_display_name(base)
    if base_name == base and fallback:
        base_name = str(fallback).split("(", 1)[0].strip()
    region = normalized.split("-", 1)[1].upper()
    return f"{base_name} ({base}-{region})"


def language_codes_match(left: str | None, right: str | None) -> bool:
    """Compare language codes across provider ISO-639-1/ISO-639-2 formats."""
    left_base, left_region = _language_parts(left)
    right_base, right_region = _language_parts(right)
    return bool(left_base and left_base == right_base) and (
        not left_region or not right_region or left_region == right_region
    )


def language_base_code(code: str | None) -> str:
    """Return the normalized ISO-639-1 base for a provider language code."""
    base, _region = _language_parts(code)
    return base


def _language_parts(code: str | None) -> tuple[str, str | None]:
    normalized = str(code or "").replace("_", "-").casefold()
    if not normalized:
        return "", None
    parts = normalized.split("-", 1)
    base = _ISO_639_2_TO_1.get(parts[0], parts[0])
    return base, parts[1] if len(parts) == 2 else None


def language_catalog_cache_key(provider: str) -> str:
    return f"catalog:{provider}:metadata-languages"


def language_catalog_refresh_key(provider: str) -> str:
    return f"{language_catalog_cache_key(provider)}:refreshing"


def get_language_choices(provider: str) -> tuple[tuple[str, str], ...]:
    cached = cache.get(language_catalog_cache_key(provider))
    if cached is not None:
        return tuple((item["code"], item["name"]) for item in cached)

    from apps.catalog.tasks import refresh_language_catalog

    if cache.add(language_catalog_refresh_key(provider), True, timeout=60):
        refresh_language_catalog.defer(provider_name=provider)
    return FALLBACK_LANGUAGE_OPTIONS[provider]
