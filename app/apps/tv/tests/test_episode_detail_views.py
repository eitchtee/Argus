from datetime import date, timedelta
from unittest.mock import patch

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

    @patch("apps.tv.views.get_object_or_404")
    def test_page_shell_defers_episode_context(self, get_object_or_404_mock):
        get_object_or_404_mock.side_effect = [self.show, self.episode]

        response = self.client.get(
            f"/tv/123/episodes/{self.episode.id}/",
            HTTP_HX_REQUEST="true",
            HTTP_HX_BOOSTED="true",
        )

        get_object_or_404_mock.assert_not_called()
        self.assertContains(response, 'id="tv-episode-content"')
        self.assertContains(response, f'hx-get="/tv/123/episodes/{self.episode.id}/"')
        self.assertContains(response, 'hx-trigger="load"')
        self.assertNotContains(response, "Pilot")

    def test_404_when_episode_does_not_belong_to_show(self):
        other_show = Show.objects.create(external_id="456", name="Bar")
        other_season = Season.objects.create(show=other_show, season_number=1, name="Season 1")
        other_episode = Episode.objects.create(
            show=other_show, season=other_season, season_number=1, episode_number=1, name="Other"
        )

        response = self.client.get(
            f"/tv/123/episodes/{other_episode.id}/", HTTP_HX_REQUEST="true"
        )

        self.assertEqual(response.status_code, 404)

    def test_renders_read_only_when_not_tracking(self):
        response = self.client.get(
            f"/tv/123/episodes/{self.episode.id}/", HTTP_HX_REQUEST="true"
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Pilot")
        self.assertContains(response, "The beginning.")
        self.assertNotContains(response, "Mark watched")

    def test_renders_watched_action_card_when_tracking(self):
        UserShow.objects.create(user=self.user, show=self.show, status=UserShow.Status.TRACKED)

        response = self.client.get(
            f"/tv/123/episodes/{self.episode.id}/", HTTP_HX_REQUEST="true"
        )
        content = response.content.decode()

        self.assertContains(response, "Mark watched")
        self.assertContains(response, 'class="episode-media-stack"')
        self.assertContains(response, 'id="episode-watched-button" class="media-poster-actions"')
        self.assertContains(response, 'class="join media-action-join join-horizontal"')
        self.assertContains(
            response,
            'class="btn btn-sm join-item media-action-button btn-success"',
        )
        self.assertContains(response, 'data-tippy-content="Mark watched"')
        self.assertNotContains(response, 'class="fab"')
        self.assertLess(
            content.index('<div class="episode-media-stack">'),
            content.index('<div id="episode-watched-button" class="media-poster-actions"'),
        )
        self.assertContains(response, "fa-eye")

    def test_renders_unwatched_action_card_for_watched_episode(self):
        UserShow.objects.create(user=self.user, show=self.show, status=UserShow.Status.TRACKED)
        UserEpisode.objects.create(user=self.user, episode=self.episode)

        response = self.client.get(
            f"/tv/123/episodes/{self.episode.id}/", HTTP_HX_REQUEST="true"
        )

        self.assertContains(response, "Mark unwatched")
        self.assertContains(response, 'id="episode-watched-button" class="media-poster-actions"')
        self.assertContains(response, 'data-tippy-content="Mark unwatched"')
        self.assertNotContains(response, 'class="fab"')
        self.assertContains(response, "fa-eye-slash")

    def test_shows_finale_badge(self):
        self.episode.finale_type = "series"
        self.episode.save(update_fields=["finale_type"])

        response = self.client.get(
            f"/tv/123/episodes/{self.episode.id}/", HTTP_HX_REQUEST="true"
        )

        self.assertContains(response, 'class="episode-status episode-status--finale"')
        self.assertContains(response, "Series Finale")

    def test_shows_season_finale_label(self):
        self.episode.finale_type = "season"
        self.episode.save(update_fields=["finale_type"])

        response = self.client.get(
            f"/tv/123/episodes/{self.episode.id}/", HTTP_HX_REQUEST="true"
        )

        self.assertContains(response, 'class="episode-status episode-status--finale"')
        self.assertContains(response, "Season Finale")
        self.assertNotContains(response, "Series Finale")

    def test_no_finale_badge_when_not_set(self):
        response = self.client.get(
            f"/tv/123/episodes/{self.episode.id}/", HTTP_HX_REQUEST="true"
        )

        self.assertNotContains(response, "Finale")

    def test_uses_show_backdrop_as_episode_card_background(self):
        self.show.backdrop_path = "https://artworks.thetvdb.com/fanart.jpg"
        self.show.save(update_fields=["backdrop_path"])

        response = self.client.get(
            f"/tv/123/episodes/{self.episode.id}/", HTTP_HX_REQUEST="true"
        )

        self.assertContains(response, 'class="media-hero"')
        self.assertContains(
            response,
            "background-image: url('https://artworks.thetvdb.com/fanart.jpg');",
        )
        self.assertContains(response, 'class="absolute inset-0 bg-base-100/85"')

    def test_previous_and_next_links_across_season_boundary(self):
        episode_two = Episode.objects.create(
            show=self.show, season=self.season, season_number=1, episode_number=2, name="Second"
        )
        season_two = Season.objects.create(show=self.show, season_number=2, name="Season 2")
        episode_three = Episode.objects.create(
            show=self.show, season=season_two, season_number=2, episode_number=1, name="Third"
        )

        response = self.client.get(
            f"/tv/123/episodes/{episode_two.id}/", HTTP_HX_REQUEST="true"
        )

        self.assertContains(response, f"/tv/123/episodes/{self.episode.id}/\"")
        self.assertContains(response, f"/tv/123/episodes/{episode_three.id}/\"")

    def test_previous_button_is_disabled_on_series_first_episode(self):
        response = self.client.get(
            f"/tv/123/episodes/{self.episode.id}/", HTTP_HX_REQUEST="true"
        )

        self.assertContains(response, '<button type="button" class="detail-link" disabled')
        self.assertContains(response, 'aria-label="Previous"')
        self.assertContains(response, "fa-chevron-left")

    def test_next_button_is_disabled_on_series_last_episode(self):
        response = self.client.get(
            f"/tv/123/episodes/{self.episode.id}/", HTTP_HX_REQUEST="true"
        )

        self.assertContains(response, '<button type="button" class="detail-link" disabled')
        self.assertContains(response, 'aria-label="Next"')
        self.assertContains(response, "fa-chevron-right")
