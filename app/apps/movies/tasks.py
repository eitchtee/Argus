from django.conf import settings
from django.db.models import Q
from django.utils import timezone
from huey.contrib.djhuey import db_task

from apps.catalog.models import SyncStatus
from apps.catalog.providers.exceptions import ProviderError
from apps.catalog.providers.registry import get_provider
from apps.movies import services as movie_services
from apps.movies.models import Movie, UserMovie


@db_task()
def import_movie_task(provider: str, external_id: str):
    return movie_services.import_movie(provider, external_id, language="en-US")


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
        hydrate_movie_translations(imported_movie.id)
        return imported_movie
    except Exception:
        Movie.objects.filter(id=movie_id).update(sync_status=SyncStatus.ERROR)
        raise


@db_task()
def enqueue_stale_movies():
    cutoff = timezone.now() - timezone.timedelta(
        days=settings.CATALOG_MOVIE_SYNC_INTERVAL_DAYS,
    )
    tracked_movie_ids = UserMovie.objects.values_list("movie_id", flat=True).distinct()
    stale_movie_ids = (
        Movie.objects.filter(id__in=tracked_movie_ids)
        .filter(Q(last_synced_at__isnull=True) | Q(last_synced_at__lte=cutoff))
        .values_list("id", flat=True)
    )

    enqueued_count = 0
    for movie_id in stale_movie_ids:
        sync_movie(movie_id)
        enqueued_count += 1

    return enqueued_count
