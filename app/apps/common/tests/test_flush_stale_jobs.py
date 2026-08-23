from io import StringIO

from unittest.mock import patch

from django.core.cache import cache
from django.core.management import call_command
from django.db import connection
from django.test import TestCase

from apps.catalog.languages import (
    language_catalog_cache_key,
    language_catalog_refresh_key,
)
from apps.catalog.models import SyncStatus
from apps.movies.models import Movie
from apps.tv.models import Show


def _defer_job(status: str) -> int:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO procrastinate_jobs (task_name, queue_name, args, status)
            VALUES ('sync_show', 'default', '{}', %s)
            RETURNING id
            """,
            [status],
        )
        return cursor.fetchone()[0]


def _job_statuses() -> list[str]:
    with connection.cursor() as cursor:
        cursor.execute("SELECT status FROM procrastinate_jobs ORDER BY id")
        return [row[0] for row in cursor.fetchall()]


class FlushStaleJobsLanguageCatalogTests(TestCase):
    """The metadata language lists live in a cache that only the discarded
    refresh job fills, so a flush must re-arm it or the settings page offers
    English alone."""

    def setUp(self):
        for provider_name in ("tmdb", "tvdb"):
            cache.delete(language_catalog_cache_key(provider_name))
            cache.delete(language_catalog_refresh_key(provider_name))

    def test_it_requeues_a_refresh_for_every_provider_with_a_cold_cache(self):
        with patch(
            "apps.catalog.tasks.refresh_language_catalog.defer"
        ) as defer:
            call_command("flush_stale_jobs", stdout=StringIO())

        self.assertEqual(
            sorted(call.kwargs["provider_name"] for call in defer.call_args_list),
            ["tmdb", "tvdb"],
        )

    def test_it_leaves_a_warm_cache_alone(self):
        cache.set(
            language_catalog_cache_key("tmdb"),
            [{"code": "en-US", "name": "English"}],
            timeout=None,
        )

        with patch(
            "apps.catalog.tasks.refresh_language_catalog.defer"
        ) as defer:
            call_command("flush_stale_jobs", stdout=StringIO())

        self.assertEqual(
            [call.kwargs["provider_name"] for call in defer.call_args_list],
            ["tvdb"],
        )

    def test_it_clears_a_lock_left_by_a_discarded_refresh(self):
        cache.set(language_catalog_refresh_key("tmdb"), True, timeout=60)

        with patch("apps.catalog.tasks.refresh_language_catalog.defer"):
            call_command("flush_stale_jobs", stdout=StringIO())

        self.assertIsNone(cache.get(language_catalog_refresh_key("tmdb")))


class FlushStaleJobsCommandTests(TestCase):
    def test_discards_queued_and_in_flight_jobs_but_keeps_finished_ones(self):
        _defer_job("todo")
        _defer_job("doing")
        _defer_job("succeeded")
        _defer_job("failed")

        # The language catalog requeue would otherwise add a job of its own.
        with patch("apps.catalog.tasks.refresh_language_catalog.defer"):
            call_command("flush_stale_jobs", stdout=StringIO())

        self.assertEqual(_job_statuses(), ["succeeded", "failed"])

    def test_releases_periodic_defer_pointing_at_a_discarded_job(self):
        job_id = _defer_job("todo")
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO procrastinate_periodic_defers
                    (task_name, periodic_id, defer_timestamp, job_id)
                VALUES ('sync_tv', '', 1, %s)
                """,
                [job_id],
            )

        call_command("flush_stale_jobs", stdout=StringIO())

        with connection.cursor() as cursor:
            cursor.execute("SELECT job_id FROM procrastinate_periodic_defers")
            self.assertEqual(cursor.fetchall(), [(None,)])

    def test_clears_sync_state_left_waiting_on_a_discarded_job(self):
        show = Show.objects.create(
            provider="tvdb",
            external_id="121361",
            name="Game of Thrones",
            sync_status=SyncStatus.PENDING,
        )
        movie = Movie.objects.create(
            provider="tmdb",
            external_id="550",
            title="Fight Club",
            sync_status=SyncStatus.PENDING,
        )
        synced = Show.objects.create(
            provider="tvdb",
            external_id="81189",
            name="Breaking Bad",
            sync_status=SyncStatus.OK,
        )

        call_command("flush_stale_jobs", stdout=StringIO())

        show.refresh_from_db()
        movie.refresh_from_db()
        synced.refresh_from_db()
        self.assertEqual(show.sync_status, SyncStatus.ERROR)
        self.assertEqual(movie.sync_status, SyncStatus.ERROR)
        self.assertEqual(synced.sync_status, SyncStatus.OK)
