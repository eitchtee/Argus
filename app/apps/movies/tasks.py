from django.conf import settings
from django.db.models import Q
from django.utils import timezone
from huey import crontab
from huey.contrib.djhuey import db_periodic_task, db_task

from apps.catalog.models import SyncStatus
from apps.catalog.providers.exceptions import ProviderError
from apps.catalog.providers.registry import get_provider
from apps.movies import services as movie_services
from apps.movies.models import Movie, UserMovie


@db_task()
def hydrate_movie_translations(movie_id: int):
    movie = Movie.objects.get(id=movie_id)
    provider = get_provider(movie.provider)
    failures = []
    result = movie
    for option in provider.list_languages():
        try:
            result = movie_services.import_movie(
                movie.provider,
                movie.external_id,
                language=option.code,
                provider_getter=lambda _name: provider,
            )
        except ProviderError:
            failures.append(option.code)
    if failures:
        raise ProviderError(
            f"Movie translation hydration failed for: {', '.join(failures)}"
        )
    return result


@db_task()
def sync_movie(movie_id: int):
    movie = Movie.objects.get(id=movie_id)
    movie.sync_status = SyncStatus.ERROR
    movie.save(update_fields=["sync_status", "updated_at"])

    try:
        imported_movie = movie_services.import_movie(
            movie.provider,
            movie.external_id,
            language="en-US",
        )
        translation_task = hydrate_movie_translations(imported_movie.id)
        return {
            "item_id": imported_movie.id,
            "translation_task_id": translation_task.id,
        }
    except Exception:
        Movie.objects.filter(id=movie_id).update(sync_status=SyncStatus.ERROR)
        raise


@db_task()
def sync_movies(force_all: bool = False):
    if force_all:
        movie_ids = Movie.objects.filter(provider="tmdb").values_list("id", flat=True)
    else:
        cutoff = timezone.now() - timezone.timedelta(
            days=settings.CATALOG_MOVIE_SYNC_INTERVAL_DAYS,
        )
        tracked_movie_ids = UserMovie.objects.values_list("movie_id", flat=True).distinct()
        movie_ids = (
            Movie.objects.filter(provider="tmdb", id__in=tracked_movie_ids)
            .filter(Q(last_synced_at__isnull=True) | Q(last_synced_at__lte=cutoff))
            .values_list("id", flat=True)
        )

    return [sync_movie(movie_id).id for movie_id in movie_ids]


@db_periodic_task(crontab(hour=2, minute=0))
def daily_movie_sync():
    sync_movies()
