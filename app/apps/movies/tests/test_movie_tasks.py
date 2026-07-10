from datetime import timedelta
from unittest.mock import patch

from cachalot.api import invalidate
from django.contrib.auth import get_user_model
from django.test import TransactionTestCase, override_settings
from django.utils import timezone

from apps.catalog.models import SyncStatus
from apps.catalog.providers.exceptions import ProviderError
from apps.movies.models import Movie, UserMovie


@override_settings(CACHALOT_ENABLED=False)
class MovieTaskTests(TransactionTestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("user@example.com")

    @patch("apps.movies.tasks.movie_services.import_movie")
    def test_import_movie_task_calls_import_service(self, import_movie):
        from apps.movies.tasks import import_movie_task

        import_movie_task.call_local("tmdb", "550")

        import_movie.assert_called_once_with("tmdb", "550")

    @patch("apps.movies.tasks.movie_services.import_movie")
    def test_sync_movie_imports_existing_movie_by_provider_identity(self, import_movie):
        from apps.movies.tasks import sync_movie

        movie = Movie.objects.create(provider="tmdb", external_id="550", title="Fight Club")

        sync_movie.call_local(movie.id)

        import_movie.assert_called_once_with("tmdb", "550")

    @patch("apps.movies.tasks.movie_services.import_movie")
    def test_sync_movie_marks_provider_failures_as_error(self, import_movie):
        from apps.movies.tasks import sync_movie

        movie = Movie.objects.create(
            provider="tmdb",
            external_id="550",
            title="Fight Club",
            sync_status=SyncStatus.OK,
        )
        import_movie.side_effect = ProviderError("provider down")

        with self.assertRaises(ProviderError):
            sync_movie.call_local(movie.id)

        invalidate(Movie)
        movie.refresh_from_db()
        self.assertEqual(movie.sync_status, SyncStatus.ERROR)

    @override_settings(CATALOG_MOVIE_SYNC_INTERVAL_DAYS=14)
    @patch("apps.movies.tasks.sync_movie")
    def test_enqueue_stale_movies_enqueues_only_tracked_stale_movies(self, sync_movie):
        from apps.movies.tasks import enqueue_stale_movies

        stale_tracked = self._create_movie(
            external_id="1",
            title="Stale tracked",
            last_synced_at=timezone.now() - timedelta(days=15),
        )
        fresh_tracked = self._create_movie(
            external_id="2",
            title="Fresh tracked",
            last_synced_at=timezone.now() - timedelta(days=2),
        )
        stale_untracked = self._create_movie(
            external_id="3",
            title="Stale untracked",
            last_synced_at=timezone.now() - timedelta(days=30),
        )
        never_synced_tracked = self._create_movie(
            external_id="4",
            title="Never synced tracked",
            last_synced_at=None,
        )
        UserMovie.objects.create(user=self.user, movie=stale_tracked, on_watchlist=True)
        UserMovie.objects.create(user=self.user, movie=fresh_tracked, on_watchlist=True)
        UserMovie.objects.create(
            user=self.user,
            movie=never_synced_tracked,
            on_watchlist=True,
        )

        enqueued_count = enqueue_stale_movies.call_local()

        self.assertEqual(enqueued_count, 2)
        self.assertCountEqual(
            [call.args[0] for call in sync_movie.call_args_list],
            [stale_tracked.id, never_synced_tracked.id],
        )
        self.assertNotIn(
            stale_untracked.id,
            [call.args[0] for call in sync_movie.call_args_list],
        )

    def _create_movie(self, **overrides):
        defaults = {
            "provider": "tmdb",
            "external_id": "550",
            "title": "Fight Club",
        }
        defaults.update(overrides)
        return Movie.objects.create(**defaults)
