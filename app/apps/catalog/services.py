import hashlib
from collections.abc import Callable

from django.conf import settings
from django.core.cache import cache

from apps.catalog.providers.base import DetailDTO, EpisodeDTO, SearchResultDTO
from apps.catalog.providers.registry import get_provider


SEARCH_TYPE_PROVIDERS = {
    "movie": "tmdb",
    "tv": "tvdb",
}


def search(
    query: str,
    *,
    media_type: str,
    page: int = 1,
    provider_getter: Callable[[str], object] = get_provider,
) -> list[SearchResultDTO]:
    provider_name = _provider_name_for_media_type(media_type)
    normalized_query = query.strip()
    cache_key = _search_cache_key(provider_name, normalized_query, page)
    cached_results = cache.get(cache_key)

    if cached_results is not None:
        return cached_results

    provider = provider_getter(provider_name)
    results = provider.search(normalized_query, page=page)
    cache.set(cache_key, results, settings.CATALOG_SEARCH_CACHE_TTL)
    return results


def _provider_name_for_media_type(media_type: str) -> str:
    try:
        return SEARCH_TYPE_PROVIDERS[media_type]
    except KeyError as exc:
        raise ValueError(f"Unsupported search type: {media_type}") from exc


def _search_cache_key(provider_name: str, query: str, page: int) -> str:
    query_hash = hashlib.sha1(query.encode("utf-8")).hexdigest()
    return f"search:{provider_name}:{query_hash}:{page}"


def get_movie_detail(
    external_id: str,
    *,
    provider_getter: Callable[[str], object] = get_provider,
) -> DetailDTO:
    return _get_cached_detail("tmdb", external_id, provider_getter)


def get_show_detail(
    external_id: str,
    *,
    provider_getter: Callable[[str], object] = get_provider,
) -> DetailDTO:
    return _get_cached_detail("tvdb", external_id, provider_getter)


def get_show_episodes(
    external_id: str,
    *,
    provider_getter: Callable[[str], object] = get_provider,
) -> list[EpisodeDTO]:
    cache_key = _episodes_cache_key("tvdb", external_id)
    cached_episodes = cache.get(cache_key)

    if cached_episodes is not None:
        return cached_episodes

    provider = provider_getter("tvdb")
    episodes = provider.fetch_episodes(external_id)
    cache.set(cache_key, episodes, settings.CATALOG_SEARCH_CACHE_TTL)
    return episodes


def _get_cached_detail(provider_name: str, external_id: str, provider_getter) -> DetailDTO:
    cache_key = _detail_cache_key(provider_name, external_id)
    cached_detail = cache.get(cache_key)

    if cached_detail is not None:
        return cached_detail

    provider = provider_getter(provider_name)
    detail = provider.fetch_detail(external_id)
    cache.set(cache_key, detail, settings.CATALOG_SEARCH_CACHE_TTL)
    return detail


def _detail_cache_key(provider_name: str, external_id: str) -> str:
    return f"detail:{provider_name}:{external_id}"


def _episodes_cache_key(provider_name: str, external_id: str) -> str:
    return f"episodes:{provider_name}:{external_id}"
