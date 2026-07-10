from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings


@override_settings(
    STORAGES={
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {
            "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"
        },
    },
    DJANGO_VITE_DEV_MODE=True,
)
class IndexViewTests(TestCase):
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
        response = self.client.get("/")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login/", response["Location"])

    def test_renders_for_authenticated_user(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)

    def test_renders_tv_tabs(self):
        response = self.client.get("/")

        self.assertContains(response, 'aria-label="Watchlist"')
        self.assertContains(response, 'aria-label="Upcoming"')
        self.assertContains(response, "/tv/home/watchlist/")
        self.assertContains(response, "/tv/home/upcoming/")

    def test_watchlist_tab_is_checked_by_default(self):
        response = self.client.get("/")
        content = response.content.decode()

        watchlist_input_start = content.index('aria-label="Watchlist"')
        watchlist_tag_start = content.rindex("<input", 0, watchlist_input_start)
        watchlist_tag_end = content.index(">", watchlist_input_start)
        self.assertIn("checked", content[watchlist_tag_start:watchlist_tag_end])

    def test_shows_empty_state_when_no_watchlist_movies(self):
        response = self.client.get("/")

        self.assertContains(response, "Nothing to suggest")

    def test_shows_watch_something_movie(self):
        from apps.movies.models import Movie, UserMovie

        movie = Movie.objects.create(provider="tmdb", external_id="1", title="Interstellar")
        UserMovie.objects.create(user=self.user, movie=movie, on_watchlist=True)

        response = self.client.get("/")

        self.assertContains(response, "Interstellar")
        self.assertContains(response, "/movies/1/")
