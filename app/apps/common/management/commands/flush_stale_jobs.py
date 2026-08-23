from django.core.cache import cache
from django.core.management.base import BaseCommand
from django.db import connection, transaction

from apps.catalog.languages import (
    language_catalog_cache_key,
    language_catalog_refresh_key,
)
from apps.catalog.localization import PROVIDER_DEFAULT_LANGUAGES
from apps.catalog.models import SyncStatus
from apps.movies.models import Movie
from apps.tv.models import Show

# A queued or in-flight job cannot survive the process that owned it: the worker
# never resumes "doing" rows, and "todo" rows are almost always a backlog the
# previous run could not drain. Both are discarded on startup.
STALE_JOB_STATUSES = ["todo", "doing"]


class Command(BaseCommand):
    help = (
        "Discards procrastinate jobs left queued or in-flight by a previous run, "
        "and clears the pending sync state that was waiting on them."
    )

    def handle(self, *args, **options):
        with transaction.atomic(), connection.cursor() as cursor:
            # Periodic defers reference jobs with ON DELETE NO ACTION. Dropping the
            # reference keeps the (task_name, periodic_id, defer_timestamp) dedupe
            # ledger intact so the next tick is not deferred twice.
            cursor.execute(
                """
                UPDATE procrastinate_periodic_defers
                SET job_id = NULL
                WHERE job_id IN (
                    SELECT id FROM procrastinate_jobs WHERE status = ANY(%s)
                )
                """,
                [STALE_JOB_STATUSES],
            )
            cursor.execute(
                "DELETE FROM procrastinate_jobs WHERE status = ANY(%s)",
                [STALE_JOB_STATUSES],
            )
            discarded = cursor.rowcount

        # Without the job that would have filled them in, these never leave PENDING,
        # and the detail pages poll for episodes forever.
        stranded_shows = Show.objects.filter(sync_status=SyncStatus.PENDING).update(
            sync_status=SyncStatus.ERROR,
        )
        stranded_movies = Movie.objects.filter(sync_status=SyncStatus.PENDING).update(
            sync_status=SyncStatus.ERROR,
        )

        requeued = self._requeue_language_catalogs()

        self.stdout.write(
            self.style.SUCCESS(
                f"Discarded {discarded} stale job(s); "
                f"cleared {stranded_shows} show(s) and {stranded_movies} movie(s) "
                f"stuck on a pending sync; requeued {requeued} language catalog(s)."
            )
        )

    def _requeue_language_catalogs(self) -> int:
        """Re-arm the metadata language lists discarded above.

        Nothing else fills that cache, and a cold one leaves the settings page
        offering English alone. The lazy refresh in get_language_choices takes
        its lock before deferring, so a discarded job also blocks the retry
        that would have replaced it.
        """
        from apps.catalog.tasks import refresh_language_catalog

        requeued = 0
        for provider_name in PROVIDER_DEFAULT_LANGUAGES:
            cache.delete(language_catalog_refresh_key(provider_name))
            if cache.get(language_catalog_cache_key(provider_name)) is not None:
                continue
            refresh_language_catalog.defer(provider_name=provider_name)
            requeued += 1
        return requeued
