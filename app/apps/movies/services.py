import dataclasses
from datetime import date

from django.db import transaction
from django.db.models import F
from django.utils import timezone

from apps.catalog.models import Genre, SyncStatus
from apps.catalog.providers.exceptions import ProviderError
from apps.catalog.providers.registry import get_provider
from apps.movies.models import Movie, UserMovie


def import_movie(provider: str, external_id: str, *, provider_getter=get_provider) -> Movie:
    if provider != "tmdb":
        raise ValueError("Movies must use tmdb provider metadata.")

    provider_client = provider_getter(provider)

    try:
        detail = provider_client.fetch_detail(external_id)
    except ProviderError:
        Movie.objects.filter(provider=provider, external_id=external_id).update(
            sync_status=SyncStatus.ERROR,
        )
        raise

    with transaction.atomic():
        movie, _created = Movie.objects.update_or_create(
            provider=provider,
            external_id=external_id,
            defaults={
                "imdb_id": detail.imdb_id,
                "director": detail.director or "",
                "trailer_url": detail.trailer_url,
                "cast": [dataclasses.asdict(member) for member in detail.cast],
                "title": detail.title,
                "original_title": detail.original_title,
                "overview": detail.overview,
                "tagline": detail.tagline,
                "poster_path": detail.poster_path,
                "backdrop_path": detail.backdrop_path,
                "release_date": _parse_date(detail.release_date),
                "runtime": detail.runtime,
                "status": detail.status,
                "vote_average": detail.vote_average,
                "vote_count": detail.vote_count,
                "last_synced_at": timezone.now(),
                "sync_status": SyncStatus.OK,
            },
        )

        genres = [
            Genre.objects.update_or_create(
                provider=genre.provider,
                external_id=genre.external_id,
                defaults={"name": genre.name},
            )[0]
            for genre in detail.genres
        ]
        movie.genres.set(genres)

    return movie


def track_movie(
    user,
    provider: str,
    external_id: str,
    *,
    import_func=import_movie,
) -> UserMovie:
    movie = import_func(provider, external_id)
    user_movie, _created = UserMovie.objects.get_or_create(user=user, movie=movie)
    user_movie.on_watchlist = True
    user_movie.watchlist_added_at = timezone.now()
    user_movie.save(update_fields=["on_watchlist", "watchlist_added_at", "updated_at"])
    return user_movie


def remove_from_watchlist(user, movie: Movie) -> UserMovie | None:
    try:
        user_movie = UserMovie.objects.get(user=user, movie=movie)
    except UserMovie.DoesNotExist:
        return None

    user_movie.on_watchlist = False
    user_movie.watchlist_added_at = None

    if not user_movie.is_seen and user_movie.tier is None:
        user_movie.delete()
        return None

    user_movie.save(update_fields=["on_watchlist", "watchlist_added_at", "updated_at"])
    return user_movie


def mark_seen(user, movie: Movie) -> UserMovie:
    user_movie, _created = UserMovie.objects.get_or_create(user=user, movie=movie)
    user_movie.is_seen = True
    user_movie.seen_at = timezone.now()
    user_movie.on_watchlist = False
    user_movie.watchlist_added_at = None
    user_movie.save(
        update_fields=[
            "is_seen",
            "seen_at",
            "on_watchlist",
            "watchlist_added_at",
            "updated_at",
        ]
    )
    return user_movie


def unmark_seen(user, movie: Movie) -> UserMovie:
    user_movie, _created = UserMovie.objects.get_or_create(user=user, movie=movie)
    user_movie.is_seen = False
    user_movie.seen_at = None
    user_movie.tier = None
    user_movie.save(update_fields=["is_seen", "seen_at", "tier", "updated_at"])
    return user_movie


def set_tier(user, movie: Movie, tier: str) -> UserMovie:
    user_movie, _created = UserMovie.objects.get_or_create(user=user, movie=movie)

    if not user_movie.is_seen:
        raise ValueError("Cannot tier an unseen movie.")

    user_movie.tier = tier
    user_movie.save(update_fields=["tier", "updated_at"])
    return user_movie


def clear_tier(user, movie: Movie) -> UserMovie:
    user_movie, _created = UserMovie.objects.get_or_create(user=user, movie=movie)
    user_movie.tier = None
    user_movie.save(update_fields=["tier", "updated_at"])
    return user_movie


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None

    return date.fromisoformat(value)


def get_watch_something(user, count: int = 10) -> list[Movie]:
    return list(
        Movie.objects.filter(
            user_states__user=user, user_states__on_watchlist=True, user_states__is_seen=False
        ).order_by("?")[:count]
    )


def get_watchlist_movies(user) -> list[Movie]:
    entries = (
        UserMovie.objects.filter(
            user=user,
            on_watchlist=True,
            is_seen=False,
        )
        .select_related("movie")
        .order_by(
            F("watchlist_added_at").desc(nulls_last=True),
            "movie__title",
            "movie__external_id",
        )
    )
    return [entry.movie for entry in entries]


def get_watched_movies(user) -> list[Movie]:
    entries = (
        UserMovie.objects.filter(
            user=user,
            is_seen=True,
        )
        .select_related("movie")
        .order_by(
            F("seen_at").desc(nulls_last=True),
            "movie__title",
            "movie__external_id",
        )
    )
    return [entry.movie for entry in entries]
