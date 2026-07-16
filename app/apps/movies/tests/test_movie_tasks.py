from datetime import timedelta
from unittest.mock import call, patch

from django.contrib.auth import get_user_model
from django.test import TransactionTestCase, override_settings
from django.utils import timezone

from apps.catalog.models import SyncStatus
from apps.catalog.providers.exceptions import ProviderError
from apps.catalog.providers.base import LanguageOptionDTO
from apps.movies.models import Movie, UserMovie


class MovieTaskTests(TransactionTestCase):
    @patch("apps.movies.tasks.get_provider")
    @patch("apps.movies.tasks.movie_services.import_movie")
    def test_hydration_imports_each_provider_language_and_continues_after_failure(
        self,
        import_movie,
        get_provider,
    ):
        from apps.movies.tasks import hydrate_movie_translations

        movie = Movie.objects.create(provider="tmdb", external_id="550", title="Fight Club")
        provider = get_provider.return_value
        provider.list_languages.return_value = [
            LanguageOptionDTO("en-US", "English"),
            LanguageOptionDTO("pt-BR", "Português"),
        ]
        import_movie.side_effect = [movie, ProviderError("pt failed")]

        with self.assertRaisesMessage(ProviderError, "pt-BR"):
            hydrate_movie_translations.func(movie.id)

        self.assertEqual(import_movie.call_count, 2)
        self.assertEqual(
            [call.kwargs["language"] for call in import_movie.call_args_list],
            ["en-US", "pt-BR"],
        )

    def setUp(self):
        self.user = get_user_model().objects.create_user("user@example.com")

    @patch("apps.movies.tasks.hydrate_movie_translations")
    @patch("apps.movies.tasks.movie_services.import_movie")
    def test_sync_movie_imports_existing_movie_and_queues_translation_hydration(
        self,
        import_movie,
        hydrate_movie_translations,
    ):
        from apps.movies.tasks import sync_movie

        movie = Movie.objects.create(provider="tmdb", external_id="550", title="Fight Club")
        import_movie.return_value = movie

        sync_movie.func(movie.id)

        import_movie.assert_called_once_with("tmdb", "550", language="en-US")
        hydrate_movie_translations.defer.assert_called_once_with(movie_id=movie.id)

    @patch("apps.movies.tasks.hydrate_movie_translations")
    @patch("apps.movies.tasks.movie_services.import_movie")
    def test_sync_movie_returns_the_translation_task_id(
        self,
        import_movie,
        hydrate_movie_translations,
    ):
        from apps.movies.tasks import sync_movie

        movie = Movie.objects.create(provider="tmdb", external_id="550", title="Fight Club")
        import_movie.return_value = movie
        hydrate_movie_translations.defer.return_value = 41

        result = sync_movie.func(movie.id)

        hydrate_movie_translations.defer.assert_called_once_with(movie_id=movie.id)

        self.assertEqual(
            result,
            {"item_id": movie.id, "translation_task_id": 41},
        )

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
            sync_movie.func(movie.id)

        movie.refresh_from_db()
        self.assertEqual(movie.sync_status, SyncStatus.ERROR)

    @override_settings(CATALOG_MOVIE_SYNC_INTERVAL_DAYS=14)
    @patch("apps.movies.tasks.sync_movie")
    def test_sync_movies_enqueues_only_tracked_stale_movies(self, sync_movie):
        from apps.movies.tasks import sync_movies

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

        sync_movie.defer.side_effect = [41, 42]

        enqueued_task_ids = sync_movies.func()

        self.assertEqual(enqueued_task_ids, [41, 42])
        self.assertCountEqual(
            [call.kwargs["movie_id"] for call in sync_movie.defer.call_args_list],
            [stale_tracked.id, never_synced_tracked.id],
        )
        self.assertNotIn(
            stale_untracked.id,
            [call.kwargs["movie_id"] for call in sync_movie.defer.call_args_list],
        )

    @patch("apps.movies.tasks.sync_movie")
    def test_sync_movies_force_all_enqueues_every_tmdb_movie(self, sync_movie):
        from apps.movies.tasks import sync_movies

        first = self._create_movie(external_id="1", title="Tracked")
        second = self._create_movie(external_id="2", title="Untracked")
        Movie.objects.create(provider="other", external_id="3", title="Other provider")
        sync_movie.defer.side_effect = [41, 42]

        result = sync_movies.func(force_all=True)

        self.assertEqual(result, [41, 42])
        self.assertCountEqual(
            [call.kwargs["movie_id"] for call in sync_movie.defer.call_args_list],
            [first.id, second.id],
        )

    @patch("apps.movies.tasks.sync_movies")
    def test_daily_movie_sync_queues_default_dispatch(self, sync_movies):
        from apps.movies.tasks import daily_movie_sync

        daily_movie_sync.func(timestamp=0)

        sync_movies.defer.assert_called_once_with()

    def _create_movie(self, **overrides):
        defaults = {
            "provider": "tmdb",
            "external_id": "550",
            "title": "Fight Club",
        }
        defaults.update(overrides)
        return Movie.objects.create(**defaults)
