from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from apps.tv import services
from apps.tv.models import Episode, Season, Show, UserEpisode, UserShow, UserSkippedEpisode


class SkippedEpisodeTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("user@example.com", password="password")
        self.client.login(username="user@example.com", password="password")
        self.show = Show.objects.create(external_id="123", name="Late Show")
        UserShow.objects.create(user=self.user, show=self.show)
        today = timezone.localdate()
        self.season1 = Season.objects.create(show=self.show, season_number=1, name="Season 1")
        self.season2 = Season.objects.create(show=self.show, season_number=2, name="Season 2")
        self.s1 = [self.episode(self.season1, n, today - timedelta(days=100 - n)) for n in (1, 2)]
        self.s2 = [self.episode(self.season2, n, today - timedelta(days=10 - n)) for n in (1, 2)]
        self.future = self.episode(self.season2, 3, today + timedelta(days=3))

    def episode(self, season, number, air_date):
        return Episode.objects.create(
            show=self.show,
            season=season,
            season_number=season.season_number,
            episode_number=number,
            air_date=air_date,
        )

    def skip(self, *episodes):
        services.skip_episodes(
            self.user, self.show, Episode.objects.filter(id__in=[e.id for e in episodes])
        )

    def test_up_next_jumps_over_skipped_season(self):
        self.skip(*self.s1)

        [entry] = services.get_watchlist(self.user)
        self.assertEqual(entry.next_episode, self.s2[0])
        self.assertEqual(entry.pending_count, 1)
        self.assertEqual(services.get_watchlist_entry(self.user, self.show).next_episode, self.s2[0])

    def test_show_completes_when_rest_is_watched_or_skipped(self):
        self.skip(self.s1[0], self.future)
        for episode in (self.s1[1], *self.s2):
            UserEpisode.objects.create(user=self.user, episode=episode)

        [show] = services.get_watchlist_shows(self.user, "completed")
        self.assertEqual((show.watched_episode_count, show.total_episode_count), (3, 3))
        self.assertEqual(services.get_watchlist(self.user), [])

    def test_skipped_future_episode_is_not_upcoming(self):
        self.assertIn(
            self.future, [entry.episode for entry in services.get_upcoming_episodes(self.user)]
        )
        self.skip(self.future)

        self.assertNotIn(
            self.future, [entry.episode for entry in services.get_upcoming_episodes(self.user)]
        )

    def test_skip_leaves_watched_episodes_alone_and_watching_clears_skip(self):
        UserEpisode.objects.create(user=self.user, episode=self.s1[0])
        self.skip(*self.s1)
        self.assertEqual(
            list(UserSkippedEpisode.objects.values_list("episode_id", flat=True)), [self.s1[1].id]
        )

        services.mark_episode_watched(self.user, self.s1[1])
        self.assertFalse(UserSkippedEpisode.objects.exists())

    def test_marking_season_watched_keeps_skipped_episodes_unwatched(self):
        self.skip(self.s2[0])
        services.mark_season_watched(self.user, self.season2)

        self.assertEqual(
            list(UserEpisode.objects.values_list("episode_id", flat=True)), [self.s2[1].id]
        )

    def test_season_skip_view_round_trip(self):
        url = f"/tv/123/seasons/{self.season1.id}/skipped/"

        response = self.client.post(url, HTTP_HX_REQUEST="true")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(UserSkippedEpisode.objects.count(), 2)
        self.assertContains(response, f'hx-delete="{url}"')

        self.client.delete(url, HTTP_HX_REQUEST="true")
        self.assertFalse(UserSkippedEpisode.objects.exists())

    def test_episode_detail_skip_view_round_trip(self):
        url = f"/tv/123/episodes/{self.s1[0].id}/detail-skipped/"

        response = self.client.post(url, HTTP_HX_REQUEST="true")
        self.assertContains(response, f'hx-delete="{url}"')
        self.assertTrue(UserSkippedEpisode.objects.filter(episode=self.s1[0]).exists())

        response = self.client.delete(url, HTTP_HX_REQUEST="true")
        self.assertContains(response, f'hx-post="{url}"')
        self.assertFalse(UserSkippedEpisode.objects.exists())

    def test_skipping_requires_tracking(self):
        UserShow.objects.filter(user=self.user).update(status=UserShow.Status.DROPPED)

        response = self.client.post(
            f"/tv/123/episodes/{self.s1[0].id}/skipped/", HTTP_HX_REQUEST="true"
        )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(UserSkippedEpisode.objects.exists())
