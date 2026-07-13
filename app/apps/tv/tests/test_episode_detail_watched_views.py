from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.tv.models import Episode, Season, Show, UserEpisode, UserShow


class EpisodeDetailWatchedViewTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("user@example.com", password="password")
        self.client.login(username="user@example.com", password="password")
        self.show = Show.objects.create(external_id="123", name="Foo")
        self.season = Season.objects.create(show=self.show, season_number=1, name="Season 1")
        self.episode = Episode.objects.create(
            show=self.show, season=self.season, season_number=1, episode_number=1, name="Pilot"
        )
        UserShow.objects.create(user=self.user, show=self.show, status=UserShow.Status.TRACKED)

    def test_requires_htmx_header(self):
        response = self.client.post(f"/tv/123/episodes/{self.episode.id}/detail-watched/")
        self.assertEqual(response.status_code, 403)

    def test_demo_mode_blocks_non_superusers(self):
        with self.settings(DEMO=True):
            response = self.client.post(
                f"/tv/123/episodes/{self.episode.id}/detail-watched/", HTTP_HX_REQUEST="true"
            )
        self.assertEqual(response.status_code, 403)
        self.assertFalse(UserEpisode.objects.filter(user=self.user, episode=self.episode).exists())

    def test_marking_watched_swaps_button_to_unmark(self):
        response = self.client.post(
            f"/tv/123/episodes/{self.episode.id}/detail-watched/", HTTP_HX_REQUEST="true"
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Mark unwatched")
        self.assertContains(response, 'class="fab"')
        self.assertContains(response, "fa-eye-slash")
        self.assertTrue(UserEpisode.objects.filter(user=self.user, episode=self.episode).exists())

    def test_unmarking_watched_swaps_button_to_mark(self):
        UserEpisode.objects.create(user=self.user, episode=self.episode)

        response = self.client.delete(
            f"/tv/123/episodes/{self.episode.id}/detail-watched/", HTTP_HX_REQUEST="true"
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Mark watched")
        self.assertContains(response, 'class="fab"')
        self.assertContains(response, "fa-eye")
        self.assertFalse(UserEpisode.objects.filter(user=self.user, episode=self.episode).exists())

    def test_requires_tracking(self):
        UserShow.objects.filter(user=self.user, show=self.show).update(status=UserShow.Status.PAUSED)

        response = self.client.post(
            f"/tv/123/episodes/{self.episode.id}/detail-watched/", HTTP_HX_REQUEST="true"
        )

        self.assertEqual(response.status_code, 400)
