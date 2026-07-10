from django.apps import apps

from apps.catalog.providers.base import SearchResultDTO


def tracked_keys(user, media_type: str, results: list[SearchResultDTO]) -> set[tuple[str, str]]:
    if media_type == "movie":
        return _movie_tracked_keys(user, results)
    if media_type == "tv":
        return _show_tracked_keys(user, results)
    return set()


def _movie_tracked_keys(user, results):
    try:
        movie_model = apps.get_model("movies", "Movie")
        user_movie_model = apps.get_model("movies", "UserMovie")
    except LookupError:
        return set()

    candidate_keys = [(result.provider, result.external_id) for result in results]
    if not candidate_keys:
        return set()

    movies = movie_model.objects.filter(
        provider__in={provider for provider, _external_id in candidate_keys},
        external_id__in={external_id for _provider, external_id in candidate_keys},
    )
    tracked_movie_ids = set(
        user_movie_model.objects.filter(user=user, movie__in=movies).values_list(
            "movie_id",
            flat=True,
        )
    )

    return {
        (movie.provider, movie.external_id)
        for movie in movies
        if movie.id in tracked_movie_ids
    }


def _show_tracked_keys(user, results):
    try:
        show_model = apps.get_model("tv", "Show")
        user_show_model = apps.get_model("tv", "UserShow")
    except LookupError:
        return set()

    candidate_keys = [(result.provider, result.external_id) for result in results]
    if not candidate_keys:
        return set()

    shows = show_model.objects.filter(
        provider__in={provider for provider, _external_id in candidate_keys},
        external_id__in={external_id for _provider, external_id in candidate_keys},
    )
    tracked_show_ids = set(
        user_show_model.objects.filter(user=user, show__in=shows).values_list(
            "show_id",
            flat=True,
        )
    )

    return {
        (show.provider, show.external_id)
        for show in shows
        if show.id in tracked_show_ids
    }