import dataclasses
from datetime import date

from django.db import transaction
from django.db.models import F
from django.utils import timezone

from apps.catalog.models import Genre, SyncStatus
from apps.catalog.localization import (
    PROVIDER_DEFAULT_LANGUAGES,
    merge_translation_maps,
    metadata_language_for_user,
)
from apps.catalog.providers.exceptions import ProviderError
from apps.catalog.providers.registry import get_provider
from apps.catalog.tracking import find_tracking_match, identity_keys
from apps.movies.models import Movie, UserMovie
from apps.trakt.changes import record_intent
from apps.trakt.identities import movie_payload
from apps.trakt.models import TraktSyncIntent


def import_movie(
    provider: str,
    external_id: str,
    *,
    language: str | None = None,
    provider_getter=get_provider,
) -> Movie:
    if provider not in PROVIDER_DEFAULT_LANGUAGES:
        raise ValueError(f"Unsupported provider: {provider}")

    language = language or PROVIDER_DEFAULT_LANGUAGES[provider]
    provider_client = provider_getter(provider)

    try:
        detail = provider_client.fetch_detail(
            external_id,
            language=language,
            media_type="movie",
        )
    except ProviderError:
        Movie.objects.filter(provider=provider, external_id=external_id).update(
            sync_status=SyncStatus.ERROR,
        )
        raise

    with transaction.atomic():
        existing_movie = Movie.objects.filter(
            provider=provider,
            external_id=external_id,
        ).first()
        translations = merge_translation_maps(
            existing_movie.translations if existing_movie else {},
            detail.translations,
        )
        default_text = translations.get(PROVIDER_DEFAULT_LANGUAGES[provider], {})
        base_title = default_text.get("title") or (
            detail.title
            if language == PROVIDER_DEFAULT_LANGUAGES[provider]
            else detail.original_title or detail.title
        )
        if base_title and not default_text.get("title"):
            translations = merge_translation_maps(
                translations,
                {PROVIDER_DEFAULT_LANGUAGES[provider]: {"title": base_title}},
            )
            default_text = translations[PROVIDER_DEFAULT_LANGUAGES[provider]]
        movie, _created = Movie.objects.update_or_create(
            provider=provider,
            external_id=external_id,
            defaults={
                "imdb_id": detail.imdb_id,
                "tmdb_id": detail.tmdb_id or (external_id if provider == "tmdb" else None),
                "tvdb_id": detail.tvdb_id or (external_id if provider == "tvdb" else None),
                "director": detail.director or "",
                "trailer_url": detail.trailer_url,
                "cast": [dataclasses.asdict(member) for member in detail.cast],
                "title": base_title,
                "original_title": detail.original_title,
                "overview": default_text.get("overview", detail.overview),
                "tagline": default_text.get("tagline", detail.tagline),
                "translations": translations,
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

        genres = []
        for genre in detail.genres:
            saved_genre, _created = Genre.objects.get_or_create(
                provider=genre.provider,
                external_id=genre.external_id,
                defaults={"name": genre.name},
            )
            saved_genre.name = (
                genre.translations.get("en-US", {}).get("name")
                or saved_genre.name
                or genre.name
            )
            saved_genre.translations = merge_translation_maps(
                saved_genre.translations,
                genre.translations,
            )
            saved_genre.save(update_fields=["name", "translations"])
            genres.append(saved_genre)
        movie.genres.set(genres)

    return movie


def track_movie(
    user,
    provider: str,
    external_id: str,
    *,
    import_func=import_movie,
    hydrate_func=None,
) -> UserMovie:
    if provider not in PROVIDER_DEFAULT_LANGUAGES:
        raise ValueError(f"Unsupported provider: {provider}")

    language = metadata_language_for_user(user, provider)
    movie = import_func(provider, external_id, language=language)
    match = find_tracking_match(
        user,
        "movie",
        provider=movie.provider,
        external_id=movie.external_id,
        tmdb_id=movie.tmdb_id,
        tvdb_id=movie.tvdb_id,
        imdb_id=movie.imdb_id,
    )
    if match is not None and not match.same_provider:
        raise ValueError("Tracked on another provider.")
    user_movie, _created = UserMovie.objects.get_or_create(user=user, movie=movie)
    user_movie.on_watchlist = True
    user_movie.watchlist_added_at = timezone.now()
    user_movie.save(update_fields=["on_watchlist", "watchlist_added_at", "updated_at"])
    record_intent(
        user,
        TraktSyncIntent.Kind.MOVIE_WATCHLIST,
        movie_payload(movie),
    )
    if hydrate_func is None:
        from apps.movies.tasks import hydrate_movie_translations

        hydrate_func = lambda movie_id: hydrate_movie_translations.defer(
            movie_id=movie_id,
        )
    hydrate_func(movie.id)
    return user_movie


def refresh_movie(user, movie, *, sync_func=None) -> Movie:
    if not UserMovie.objects.filter(user=user, movie=movie).exists():
        raise ValueError("Movie is not tracked by this user.")

    movie.sync_status = SyncStatus.PENDING
    movie.save(update_fields=["sync_status", "updated_at"])
    if sync_func is None:
        from apps.movies.tasks import sync_movie

        sync_func = lambda movie_id: sync_movie.defer(movie_id=movie_id)
    sync_func(movie.id)
    return movie


def switch_movie_provider(
    user,
    *,
    source_provider: str,
    source_external_id: str,
    target_provider: str,
    target_external_id: str,
    target_imdb_id: str | None = None,
    sync_func=None,
) -> Movie:
    if source_provider not in PROVIDER_DEFAULT_LANGUAGES:
        raise ValueError(f"Unsupported provider: {source_provider}")
    if target_provider not in PROVIDER_DEFAULT_LANGUAGES:
        raise ValueError(f"Unsupported provider: {target_provider}")
    if source_provider == target_provider:
        raise ValueError("Target provider must differ from the source provider.")

    with transaction.atomic():
        source_state = (
            UserMovie.objects.select_for_update()
            .filter(
                user=user,
                movie__provider=source_provider,
                movie__external_id=str(source_external_id),
            )
            .select_related("movie")
            .first()
        )
        if source_state is None:
            raise ValueError("Source movie is not tracked by this user.")

        source = Movie.objects.select_for_update().get(id=source_state.movie_id)
        source_trakt_id = source.trakt_id
        target = Movie.objects.filter(
            provider=target_provider,
            external_id=str(target_external_id),
        ).first()
        if not _movie_provider_ids_match(
            source,
            target,
            target_provider=target_provider,
            target_external_id=target_external_id,
            target_imdb_id=target_imdb_id,
        ):
            raise ValueError("Movies do not match across providers.")

        target_created = target is None
        if target_created:
            target = Movie.objects.create(
                provider=target_provider,
                external_id=str(target_external_id),
                **_movie_switch_defaults(source, target_provider, target_external_id),
            )
            target.genres.set(source.genres.all())
        else:
            target.imdb_id = target.imdb_id or target_imdb_id or source.imdb_id
            target.tmdb_id = target.tmdb_id or source.tmdb_id
            target.tvdb_id = target.tvdb_id or source.tvdb_id

        target.tmdb_id = (
            str(target_external_id)
            if target_provider == "tmdb"
            else target.tmdb_id
        )
        target.tvdb_id = (
            str(target_external_id)
            if target_provider == "tvdb"
            else target.tvdb_id
        )
        target.sync_status = SyncStatus.PENDING
        target.save(
            update_fields=[
                "imdb_id",
                "tmdb_id",
                "tvdb_id",
                "trakt_id",
                "sync_status",
                "updated_at",
            ]
        )

        target_state, _created = UserMovie.objects.get_or_create(
            user=user,
            movie=target,
        )
        target_state.on_watchlist = source_state.on_watchlist
        target_state.watchlist_added_at = source_state.watchlist_added_at
        target_state.is_seen = source_state.is_seen
        target_state.seen_at = source_state.seen_at
        target_state.tier = source_state.tier
        target_state.save(
            update_fields=[
                "on_watchlist",
                "watchlist_added_at",
                "is_seen",
                "seen_at",
                "tier",
                "updated_at",
            ]
        )
        source_state.delete()
        source_removed = not source.user_states.exists()
        if source_removed:
            source.delete()
            if source_trakt_id and not target.trakt_id:
                target.trakt_id = source_trakt_id
                target.save(update_fields=["trakt_id", "updated_at"])

    if target_state.is_seen:
        record_intent(
            user,
            TraktSyncIntent.Kind.MOVIE_HISTORY,
            movie_payload(target, watched_at=target_state.seen_at),
        )
    record_intent(
        user,
        TraktSyncIntent.Kind.MOVIE_WATCHLIST,
        movie_payload(target),
        desired=target_state.on_watchlist,
    )

    if sync_func is None:
        from apps.movies.tasks import sync_movie

        sync_func = lambda movie_id: sync_movie.defer(movie_id=movie_id)
    sync_func(target.id)
    return target


def _movie_provider_ids_match(
    source: Movie,
    target: Movie | None,
    *,
    target_provider: str,
    target_external_id: str,
    target_imdb_id: str | None,
) -> bool:
    source_keys = identity_keys(
        source.provider,
        source.external_id,
        tmdb_id=source.tmdb_id,
        tvdb_id=source.tvdb_id,
        imdb_id=source.imdb_id,
    )
    target_keys = identity_keys(
        target_provider,
        target_external_id,
        tmdb_id=target.tmdb_id if target else None,
        tvdb_id=target.tvdb_id if target else None,
        imdb_id=(target.imdb_id if target else None) or target_imdb_id,
    )
    return bool(source_keys.intersection(target_keys))


def _movie_switch_defaults(
    source: Movie,
    target_provider: str,
    target_external_id: str,
) -> dict:
    return {
        "imdb_id": source.imdb_id,
        "tmdb_id": str(target_external_id) if target_provider == "tmdb" else source.tmdb_id,
        "tvdb_id": str(target_external_id) if target_provider == "tvdb" else source.tvdb_id,
        "title": source.title,
        "original_title": source.original_title,
        "overview": source.overview,
        "tagline": source.tagline,
        "translations": source.translations,
        "poster_path": source.poster_path,
        "backdrop_path": source.backdrop_path,
        "cast": source.cast,
        "director": source.director,
        "trailer_url": source.trailer_url,
        "release_date": source.release_date,
        "runtime": source.runtime,
        "status": source.status,
        "vote_average": source.vote_average,
        "vote_count": source.vote_count,
        "sync_status": SyncStatus.PENDING,
    }


def remove_from_watchlist(user, movie: Movie) -> UserMovie | None:
    record_intent(
        user,
        TraktSyncIntent.Kind.MOVIE_WATCHLIST,
        movie_payload(movie),
        desired=False,
    )
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


def delete_movie_data(user, movie: Movie) -> None:
    record_intent(
        user,
        TraktSyncIntent.Kind.MOVIE_WATCHLIST,
        movie_payload(movie),
        desired=False,
    )
    UserMovie.objects.filter(user=user, movie=movie).delete()


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
    record_intent(
        user,
        TraktSyncIntent.Kind.MOVIE_HISTORY,
        movie_payload(movie, watched_at=user_movie.seen_at),
    )
    record_intent(
        user,
        TraktSyncIntent.Kind.MOVIE_WATCHLIST,
        movie_payload(movie),
        desired=False,
    )
    return user_movie


def unmark_seen(user, movie: Movie) -> UserMovie:
    user_movie, _created = UserMovie.objects.get_or_create(user=user, movie=movie)
    user_movie.is_seen = False
    user_movie.seen_at = None
    user_movie.tier = None
    user_movie.on_watchlist = True
    user_movie.watchlist_added_at = timezone.now()
    user_movie.save(
        update_fields=[
            "is_seen",
            "seen_at",
            "tier",
            "on_watchlist",
            "watchlist_added_at",
            "updated_at",
        ]
    )
    record_intent(
        user,
        TraktSyncIntent.Kind.MOVIE_WATCHLIST,
        movie_payload(movie),
    )
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
            user_states__user=user,
            user_states__on_watchlist=True,
            user_states__is_seen=False,
            release_date__isnull=False,
            release_date__lte=timezone.localdate(),
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
