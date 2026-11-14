from django.db.models import Q

from apps.catalog.localization import (
    PROVIDER_DEFAULT_LANGUAGES,
    metadata_language_for_user,
    resolve_field,
    resolve_title,
)

TITLE_LIMIT = 12
EPISODE_LIMIT = 8


def _text_match(user, field, query, provider_path="provider"):
    """Match the raw field, or its translation in the user's language for each provider."""
    condition = Q(**{f"{field}__icontains": query})
    for provider in PROVIDER_DEFAULT_LANGUAGES:
        language = metadata_language_for_user(user, provider)
        condition |= Q(
            **{provider_path: provider, f"translations__{language}__{field}__icontains": query}
        )
    return condition


def search_library(user, query):
    """Movies and shows in the user's library matching ``query``, then matching episodes."""
    from apps.movies.models import Movie
    from apps.tv.models import Episode, Show

    query = query.strip()
    if len(query) < 2:
        return {"titles": [], "episodes": []}

    movies = Movie.objects.filter(
        _text_match(user, "title", query) | Q(original_title__icontains=query),
        user_states__user=user,
    ).distinct()[:TITLE_LIMIT]
    shows = Show.objects.filter(
        _text_match(user, "name", query) | Q(original_title__icontains=query),
        user_states__user=user,
    ).distinct()[:TITLE_LIMIT]

    titles = [
        {"kind": "movie", "record": movie, "title": _title(user, movie)}
        for movie in movies
    ] + [
        {"kind": "tv", "record": show, "title": _title(user, show)}
        for show in shows
    ]
    needle = query.casefold()
    # Prefix matches first, then alphabetical.
    titles.sort(key=lambda t: (not t["title"].casefold().startswith(needle), t["title"].casefold()))

    episodes = (
        Episode.objects.filter(
            _text_match(user, "name", query, provider_path="show__provider"),
            show__user_states__user=user,
        )
        .select_related("show")
        .distinct()[:EPISODE_LIMIT]
    )
    return {
        "titles": titles[:TITLE_LIMIT],
        "episodes": [
            {
                "record": episode,
                "title": resolve_field(
                    episode, "name", metadata_language_for_user(user, episode.show.provider)
                ),
                "show_title": _title(user, episode.show),
            }
            for episode in episodes
        ],
    }


def _title(user, record):
    return resolve_title(record, metadata_language_for_user(user, record.provider))
