from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from apps.catalog.providers.base import CastMemberDTO, DetailDTO
from apps.movies.models import Movie, UserMovie


@override_settings(
    STORAGES={
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {
            "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"
        },
    },
    DJANGO_VITE_DEV_MODE=True,
)
class MovieDetailViewTests(TestCase):
    def setUp(self):
        from django_vite.core.asset_loader import DjangoViteAssetLoader

        DjangoViteAssetLoader._instance = None

        self.user = get_user_model().objects.create_user("user@example.com", password="password")
        self.client.login(username="user@example.com", password="password")

    def tearDown(self):
        from django_vite.core.asset_loader import DjangoViteAssetLoader

        DjangoViteAssetLoader._instance = None

    def test_requires_auth(self):
        self.client.logout()
        response = self.client.get("/movies/550/")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login/", response["Location"])

    def test_renders_from_db_when_movie_already_imported(self):
        Movie.objects.create(
            external_id="550",
            title="Fight Club",
            overview="A movie about a fight club.",
            backdrop_path="/backdrop.jpg",
            director="David Fincher",
            trailer_url="https://www.youtube.com/watch?v=SUXWAEX2jlg",
            cast=[{"name": "Edward Norton", "character": "The Narrator", "photo_url": "/norton.jpg"}],
        )

        with self.settings(TMDB_IMAGE_BASE_URL="https://image.tmdb.org/t/p/"):
            response = self.client.get("/movies/550/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Fight Club")
        self.assertContains(response, "A movie about a fight club.")
        self.assertContains(response, ">Track<")
        self.assertContains(response, "https://image.tmdb.org/t/p/w1280/backdrop.jpg")
        self.assertContains(response, "David Fincher")
        self.assertContains(response, "https://www.youtube.com/watch?v=SUXWAEX2jlg")
        self.assertContains(response, "Edward Norton")
        self.assertContains(response, "The Narrator")

    def test_shows_current_users_watchlist_state(self):
        movie = Movie.objects.create(external_id="550", title="Fight Club")
        UserMovie.objects.create(user=self.user, movie=movie, on_watchlist=True)

        response = self.client.get("/movies/550/")

        self.assertContains(response, ">Untrack<")

    def test_does_not_leak_another_users_watchlist_state(self):
        other_user = get_user_model().objects.create_user("other@example.com")
        movie = Movie.objects.create(external_id="550", title="Fight Club")
        UserMovie.objects.create(user=other_user, movie=movie, on_watchlist=True)

        response = self.client.get("/movies/550/")

        self.assertContains(response, ">Track<")

    @patch("apps.movies.views.get_movie_detail")
    def test_renders_from_provider_cache_when_not_yet_imported(self, get_movie_detail_mock):
        get_movie_detail_mock.return_value = DetailDTO(
            provider="tmdb",
            external_id="603",
            title="The Matrix",
            overview="A hacker learns the truth.",
            director="The Wachowskis",
            trailer_url="https://www.youtube.com/watch?v=vKQi3bta_kk",
            cast=[CastMemberDTO(name="Keanu Reeves", character="Neo", photo_url="/keanu.jpg")],
        )

        response = self.client.get("/movies/603/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "The Matrix")
        self.assertContains(response, "The Wachowskis")
        self.assertContains(response, "https://www.youtube.com/watch?v=vKQi3bta_kk")
        self.assertContains(response, "Keanu Reeves")
        self.assertFalse(Movie.objects.filter(external_id="603").exists())


class MovieTrackViewTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("user@example.com", password="password")
        self.client.login(username="user@example.com", password="password")

    def test_requires_htmx_header(self):
        response = self.client.post("/movies/550/track/")
        self.assertEqual(response.status_code, 403)

    @patch("apps.movies.views.track_movie")
    def test_post_tracks_movie(self, track_movie_mock):
        movie = Movie.objects.create(external_id="550", title="Fight Club")
        track_movie_mock.return_value = UserMovie.objects.create(
            user=self.user, movie=movie, on_watchlist=True
        )

        response = self.client.post("/movies/550/track/", HTTP_HX_REQUEST="true")

        track_movie_mock.assert_called_once_with(self.user, "tmdb", "550")
        self.assertContains(response, ">Untrack<")

    def test_delete_untracks_movie(self):
        movie = Movie.objects.create(external_id="550", title="Fight Club")
        UserMovie.objects.create(user=self.user, movie=movie, on_watchlist=True)

        response = self.client.delete("/movies/550/track/", HTTP_HX_REQUEST="true")

        self.assertContains(response, ">Track<")
        self.assertFalse(
            UserMovie.objects.filter(user=self.user, movie=movie, on_watchlist=True).exists()
        )

    @patch("apps.movies.views.track_movie")
    def test_demo_mode_blocks_non_superusers(self, track_movie_mock):
        with self.settings(DEMO=True):
            response = self.client.post("/movies/550/track/", HTTP_HX_REQUEST="true")

        self.assertEqual(response.status_code, 403)
        track_movie_mock.assert_not_called()


class MovieWatchedViewTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("user@example.com", password="password")
        self.client.login(username="user@example.com", password="password")

    def test_requires_htmx_header(self):
        response = self.client.post("/movies/550/watched/")
        self.assertEqual(response.status_code, 403)

    @patch("apps.movies.views.import_movie")
    def test_post_marks_watched_without_prior_tracking(self, import_movie_mock):
        movie = Movie.objects.create(external_id="550", title="Fight Club")
        import_movie_mock.return_value = movie

        response = self.client.post("/movies/550/watched/", HTTP_HX_REQUEST="true")

        import_movie_mock.assert_called_once_with("tmdb", "550")
        self.assertContains(response, ">Mark Unwatched<")
        self.assertTrue(
            UserMovie.objects.filter(user=self.user, movie=movie, is_seen=True).exists()
        )

    @patch("apps.movies.views.import_movie")
    def test_delete_marks_unwatched(self, import_movie_mock):
        movie = Movie.objects.create(external_id="550", title="Fight Club")
        UserMovie.objects.create(user=self.user, movie=movie, is_seen=True)
        import_movie_mock.return_value = movie

        response = self.client.delete("/movies/550/watched/", HTTP_HX_REQUEST="true")

        self.assertContains(response, ">Mark Watched<")
        self.assertFalse(
            UserMovie.objects.filter(user=self.user, movie=movie, is_seen=True).exists()
        )
