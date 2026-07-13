from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

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
class EpisodeDetailViewTests(TestCase):
    def setUp(self):
        from django_vite.core.asset_loader import DjangoViteAssetLoader

        DjangoViteAssetLoader._instance = None

        self.user = get_user_model().objects.create_user("user@example.com", password="password")
        self.client.login(username="user@example.com", password="password")
        self.show = Show.objects.create(external_id="123", name="Foo")
        self.season = Season.objects.create(show=self.show, season_number=1, name="Season 1")
        self.episode = Episode.objects.create(
            show=self.show,
            season=self.season,
            season_number=1,
            episode_number=1,
            name="Pilot",
            overview="The beginning.",
            air_date=date.today() - timedelta(days=1),
            runtime=45,
        )

    def tearDown(self):
        from django_vite.core.asset_loader import DjangoViteAssetLoader

        DjangoViteAssetLoader._instance = None

    def test_requires_auth(self):
        self.client.logout()
        response = self.client.get(f"/tv/123/episodes/{self.episode.id}/")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login/", response["Location"])

    def test_404_when_episode_does_not_belong_to_show(self):
        other_show = Show.objects.create(external_id="456", name="Bar")
        other_season = Season.objects.create(show=other_show, season_number=1, name="Season 1")
        other_episode = Episode.objects.create(
            show=other_show, season=other_season, season_number=1, episode_number=1, name="Other"
        )

        response = self.client.get(f"/tv/123/episodes/{other_episode.id}/")

        self.assertEqual(response.status_code, 404)

    def test_renders_read_only_when_not_tracking(self):
        response = self.client.get(f"/tv/123/episodes/{self.episode.id}/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Pilot")
        self.assertContains(response, "The beginning.")
        self.assertNotContains(response, "Mark watched")

    def test_renders_watched_fab_when_tracking(self):
        UserShow.objects.create(user=self.user, show=self.show, status=UserShow.Status.TRACKED)

        response = self.client.get(f"/tv/123/episodes/{self.episode.id}/")

        self.assertContains(response, "Mark watched")
        self.assertContains(response, 'class="fab"')
        self.assertContains(response, "fa-eye")

    def test_renders_unwatched_fab_for_watched_episode(self):
        UserShow.objects.create(user=self.user, show=self.show, status=UserShow.Status.TRACKED)
        UserEpisode.objects.create(user=self.user, episode=self.episode)

        response = self.client.get(f"/tv/123/episodes/{self.episode.id}/")

        self.assertContains(response, "Mark unwatched")
        self.assertContains(response, 'class="fab"')
        self.assertContains(response, "fa-eye-slash")

    def test_shows_finale_badge(self):
        self.episode.finale_type = "series"
        self.episode.save(update_fields=["finale_type"])

        response = self.client.get(f"/tv/123/episodes/{self.episode.id}/")

        self.assertContains(response, "Series Finale")

    def test_no_finale_badge_when_not_set(self):
        response = self.client.get(f"/tv/123/episodes/{self.episode.id}/")

        self.assertNotContains(response, "Finale")

    def test_previous_and_next_links_across_season_boundary(self):
        episode_two = Episode.objects.create(
            show=self.show, season=self.season, season_number=1, episode_number=2, name="Second"
        )
        season_two = Season.objects.create(show=self.show, season_number=2, name="Season 2")
        episode_three = Episode.objects.create(
            show=self.show, season=season_two, season_number=2, episode_number=1, name="Third"
        )

        response = self.client.get(f"/tv/123/episodes/{episode_two.id}/")

        self.assertContains(response, f"/tv/123/episodes/{self.episode.id}/\"")
        self.assertContains(response, f"/tv/123/episodes/{episode_three.id}/\"")

    def test_no_previous_link_on_series_first_episode(self):
        response = self.client.get(f"/tv/123/episodes/{self.episode.id}/")

        self.assertNotContains(response, "fa-chevron-left")

    def test_no_next_link_on_series_last_episode(self):
        response = self.client.get(f"/tv/123/episodes/{self.episode.id}/")

        self.assertNotContains(response, "fa-chevron-right")
