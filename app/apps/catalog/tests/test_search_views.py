from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.catalog.models import SyncStatus
from apps.catalog.providers.base import SearchResultDTO
from apps.movies.models import Movie, UserMovie
from apps.tv.models import Show, UserShow


def _movie_dto():
    return SearchResultDTO(
        provider="tmdb",
        external_id="550",
        title="Fight Club",
        year=1999,
        poster_url="https://image.tmdb.org/t/p/w342/poster.jpg",
        overview="A great movie.",
    )


def _show_dto():
    return SearchResultDTO(
        provider="tvdb",
        external_id="123",
        title="Foo",
        year=None,
        poster_url=None,
        overview="A show.",
    )


@override_settings(
    STORAGES={
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {
            "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"
        },
    },
    DJANGO_VITE_DEV_MODE=True,
)
class SearchPageViewTests(TestCase):
    def setUp(self):
        from django_vite.core.asset_loader import DjangoViteAssetLoader

        DjangoViteAssetLoader._instance = None

        User = get_user_model()
        self.user = User.objects.create_user("user@example.com", password="password")
        self.client.login(username="user@example.com", password="password")
        self.user.settings.tmdb_metadata_language = "pt-BR"
        self.user.settings.tvdb_metadata_language = "por"
        self.user.settings.save()

    def tearDown(self):
        from django_vite.core.asset_loader import DjangoViteAssetLoader

        DjangoViteAssetLoader._instance = None

    def test_page_requires_auth(self):
        self.client.logout()
        response = self.client.get("/search/")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login/", response["Location"])

    def test_page_renders_form_and_initial_state(self):
        response = self.client.get("/search/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "search-form")
        self.assertContains(response, 'id="search-results"')
        self.assertContains(response, 'name="provider"')
        self.assertNotContains(response, "uppercase tracking-wide text-subtle")
        self.assertContains(response, 'id="provider-picker"')
        self.assertContains(response, 'aria-label="Search provider"')
        self.assertContains(response, 'value="tmdb"')
        self.assertContains(response, 'value="tvdb"')
        self.assertNotContains(response, '<select name="provider"')
        self.assertContains(response, 'data-default-provider="tmdb"')
        self.assertContains(response, 'data-default-provider="tvdb"')
        self.assertContains(response, "TMDB")
        self.assertContains(response, "TVDB")

    @patch("apps.catalog.views.catalog_search")
    def test_page_shell_defers_search_results(self, catalog_search):
        response = self.client.get("/search/?q=Fight&type=movie&provider=tmdb")

        catalog_search.assert_not_called()
        self.assertContains(response, 'id="search-results"')
        self.assertContains(response, 'hx-trigger="load"')
        self.assertContains(response, "/search/results/?q=Fight&amp;type=movie&amp;provider=tmdb")
        self.assertNotContains(response, "Fight Club")


class SearchResultsViewTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user("user@example.com", password="password")
        self.client.login(username="user@example.com", password="password")
        self.user.settings.tmdb_metadata_language = "pt-BR"
        self.user.settings.tvdb_metadata_language = "por"
        self.user.settings.save()

    @patch("apps.catalog.views.catalog_search")
    def test_results_require_htmx_header(self, catalog_search):
        response = self.client.get("/search/results/?q=Fight&type=movie")
        self.assertEqual(response.status_code, 403)
        catalog_search.assert_not_called()

    @patch("apps.catalog.views.catalog_search")
    def test_results_render_cards(self, catalog_search):
        catalog_search.return_value = [_movie_dto()]
        response = self.client.get(
            "/search/results/?q=Fight&type=movie",
            HTTP_HX_REQUEST="true",
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Fight Club")
        self.assertContains(response, 'aria-label="Add to watchlist"')
        self.assertContains(response, 'data-tippy-content="Add to watchlist"')
        self.assertContains(response, 'data-lucide="bookmark"')
        self.assertContains(response, 'class="relative z-[2] flex shrink-0 items-center p-3"')
        self.assertNotContains(response, ">Track<")
        catalog_search.assert_called_once_with(
            "Fight",
            media_type="movie",
            language="pt-BR",
            page=1,
            provider="tmdb",
        )

    @patch("apps.catalog.views.catalog_search")
    def test_results_use_selected_provider_and_language(self, catalog_search):
        catalog_search.return_value = [
            SearchResultDTO(
                provider="tvdb",
                external_id="550",
                title="Fight Club",
                year=1999,
                poster_url=None,
                overview="A great movie.",
            )
        ]
        self.user.settings.tvdb_metadata_language = "por"
        self.user.settings.save()

        response = self.client.get(
            "/search/results/?q=Fight&type=movie&provider=tvdb",
            HTTP_HX_REQUEST="true",
        )

        self.assertEqual(response.status_code, 200)
        catalog_search.assert_called_once_with(
            "Fight",
            media_type="movie",
            language="por",
            page=1,
            provider="tvdb",
        )
        self.assertContains(response, "provider=tvdb")

    @patch("apps.catalog.views.catalog_search")
    def test_results_link_to_movie_detail_page(self, catalog_search):
        catalog_search.return_value = [_movie_dto()]
        response = self.client.get(
            "/search/results/?q=Fight&type=movie",
            HTTP_HX_REQUEST="true",
        )
        self.assertContains(response, "/movies/550/")
        self.assertContains(response, 'hx-boost="true" hx-target="body" hx-swap="innerHTML"')

    @patch("apps.catalog.views.catalog_search")
    def test_results_link_to_tv_detail_page(self, catalog_search):
        catalog_search.return_value = [_show_dto()]
        response = self.client.get(
            "/search/results/?q=Foo&type=tv",
            HTTP_HX_REQUEST="true",
        )
        self.assertContains(response, "/tv/123/")
        self.assertContains(response, 'hx-boost="true" hx-target="body" hx-swap="innerHTML"')

    @patch("apps.catalog.views.catalog_search")
    def test_results_show_already_tracked_state(self, catalog_search):
        movie = Movie.objects.create(external_id="550", provider="tmdb", title="Fight Club")
        UserMovie.objects.create(user=self.user, movie=movie, on_watchlist=True)
        catalog_search.return_value = [_movie_dto()]
        response = self.client.get(
            "/search/results/?q=Fight&type=movie",
            HTTP_HX_REQUEST="true",
        )
        self.assertContains(response, 'aria-label="Mark watched"')
        self.assertContains(response, 'data-lucide="eye"')
        self.assertNotContains(response, 'data-lucide="bookmark"')

    @patch("apps.catalog.views.catalog_search")
    def test_results_show_watched_movie_state(self, catalog_search):
        movie = Movie.objects.create(
            external_id="550",
            provider="tmdb",
            title="Fight Club",
            sync_status=SyncStatus.OK,
            last_synced_at=timezone.now(),
        )
        UserMovie.objects.create(user=self.user, movie=movie, is_seen=True)
        catalog_search.return_value = [_movie_dto()]

        response = self.client.get(
            "/search/results/?q=Fight&type=movie",
            HTTP_HX_REQUEST="true",
        )

        self.assertContains(response, 'aria-label="Mark unwatched"')
        self.assertContains(response, 'data-tippy-content="Mark unwatched"')
        self.assertContains(response, 'data-lucide="eye-off"')
        self.assertNotContains(response, 'aria-label="Mark watched"')

    @patch("apps.catalog.views.catalog_search")
    def test_results_show_other_provider_tracked_state(self, catalog_search):
        movie = Movie.objects.create(
            provider="tmdb",
            external_id="550",
            tvdb_id="42",
            title="Fight Club",
        )
        UserMovie.objects.create(user=self.user, movie=movie, on_watchlist=True)
        catalog_search.return_value = [
            SearchResultDTO(
                provider="tvdb",
                external_id="42",
                title="Fight Club",
                year=1999,
                poster_url=None,
                overview="A great movie.",
            )
        ]

        response = self.client.get(
            "/search/results/?q=Fight&type=movie&provider=tvdb",
            HTTP_HX_REQUEST="true",
        )

        self.assertContains(response, 'aria-label="Switch to TVDB"')
        self.assertContains(response, 'data-tippy-content="Switch to TVDB"')
        self.assertContains(response, "from_external_id=550")
        self.assertContains(response, 'data-lucide="arrow-right-left"')
        self.assertContains(response, "Swal.fire")
        self.assertNotContains(response, "Tracked on another provider")
        self.assertNotContains(response, 'data-lucide="eye"')

    @patch("apps.catalog.views.catalog_search")
    def test_tv_results_use_track_icon(self, catalog_search):
        catalog_search.return_value = [_show_dto()]

        response = self.client.get(
            "/search/results/?q=Foo&type=tv",
            HTTP_HX_REQUEST="true",
        )

        self.assertContains(response, 'aria-label="Track show"')
        self.assertContains(response, 'data-tippy-content="Track show"')
        self.assertContains(response, 'data-lucide="bookmark"')
        self.assertNotContains(response, 'data-lucide="eye"')

    @patch("apps.catalog.views.catalog_search")
    def test_tv_results_do_not_render_watched_action_when_tracked(self, catalog_search):
        show = Show.objects.create(external_id="123", provider="tvdb", name="Foo")
        UserShow.objects.create(
            user=self.user,
            show=show,
            status=UserShow.Status.TRACKED,
        )
        catalog_search.return_value = [_show_dto()]

        response = self.client.get(
            "/search/results/?q=Foo&type=tv",
            HTTP_HX_REQUEST="true",
        )

        self.assertContains(response, 'aria-label="Tracking"')
        self.assertContains(response, 'data-lucide="bookmark"')
        self.assertNotContains(response, 'data-lucide="eye"')

    @patch("apps.catalog.views.catalog_search")
    def test_movie_watched_action_marks_and_unmarks_movie(self, catalog_search):
        movie = Movie.objects.create(
            external_id="550",
            provider="tmdb",
            title="Fight Club",
            sync_status=SyncStatus.OK,
            last_synced_at=timezone.now(),
        )
        UserMovie.objects.create(user=self.user, movie=movie, on_watchlist=True)
        catalog_search.return_value = [_movie_dto()]
        watched_url = "/search/watched/?type=movie&provider=tmdb&external_id=550&q=Fight&page=1"

        response = self.client.post(watched_url, HTTP_HX_REQUEST="true")

        self.assertEqual(response.status_code, 200)
        self.assertTrue(UserMovie.objects.get(user=self.user, movie=movie).is_seen)
        self.assertContains(response, 'aria-label="Mark unwatched"')

        response = self.client.delete(watched_url, HTTP_HX_REQUEST="true")

        self.assertEqual(response.status_code, 200)
        self.assertFalse(UserMovie.objects.get(user=self.user, movie=movie).is_seen)
        self.assertContains(response, 'aria-label="Mark watched"')

    @patch("apps.catalog.views.catalog_search")
    @patch("apps.movies.services.queue_switch_movie_provider")
    def test_movie_provider_switch_replaces_switch_with_watched_action(
        self,
        queue_switch_movie_provider_mock,
        catalog_search,
    ):
        source = Movie.objects.create(
            provider="tmdb",
            external_id="550",
            tvdb_id="42",
            title="Fight Club",
        )
        UserMovie.objects.create(user=self.user, movie=source, on_watchlist=True)
        target = Movie.objects.create(
            provider="tvdb",
            external_id="42",
            title="Fight Club",
        )

        def switch_movie(user, **kwargs):
            UserMovie.objects.create(user=user, movie=target, on_watchlist=True)
            return target

        queue_switch_movie_provider_mock.side_effect = switch_movie
        catalog_search.return_value = [
            SearchResultDTO(
                provider="tvdb",
                external_id="42",
                title="Fight Club",
                year=1999,
                poster_url=None,
                overview="A great movie.",
            )
        ]

        response = self.client.post(
            "/search/switch/?type=movie&provider=tvdb&external_id=42&"
            "from_provider=tmdb&from_external_id=550&q=Fight&page=1",
            HTTP_HX_REQUEST="true",
        )

        self.assertEqual(response.status_code, 200)
        queue_switch_movie_provider_mock.assert_called_once_with(
            self.user,
            source_provider="tmdb",
            source_external_id="550",
            target_provider="tvdb",
            target_external_id="42",
        )
        self.assertContains(response, 'aria-label="Mark watched"')
        self.assertContains(response, 'data-lucide="eye"')
        self.assertNotContains(response, 'data-lucide="arrow-right-left"')

    @patch("apps.catalog.views.catalog_search")
    @patch("apps.movies.services.queue_switch_movie_provider")
    def test_movie_provider_switch_renders_service_error(
        self,
        queue_switch_movie_provider_mock,
        catalog_search,
    ):
        source = Movie.objects.create(
            provider="tmdb",
            external_id="550",
            tvdb_id="42",
            title="Fight Club",
        )
        UserMovie.objects.create(user=self.user, movie=source, on_watchlist=True)
        queue_switch_movie_provider_mock.side_effect = ValueError(
            "Movies do not match across providers."
        )
        catalog_search.return_value = [
            SearchResultDTO(
                provider="tvdb",
                external_id="42",
                title="Fight Club",
                year=1999,
                poster_url=None,
                overview="A great movie.",
            )
        ]

        response = self.client.post(
            "/search/switch/?type=movie&provider=tvdb&external_id=42&"
            "from_provider=tmdb&from_external_id=550&q=Fight&page=1",
            HTTP_HX_REQUEST="true",
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Movies do not match across providers.")
        self.assertContains(response, 'data-lucide="arrow-right-left"')

    @patch("apps.catalog.views.catalog_search")
    @patch("apps.tv.services.queue_switch_show_provider")
    def test_tv_provider_switch_replaces_switch_with_tracking_action(
        self,
        queue_switch_show_provider_mock,
        catalog_search,
    ):
        source = Show.objects.create(
            provider="tvdb",
            external_id="123",
            tmdb_id="1399",
            name="Foo",
        )
        UserShow.objects.create(
            user=self.user,
            show=source,
            status=UserShow.Status.TRACKED,
        )
        target = Show.objects.create(
            provider="tmdb",
            external_id="1399",
            name="Foo",
        )

        def switch_show(user, **kwargs):
            UserShow.objects.create(
                user=user,
                show=target,
                status=UserShow.Status.TRACKED,
            )
            return target

        queue_switch_show_provider_mock.side_effect = switch_show
        catalog_search.return_value = [
            SearchResultDTO(
                provider="tmdb",
                external_id="1399",
                title="Foo",
                year=None,
                poster_url=None,
                overview="A show.",
            )
        ]

        response = self.client.post(
            "/search/switch/?type=tv&provider=tmdb&external_id=1399&"
            "from_provider=tvdb&from_external_id=123&q=Foo&page=1",
            HTTP_HX_REQUEST="true",
        )

        self.assertEqual(response.status_code, 200)
        queue_switch_show_provider_mock.assert_called_once_with(
            self.user,
            source_provider="tvdb",
            source_external_id="123",
            target_provider="tmdb",
            target_external_id="1399",
        )
        self.assertContains(response, 'aria-label="Tracking"')
        self.assertContains(response, 'data-lucide="bookmark"')
        self.assertNotContains(response, 'data-lucide="arrow-right-left"')

    @override_settings(DEMO=True)
    def test_search_provider_switch_is_blocked_in_demo_mode(self):
        response = self.client.post(
            "/search/switch/?type=movie&provider=tvdb&external_id=42&"
            "from_provider=tmdb&from_external_id=550&q=Fight&page=1",
            HTTP_HX_REQUEST="true",
        )

        self.assertEqual(response.status_code, 403)

    @patch("apps.catalog.views.catalog_search")
    def test_results_empty_query_returns_initial_state(self, catalog_search):
        response = self.client.get("/search/results/?q=&type=movie", HTTP_HX_REQUEST="true")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Search for movies or TV shows")
        catalog_search.assert_not_called()


class TrackViewTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user("user@example.com", password="password")
        self.client.login(username="user@example.com", password="password")

    def test_track_requires_htmx_header(self):
        response = self.client.post("/search/track/", {"type": "movie", "external_id": "550"})
        self.assertEqual(response.status_code, 403)

    def test_track_unauthenticated_redirects(self):
        self.client.logout()
        response = self.client.post(
            "/search/track/",
            {"type": "movie", "external_id": "550"},
            HTTP_HX_REQUEST="true",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["HX-Redirect"], "/login/")

    def test_track_invalid_type_shows_error(self):
        with patch("apps.catalog.views.catalog_search", return_value=[]):
            response = self.client.post(
                "/search/track/",
                {"type": "anime", "external_id": "550", "q": "x"},
                HTTP_HX_REQUEST="true",
            )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Invalid request")

    @override_settings(DEMO=True)
    def test_track_blocked_in_demo_mode_for_non_superusers(self):
        response = self.client.post(
            "/search/track/",
            {"type": "movie", "external_id": "550", "q": "x"},
            HTTP_HX_REQUEST="true",
        )
        self.assertEqual(response.status_code, 403)

    @patch("apps.movies.services.queue_track_movie")
    def test_track_movie_calls_service_and_marks_tracked(self, queue_track_movie_mock):
        def fake_track(user, provider, external_id):
            movie = Movie.objects.create(
                external_id="550", provider="tmdb", title="Fight Club"
            )
            UserMovie.objects.create(user=user, movie=movie, on_watchlist=True)

        queue_track_movie_mock.side_effect = fake_track
        with patch("apps.catalog.views.catalog_search", return_value=[_movie_dto()]):
            response = self.client.post(
                "/search/track/",
                {"type": "movie", "external_id": "550", "q": "Fight", "page": "1"},
                HTTP_HX_REQUEST="true",
            )
        self.assertEqual(response.status_code, 200)
        queue_track_movie_mock.assert_called_once_with(self.user, "tmdb", "550")
        self.assertContains(response, 'aria-label="Mark watched"')
        self.assertContains(response, 'data-lucide="eye"')

    @patch("apps.movies.services.track_movie")
    @patch("apps.movies.services.queue_track_movie")
    def test_track_movie_queues_background_service(self, queue_track_movie_mock, track_movie_mock):
        movie = Movie.objects.create(
            external_id="550",
            provider="tmdb",
            title="Fight Club",
        )
        user_movie = UserMovie.objects.create(
            user=self.user,
            movie=movie,
            on_watchlist=True,
        )
        queue_track_movie_mock.return_value = user_movie
        with patch("apps.catalog.views.catalog_search", return_value=[_movie_dto()]):
            response = self.client.post(
                "/search/track/",
                {"type": "movie", "external_id": "550", "q": "Fight", "page": "1"},
                HTTP_HX_REQUEST="true",
            )

        self.assertEqual(response.status_code, 200)
        queue_track_movie_mock.assert_called_once_with(self.user, "tmdb", "550")
        track_movie_mock.assert_not_called()

    @patch("apps.movies.services.queue_track_movie")
    def test_track_movie_uses_provider_from_search_result(self, queue_track_movie_mock):
        def fake_track(user, provider, external_id):
            movie = Movie.objects.create(
                external_id=external_id, provider=provider, title="A Movie"
            )
            UserMovie.objects.create(user=user, movie=movie, on_watchlist=True)

        queue_track_movie_mock.side_effect = fake_track
        dto = SearchResultDTO(
            provider="tvdb",
            external_id="42",
            title="A Movie",
            year=2020,
            poster_url=None,
            overview="Overview",
        )
        with patch("apps.catalog.views.catalog_search", return_value=[dto]):
            response = self.client.post(
                "/search/track/",
                {
                    "type": "movie",
                    "provider": "tvdb",
                    "external_id": "42",
                    "q": "A Movie",
                    "page": "1",
                },
                HTTP_HX_REQUEST="true",
            )

        self.assertEqual(response.status_code, 200)
        queue_track_movie_mock.assert_called_once_with(self.user, "tvdb", "42")
        self.assertTrue(
            Movie.objects.filter(provider="tvdb", external_id="42").exists()
        )

    @patch("apps.tv.services.queue_track_show")
    def test_track_tv_calls_service_and_marks_tracked(self, queue_track_show_mock):
        def fake_track(user, external_id, *, provider="tvdb"):
            show = Show.objects.create(provider=provider, external_id="123", name="Foo")
            UserShow.objects.create(user=user, show=show, status=UserShow.Status.TRACKED)

        queue_track_show_mock.side_effect = fake_track
        with patch("apps.catalog.views.catalog_search", return_value=[_show_dto()]):
            response = self.client.post(
                "/search/track/",
                {"type": "tv", "external_id": "123", "q": "Foo", "page": "1"},
                HTTP_HX_REQUEST="true",
            )
        self.assertEqual(response.status_code, 200)
        queue_track_show_mock.assert_called_once_with(self.user, "123", provider="tvdb")
        self.assertContains(response, "Tracking")

    @patch("apps.tv.services.track_show")
    @patch("apps.tv.services.queue_track_show")
    def test_track_tv_queues_background_service(self, queue_track_show_mock, track_show_mock):
        show = Show.objects.create(provider="tvdb", external_id="123", name="Foo")
        user_show = UserShow.objects.create(
            user=self.user,
            show=show,
            status=UserShow.Status.TRACKED,
        )
        queue_track_show_mock.return_value = user_show
        with patch("apps.catalog.views.catalog_search", return_value=[_show_dto()]):
            response = self.client.post(
                "/search/track/",
                {"type": "tv", "external_id": "123", "q": "Foo", "page": "1"},
                HTTP_HX_REQUEST="true",
            )

        self.assertEqual(response.status_code, 200)
        queue_track_show_mock.assert_called_once_with(self.user, "123", provider="tvdb")
        track_show_mock.assert_not_called()

    @patch("apps.tv.services.queue_track_show")
    def test_track_tv_uses_provider_from_search_result(self, queue_track_show_mock):
        def fake_track(user, external_id, *, provider="tvdb"):
            show = Show.objects.create(
                provider=provider,
                external_id=external_id,
                name="Foo",
            )
            UserShow.objects.create(user=user, show=show, status=UserShow.Status.TRACKED)

        queue_track_show_mock.side_effect = fake_track
        dto = SearchResultDTO(
            provider="tmdb",
            external_id="123",
            title="Foo",
            year=None,
            poster_url=None,
            overview="A show.",
        )
        with patch("apps.catalog.views.catalog_search", return_value=[dto]):
            response = self.client.post(
                "/search/track/",
                {
                    "type": "tv",
                    "provider": "tmdb",
                    "external_id": "123",
                    "q": "Foo",
                    "page": "1",
                },
                HTTP_HX_REQUEST="true",
            )

        self.assertEqual(response.status_code, 200)
        queue_track_show_mock.assert_called_once_with(self.user, "123", provider="tmdb")
        self.assertTrue(Show.objects.filter(provider="tmdb", external_id="123").exists())
