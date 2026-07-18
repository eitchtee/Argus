from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

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
        self.assertContains(response, "Search for movies or TV shows")
        self.assertContains(response, 'name="provider"')
        self.assertContains(response, "Provider")
        self.assertContains(response, 'id="provider-picker"')
        self.assertContains(response, 'aria-label="Search provider"')
        self.assertContains(response, 'value="tmdb"')
        self.assertContains(response, 'value="tvdb"')
        self.assertNotContains(response, '<select name="provider"')
        self.assertContains(response, 'data-default-provider="tmdb"')
        self.assertContains(response, 'data-default-provider="tvdb"')
        self.assertContains(response, "TMDB")
        self.assertContains(response, "TVDB")


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
        self.assertContains(response, ">Track<")
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

    @patch("apps.catalog.views.catalog_search")
    def test_results_link_to_tv_detail_page(self, catalog_search):
        catalog_search.return_value = [_show_dto()]
        response = self.client.get(
            "/search/results/?q=Foo&type=tv",
            HTTP_HX_REQUEST="true",
        )
        self.assertContains(response, "/tv/123/")

    @patch("apps.catalog.views.catalog_search")
    def test_results_show_already_tracked_state(self, catalog_search):
        movie = Movie.objects.create(external_id="550", provider="tmdb", title="Fight Club")
        UserMovie.objects.create(user=self.user, movie=movie, on_watchlist=True)
        catalog_search.return_value = [_movie_dto()]
        response = self.client.get(
            "/search/results/?q=Fight&type=movie",
            HTTP_HX_REQUEST="true",
        )
        self.assertContains(response, "Tracking")

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

    @patch("apps.movies.services.track_movie")
    def test_track_movie_calls_service_and_marks_tracked(self, track_movie_mock):
        def fake_track(user, provider, external_id):
            movie = Movie.objects.create(
                external_id="550", provider="tmdb", title="Fight Club"
            )
            UserMovie.objects.create(user=user, movie=movie, on_watchlist=True)

        track_movie_mock.side_effect = fake_track
        with patch("apps.catalog.views.catalog_search", return_value=[_movie_dto()]):
            response = self.client.post(
                "/search/track/",
                {"type": "movie", "external_id": "550", "q": "Fight", "page": "1"},
                HTTP_HX_REQUEST="true",
            )
        self.assertEqual(response.status_code, 200)
        track_movie_mock.assert_called_once_with(self.user, "tmdb", "550")
        self.assertContains(response, "Tracking")

    @patch("apps.movies.services.track_movie")
    def test_track_movie_uses_provider_from_search_result(self, track_movie_mock):
        def fake_track(user, provider, external_id):
            movie = Movie.objects.create(
                external_id=external_id, provider=provider, title="A Movie"
            )
            UserMovie.objects.create(user=user, movie=movie, on_watchlist=True)

        track_movie_mock.side_effect = fake_track
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
        track_movie_mock.assert_called_once_with(self.user, "tvdb", "42")
        self.assertTrue(
            Movie.objects.filter(provider="tvdb", external_id="42").exists()
        )

    @patch("apps.tv.services.track_show")
    def test_track_tv_calls_service_and_marks_tracked(self, track_show_mock):
        def fake_track(user, external_id, *, provider="tvdb"):
            show = Show.objects.create(provider=provider, external_id="123", name="Foo")
            UserShow.objects.create(user=user, show=show, status=UserShow.Status.TRACKED)

        track_show_mock.side_effect = fake_track
        with patch("apps.catalog.views.catalog_search", return_value=[_show_dto()]):
            response = self.client.post(
                "/search/track/",
                {"type": "tv", "external_id": "123", "q": "Foo", "page": "1"},
                HTTP_HX_REQUEST="true",
            )
        self.assertEqual(response.status_code, 200)
        track_show_mock.assert_called_once_with(self.user, "123", provider="tvdb")
        self.assertContains(response, "Tracking")

    @patch("apps.tv.services.track_show")
    def test_track_tv_uses_provider_from_search_result(self, track_show_mock):
        def fake_track(user, external_id, *, provider="tvdb"):
            show = Show.objects.create(
                provider=provider,
                external_id=external_id,
                name="Foo",
            )
            UserShow.objects.create(user=user, show=show, status=UserShow.Status.TRACKED)

        track_show_mock.side_effect = fake_track
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
        track_show_mock.assert_called_once_with(self.user, "123", provider="tmdb")
        self.assertTrue(Show.objects.filter(provider="tmdb", external_id="123").exists())
