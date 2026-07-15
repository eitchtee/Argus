import hashlib
from collections.abc import Callable

from django.conf import settings
from django.core.cache import cache

from apps.catalog.providers.base import DetailDTO, EpisodeDTO, SearchResultDTO, SeasonDTO
from apps.catalog.providers.registry import get_provider


SEARCH_TYPE_PROVIDERS = {
    "movie": "tmdb",
    "tv": "tvdb",
}


def search(
    query: str,
    *,
    media_type: str,
    language: str,
    page: int = 1,
    provider_getter: Callable[[str], object] = get_provider,
) -> list[SearchResultDTO]:
    provider_name = _provider_name_for_media_type(media_type)
    normalized_query = query.strip()
    cache_key = _search_cache_key(provider_name, normalized_query, language, page)
    cached_results = cache.get(cache_key)

    if cached_results is not None:
        return cached_results

    provider = provider_getter(provider_name)
    results = provider.search(normalized_query, language=language, page=page)
    cache.set(cache_key, results, settings.CATALOG_SEARCH_CACHE_TTL)
    return results


def _provider_name_for_media_type(media_type: str) -> str:
    try:
        return SEARCH_TYPE_PROVIDERS[media_type]
    except KeyError as exc:
        raise ValueError(f"Unsupported search type: {media_type}") from exc


def _search_cache_key(provider_name: str, query: str, language: str, page: int) -> str:
    query_hash = hashlib.sha1(query.encode("utf-8")).hexdigest()
    return f"search:{provider_name}:{language}:{query_hash}:{page}"


def get_movie_detail(
    external_id: str,
    *,
    language: str,
    provider_getter: Callable[[str], object] = get_provider,
) -> DetailDTO:
    return _get_cached_detail("tmdb", external_id, language, provider_getter)


def get_show_detail(
    external_id: str,
    *,
    language: str,
    provider_getter: Callable[[str], object] = get_provider,
) -> DetailDTO:
    return _get_cached_detail("tvdb", external_id, language, provider_getter)


def get_show_episodes(
    external_id: str,
    *,
    language: str,
    provider_getter: Callable[[str], object] = get_provider,
) -> list[EpisodeDTO]:
    cache_key = _episodes_cache_key("tvdb", external_id, language)
    cached_episodes = cache.get(cache_key)

    if cached_episodes is not None:
        return cached_episodes

    provider = provider_getter("tvdb")
    episodes = provider.fetch_episodes(external_id, language=language)
    cache.set(cache_key, episodes, settings.CATALOG_SEARCH_CACHE_TTL)
    return episodes


def get_show_seasons(
    external_id: str,
    *,
    language: str,
    provider_getter: Callable[[str], object] = get_provider,
) -> list[SeasonDTO]:
    cache_key = _seasons_cache_key("tvdb", external_id, language)
    cached_seasons = cache.get(cache_key)
    if cached_seasons is not None:
        return cached_seasons
    seasons = provider_getter("tvdb").fetch_seasons(external_id, language=language)
    cache.set(cache_key, seasons, settings.CATALOG_SEARCH_CACHE_TTL)
    return seasons


def _get_cached_detail(
    provider_name: str,
    external_id: str,
    language: str,
    provider_getter,
) -> DetailDTO:
    cache_key = _detail_cache_key(provider_name, external_id, language)
    cached_detail = cache.get(cache_key)

    if cached_detail is not None:
        return cached_detail

    provider = provider_getter(provider_name)
    detail = provider.fetch_detail(external_id, language=language)
    cache.set(cache_key, detail, settings.CATALOG_SEARCH_CACHE_TTL)
    return detail


def _detail_cache_key(provider_name: str, external_id: str, language: str) -> str:
    return f"detail:{provider_name}:{language}:{external_id}"


def _episodes_cache_key(provider_name: str, external_id: str, language: str) -> str:
    return f"episodes:{provider_name}:{language}:{external_id}"


def _seasons_cache_key(provider_name: str, external_id: str, language: str) -> str:
    return f"seasons:{provider_name}:{language}:{external_id}"
