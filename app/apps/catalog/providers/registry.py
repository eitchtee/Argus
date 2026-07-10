from apps.catalog.providers.base import BaseProvider
from apps.catalog.providers.exceptions import ProviderError
from apps.catalog.providers.tmdb import TMDBProvider
from apps.catalog.providers.tvdb import TVDBProvider


_PROVIDERS: dict[str, type[BaseProvider]] = {
    TMDBProvider.name: TMDBProvider,
    TVDBProvider.name: TVDBProvider,
}


def get_provider(name: str) -> BaseProvider:
    normalized_name = name.strip().lower()

    try:
        provider_class = _PROVIDERS[normalized_name]
    except KeyError as exc:
        raise ProviderError(f"Unknown provider: {name}") from exc

    return provider_class()
