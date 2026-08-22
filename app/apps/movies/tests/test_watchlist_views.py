from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.catalog.models import UserMediaArtworkPreference
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

    @patch("apps.movies.views.get_watchlist_movies")
    def test_page_shell_defers_watchlist_movies(self, get_watchlist_movies_mock):
        response = self.client.get(reverse("movies-watchlist-page"))

        get_watchlist_movies_mock.assert_not_called()
        self.assertContains(response, 'id="movies-watchlist-content"')
        self.assertContains(response, 'hx-get="/movies/watchlist/"')
        self.assertContains(response, 'hx-trigger="load"')
        self.assertNotContains(response, "Fight Club")

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
            response = self.client.get(
                reverse("movies-watchlist-page"), HTTP_HX_REQUEST="true"
            )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Fight Club")
        self.assertContains(response, "https://image.tmdb.org/t/p/w342/poster.jpg")
        self.assertContains(
            response,
            f'href="{reverse("movie-detail", kwargs={"external_id": "550"})}"',
        )
        self.assertContains(response, 'hx-boost="true" hx-target="body" hx-swap="innerHTML"')
        self.assertContains(response, 'aria-label="Remove from watchlist"')
        self.assertContains(
            response,
            f'hx-delete="{reverse("movie-poster-watchlist-remove", kwargs={"external_id": "550"})}"',
        )

    def test_watchlist_poster_actions_dismiss_card(self):
        movie = Movie.objects.create(external_id="550", title="Fight Club")
        UserMovie.objects.create(user=self.user, movie=movie, on_watchlist=True)

        response = self.client.get(
            reverse("movies-watchlist-page"), HTTP_HX_REQUEST="true"
        )

        content = response.content.decode()
        self.assertEqual(content.count('hx-target="closest .poster-card"'), 2)
        self.assertContains(response, 'hx-swap="delete"')
        self.assertContains(response, 'hx-trigger="confirmed"')
        self.assertContains(response, "Swal.fire")
        self.assertContains(response, "Remove from watchlist?")

    def test_watched_page_poster_button_keeps_in_place_swap(self):
        movie = Movie.objects.create(external_id="550", title="Fight Club")
        UserMovie.objects.create(user=self.user, movie=movie, is_seen=True)

        response = self.client.get(
            reverse("movies-watched-page"), HTTP_HX_REQUEST="true"
        )

        self.assertNotContains(response, 'hx-target="closest .poster-card"')
        self.assertContains(response, 'hx-target="this" hx-swap="outerHTML"')
        self.assertNotContains(response, "Seen")
        self.assertNotContains(response, "<c-movies.movie-poster")

    def test_missing_poster_renders_movie_placeholder(self):
        movie = Movie.objects.create(external_id="1", title="No Poster")
        UserMovie.objects.create(user=self.user, movie=movie, on_watchlist=True)

        response = self.client.get(
            reverse("movies-watchlist-page"), HTTP_HX_REQUEST="true"
        )

        self.assertContains(response, "No Poster")
        self.assertContains(response, "fa-film")
        self.assertNotContains(response, 'src=""')

    def test_watchlist_search_data_contains_translated_and_original_titles(self):
        movie = Movie.objects.create(
            external_id="550",
            title="Clube da Luta",
            original_title="Fight Club",
        )
        UserMovie.objects.create(user=self.user, movie=movie, on_watchlist=True)

        response = self.client.get(
            reverse("movies-watchlist-page"), HTTP_HX_REQUEST="true"
        )

        self.assertContains(response, 'data-name="Clube da Luta Fight Club"')

    def test_watchlist_displays_original_title_for_users_who_enable_it(self):
        movie = Movie.objects.create(
            external_id="550",
            title="Clube da Luta",
            original_title="Fight Club",
        )
        UserMovie.objects.create(user=self.user, movie=movie, on_watchlist=True)
        UserMediaArtworkPreference.objects.create(
            user=self.user,
            provider="tmdb",
            media_type="movie",
            external_id="550",
            use_original_title=True,
        )

        response = self.client.get(
            reverse("movies-watchlist-page"), HTTP_HX_REQUEST="true"
        )

        self.assertContains(
            response,
            '<h2 class="poster-card__title" title="Fight Club">Fight Club</h2>',
        )
        self.assertContains(response, 'data-name="Clube da Luta Fight Club"')

    def test_empty_watchlist_renders_empty_state(self):
        response = self.client.get(
            reverse("movies-watchlist-page"), HTTP_HX_REQUEST="true"
        )

        self.assertContains(response, "No movies in your watchlist.")
