from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

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
class MovieWatchlistViewTests(TestCase):
    def setUp(self):
        from django_vite.core.asset_loader import DjangoViteAssetLoader

        DjangoViteAssetLoader._instance = None
        self.user = get_user_model().objects.create_user(
            "user@example.com", password="password"
        )
        self.client.login(username="user@example.com", password="password")

    def tearDown(self):
        from django_vite.core.asset_loader import DjangoViteAssetLoader

        DjangoViteAssetLoader._instance = None

    def test_requires_authentication(self):
        self.client.logout()

        response = self.client.get(reverse("movies-watchlist-page"))

        self.assertEqual(response.status_code, 302)
        self.assertIn("/login/", response["Location"])

    def test_renders_poster_cards_for_unwatched_watchlist_movies(self):
        movie = Movie.objects.create(
            external_id="550",
            title="Fight Club",
            poster_path="/poster.jpg",
        )
        UserMovie.objects.create(user=self.user, movie=movie, on_watchlist=True)
        UserMovie.objects.create(
            user=self.user,
            movie=Movie.objects.create(external_id="1", title="Seen"),
            on_watchlist=True,
            is_seen=True,
        )

        with self.settings(TMDB_IMAGE_BASE_URL="https://image.tmdb.org/t/p/"):
            response = self.client.get(reverse("movies-watchlist-page"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Fight Club")
        self.assertContains(response, "https://image.tmdb.org/t/p/w342/poster.jpg")
        self.assertContains(
            response,
            f'href="{reverse("movie-detail", kwargs={"external_id": "550"})}"',
        )
        self.assertContains(response, 'class="group card overflow-hidden')
        self.assertNotContains(response, "Seen")
        self.assertNotContains(response, "<c-movies.movie-poster")

    def test_missing_poster_renders_movie_placeholder(self):
        movie = Movie.objects.create(external_id="1", title="No Poster")
        UserMovie.objects.create(user=self.user, movie=movie, on_watchlist=True)

        response = self.client.get(reverse("movies-watchlist-page"))

        self.assertContains(response, "No Poster")
        self.assertContains(response, "fa-film")
        self.assertNotContains(response, 'src=""')

    def test_empty_watchlist_renders_empty_state(self):
        response = self.client.get(reverse("movies-watchlist-page"))

        self.assertContains(response, "No movies in your watchlist.")
