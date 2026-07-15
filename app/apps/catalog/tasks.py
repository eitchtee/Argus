from django.core.cache import cache
from huey.contrib.djhuey import db_task

from apps.catalog.languages import (
    FALLBACK_LANGUAGE_OPTIONS,
    language_catalog_cache_key,
    language_catalog_refresh_key,
)
from apps.catalog.providers.registry import get_provider


@db_task()
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
