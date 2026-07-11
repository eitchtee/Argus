from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from apps.tv.models import Episode, Season, Show, UserEpisode, UserShow


class HomeWatchlistViewTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("user@example.com", password="password")
        self.client.login(username="user@example.com", password="password")
        self.today = timezone.localdate()

    def test_requires_htmx_header(self):
        response = self.client.get("/tv/home/watchlist/")
        self.assertEqual(response.status_code, 403)

    def test_shows_empty_state_when_nothing_pending(self):
        response = self.client.get("/tv/home/watchlist/", HTTP_HX_REQUEST="true")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "You're all caught up!")

    def test_shows_row_for_show_with_pending_episode(self):
        show = Show.objects.create(external_id="1", name="My Show")
        season = Season.objects.create(show=show, season_number=1, name="Season 1")
        Episode.objects.create(
            show=show,
            season=season,
            season_number=1,
            episode_number=1,
            name="Pilot",
            air_date=self.today - timedelta(days=1),
        )
        UserShow.objects.create(user=self.user, show=show, status=UserShow.Status.TRACKED)

        response = self.client.get("/tv/home/watchlist/", HTTP_HX_REQUEST="true")

        self.assertContains(response, "My Show")
        self.assertContains(response, "Pilot")
        episode = Episode.objects.get(name="Pilot")
        self.assertContains(response, f"/tv/1/episodes/{episode.id}/\"")
        self.assertContains(response, "checkbox-lg")
        self.assertNotContains(response, "Mark watched")

    def test_uses_show_poster_image(self):
        show = Show.objects.create(
            external_id="1",
            name="My Show",
            poster_path="https://example.com/poster.jpg",
        )
        season = Season.objects.create(show=show, season_number=1, name="Season 1")
        Episode.objects.create(
            show=show,
            season=season,
            season_number=1,
            episode_number=1,
            name="Pilot",
            air_date=self.today - timedelta(days=1),
            still_path="https://example.com/still.jpg",
        )
        UserShow.objects.create(user=self.user, show=show, status=UserShow.Status.TRACKED)

        response = self.client.get("/tv/home/watchlist/", HTTP_HX_REQUEST="true")

        self.assertContains(response, "https://example.com/poster.jpg")

    def test_falls_back_to_show_poster_when_no_still_image(self):
        show = Show.objects.create(
            external_id="1", name="My Show", poster_path="https://example.com/poster.jpg"
        )
        season = Season.objects.create(show=show, season_number=1, name="Season 1")
        Episode.objects.create(
            show=show,
            season=season,
            season_number=1,
            episode_number=1,
            name="Pilot",
            air_date=self.today - timedelta(days=1),
            still_path=None,
        )
        UserShow.objects.create(user=self.user, show=show, status=UserShow.Status.TRACKED)

        response = self.client.get("/tv/home/watchlist/", HTTP_HX_REQUEST="true")

        self.assertContains(response, "https://example.com/poster.jpg")


class HomeWatchlistEpisodeWatchedViewTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("user@example.com", password="password")
        self.client.login(username="user@example.com", password="password")
        self.today = timezone.localdate()
        self.show = Show.objects.create(external_id="1", name="My Show")
        self.season = Season.objects.create(show=self.show, season_number=1, name="Season 1")
        UserShow.objects.create(user=self.user, show=self.show, status=UserShow.Status.TRACKED)

    def test_requires_htmx_header(self):
        episode = Episode.objects.create(
            show=self.show, season=self.season, season_number=1, episode_number=1,
            name="Pilot", air_date=self.today - timedelta(days=1),
        )
        response = self.client.post(f"/tv/home/watchlist/episodes/{episode.id}/watched/")
        self.assertEqual(response.status_code, 403)

    def test_demo_mode_blocks_non_superusers(self):
        episode = Episode.objects.create(
            show=self.show, season=self.season, season_number=1, episode_number=1,
            name="Pilot", air_date=self.today - timedelta(days=1),
        )
        with self.settings(DEMO=True):
            response = self.client.post(
                f"/tv/home/watchlist/episodes/{episode.id}/watched/", HTTP_HX_REQUEST="true"
            )
        self.assertEqual(response.status_code, 403)
        self.assertFalse(UserEpisode.objects.filter(user=self.user, episode=episode).exists())

    def test_marking_watched_with_more_pending_renders_next_episode_row(self):
        first = Episode.objects.create(
            show=self.show, season=self.season, season_number=1, episode_number=1,
            name="First", air_date=self.today - timedelta(days=2),
        )
        Episode.objects.create(
            show=self.show, season=self.season, season_number=1, episode_number=2,
            name="Second", air_date=self.today - timedelta(days=1),
        )

        response = self.client.post(
            f"/tv/home/watchlist/episodes/{first.id}/watched/", HTTP_HX_REQUEST="true"
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Second")
        self.assertNotContains(response, "First")
        self.assertTrue(UserEpisode.objects.filter(user=self.user, episode=first).exists())

    def test_marking_watched_with_nothing_left_removes_the_row(self):
        only_episode = Episode.objects.create(
            show=self.show, season=self.season, season_number=1, episode_number=1,
            name="Pilot", air_date=self.today - timedelta(days=1),
        )

        response = self.client.post(
            f"/tv/home/watchlist/episodes/{only_episode.id}/watched/", HTTP_HX_REQUEST="true"
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"")


class HomeUpcomingViewTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("user@example.com", password="password")
        self.client.login(username="user@example.com", password="password")
        self.today = timezone.localdate()
        self.show = Show.objects.create(external_id="1", name="My Show")
        self.season = Season.objects.create(show=self.show, season_number=1, name="Season 1")
        UserShow.objects.create(user=self.user, show=self.show, status=UserShow.Status.TRACKED)

    def test_requires_htmx_header(self):
        response = self.client.get("/tv/home/upcoming/")
        self.assertEqual(response.status_code, 403)

    def test_shows_empty_state_when_nothing_upcoming(self):
        response = self.client.get("/tv/home/upcoming/", HTTP_HX_REQUEST="true")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "No upcoming episodes.")

    def test_shows_episode_with_countdown(self):
        self.show.poster_path = "https://example.com/poster.jpg"
        self.show.save(update_fields=["poster_path"])
        Episode.objects.create(
            show=self.show, season=self.season, season_number=1, episode_number=1,
            name="Pilot", air_date=self.today,
        )

        response = self.client.get("/tv/home/upcoming/", HTTP_HX_REQUEST="true")

        self.assertContains(response, "Pilot")
        self.assertContains(response, "Today")
        self.assertContains(response, "https://example.com/poster.jpg")
        self.assertNotContains(response, "checkbox-lg")
        episode = Episode.objects.get(name="Pilot")
        self.assertContains(response, f"/tv/1/episodes/{episode.id}/\"")

    def test_caps_at_ten_episodes(self):
        for i in range(15):
            Episode.objects.create(
                show=self.show, season=self.season, season_number=1, episode_number=i,
                name=f"Ep {i}", air_date=self.today + timedelta(days=i),
            )

        response = self.client.get("/tv/home/upcoming/", HTTP_HX_REQUEST="true")

        self.assertNotContains(response, "hx-trigger=\"revealed\"")
        self.assertEqual(response.content.decode().count('id="upcoming-episode-'), 10)
