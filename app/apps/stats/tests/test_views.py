from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.catalog.models import Genre
from apps.movies.models import Movie, UserMovie
from apps.tv.models import Episode, Season, Show, UserEpisode, UserShow


@override_settings(
    STORAGES={
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {
            "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"
        },
    },
    DJANGO_VITE_DEV_MODE=True,
)
class StatsViewTests(TestCase):
    def setUp(self):
        from django_vite.core.asset_loader import DjangoViteAssetLoader

        DjangoViteAssetLoader._instance = None
        self.user = get_user_model().objects.create_user(
            "user@example.com",
            password="password",
        )
        self.client.login(username="user@example.com", password="password")

    def tearDown(self):
        from django_vite.core.asset_loader import DjangoViteAssetLoader

        DjangoViteAssetLoader._instance = None

    def test_requires_authentication(self):
        self.client.logout()

        response = self.client.get(reverse("stats-page"))

        self.assertEqual(response.status_code, 302)
        self.assertIn("/login/", response["Location"])

    def test_page_shell_defers_stats_content_and_sidebar_links_it(self):
        response = self.client.get(reverse("stats-page"))

        self.assertContains(response, 'id="stats-content"')
        self.assertContains(response, 'hx-get="/stats/"')
        self.assertContains(response, 'hx-trigger="load"')
        self.assertContains(response, 'data-lucide="chart-column"')
        self.assertNotContains(response, 'data-stat="time-watched"')

    def test_empty_stats_render_empty_state(self):
        response = self.client.get(reverse("stats-page"), HTTP_HX_REQUEST="true")

        self.assertContains(response, "No stats yet.")

    def test_fragment_renders_totals_genres_shows_and_records(self):
        genre = Genre.objects.create(provider="tmdb", external_id="18", name="Drama")
        movie = Movie.objects.create(external_id="10", title="Very long movie", runtime=190)
        movie.genres.add(genre)
        UserMovie.objects.create(
            user=self.user,
            movie=movie,
            is_seen=True,
            seen_at=timezone.now(),
        )
        show = Show.objects.create(external_id="20", name="Binged show", provider="tmdb")
        UserShow.objects.create(user=self.user, show=show)
        season = Season.objects.create(show=show, season_number=1)
        for number in (1, 2):
            episode = Episode.objects.create(
                show=show,
                season=season,
                season_number=1,
                episode_number=number,
                runtime=60,
                air_date=timezone.localdate(),
            )
            UserEpisode.objects.create(user=self.user, episode=episode)

        response = self.client.get(reverse("stats-page"), HTTP_HX_REQUEST="true")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-stat="time-watched"')
        self.assertContains(response, "5h 10m")
        self.assertContains(response, 'data-stat-genre="Drama"')
        self.assertContains(response, "Binged show")
        self.assertContains(response, reverse("tv-detail", kwargs={"external_id": "20"}) + "?provider=tmdb")
        self.assertContains(response, 'data-stat="binge"')
        self.assertContains(response, 'data-stat="longest-movie"')
        self.assertContains(response, "Very long movie")
        self.assertContains(response, "3h 10m")
