from django.core.cache import cache


FALLBACK_LANGUAGE_OPTIONS = {
    "tvdb": (("eng", "English"),),
    "tmdb": (("en-US", "English (United States)"),),
}


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
        refresh_language_catalog(provider)
    return FALLBACK_LANGUAGE_OPTIONS[provider]
