from django.conf import settings
from django.contrib.auth import get_user_model
from django.db.models import Q
from django.utils import timezone
from procrastinate.contrib.django import app

from apps.catalog.models import SyncStatus
from apps.catalog.localization import (
    PROVIDER_DEFAULT_LANGUAGES,
    metadata_language_for_user,
)
from apps.catalog.providers.registry import get_provider
from apps.movies import services as movie_services
from apps.movies.models import Movie, UserMovie


@app.task(name="track_movie")
def track_movie(user_id: int, movie_id: int):
    user = get_user_model().objects.get(id=user_id)
    movie = Movie.objects.get(id=movie_id)
    imported_movie = movie_services.import_movie(
        movie.provider,
        movie.external_id,
        language=metadata_language_for_user(user, movie.provider),
    )
    translation_task_id = hydrate_movie_translations.defer(movie_id=imported_movie.id)
    return {
        "item_id": imported_movie.id,
        "translation_task_id": translation_task_id,
    }


@app.task(name="hydrate_movie_translations")
def hydrate_movie_translations(movie_id: int):
    # One detail response already carries every translation the provider has
    # for a movie, so this no longer walks the language list. Genre names were
    # the only per-language part, and refresh_genre_catalog now collects those
    # once for every title instead of once per movie.
    movie = Movie.objects.get(id=movie_id)
    provider = get_provider(movie.provider)
    return movie_services.import_movie(
        movie.provider,
        movie.external_id,
        language=PROVIDER_DEFAULT_LANGUAGES[movie.provider],
        sync_artworks=True,
        provider_getter=lambda _name: provider,
    )


@app.task(name="sync_movie")
def sync_movie(movie_id: int):
    movie = Movie.objects.get(id=movie_id)
    movie.sync_status = SyncStatus.ERROR
    movie.save(update_fields=["sync_status", "updated_at"])

    try:
        imported_movie = movie_services.import_movie(
            movie.provider,
            movie.external_id,
            language=PROVIDER_DEFAULT_LANGUAGES[movie.provider],
        )
        translation_task_id = hydrate_movie_translations.defer(
            movie_id=imported_movie.id,
        )
        return {
            "item_id": imported_movie.id,
            "translation_task_id": translation_task_id,
        }
    except Exception:
        Movie.objects.filter(id=movie_id).update(sync_status=SyncStatus.ERROR)
        raise


@app.task(name="sync_movies")
def sync_movies(force_all: bool = False):
    if force_all:
        movie_ids = Movie.objects.filter(
            provider__in=PROVIDER_DEFAULT_LANGUAGES,
        ).values_list("id", flat=True)
    else:
        cutoff = timezone.now() - timezone.timedelta(
            days=settings.CATALOG_MOVIE_SYNC_INTERVAL_DAYS,
        )
        tracked_movie_ids = UserMovie.objects.values_list("movie_id", flat=True).distinct()
        movie_ids = (
            Movie.objects.filter(
                provider__in=PROVIDER_DEFAULT_LANGUAGES,
                id__in=tracked_movie_ids,
            )
            .filter(Q(last_synced_at__isnull=True) | Q(last_synced_at__lte=cutoff))
            .values_list("id", flat=True)
        )

    return [sync_movie.defer(movie_id=movie_id) for movie_id in movie_ids]


@app.periodic(cron="0 2 * * *")
@app.task(name="daily_movie_sync")
def daily_movie_sync(timestamp: int|None = None):
    sync_movies.defer()
