from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from apps.tv import services
from apps.tv.models import Episode, Season, Show, UserEpisode, UserShow


class WatchlistProgressColorTests(SimpleTestCase):
    def test_remaining_episodes_are_yellow(self):
        self.assertEqual(
            services.watchlist_progress_color(1, 2, "Ended"),
            "warning",
        )

    def test_finished_show_with_all_available_episodes_is_green(self):
        self.assertEqual(
            services.watchlist_progress_color(2, 2, "Ended"),
            "success",
        )

    def test_continuing_show_with_all_available_episodes_is_blue(self):
        self.assertEqual(
            services.watchlist_progress_color(2, 2, "Continuing"),
            "info",
        )


class WatchlistShowsServiceTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_user("user@example.com")
        self.other_user = user_model.objects.create_user("other@example.com")
        self.today = timezone.localdate()

    def make_show(self, name, external_id, status=UserShow.Status.TRACKED, user=None):
        show = Show.objects.create(name=name, external_id=external_id)
        season = Season.objects.create(show=show, season_number=1, name="Season 1")
        UserShow.objects.create(
            user=user or self.user,
            show=show,
            status=status,
        )
        return show, season

    def make_episode(self, show, season, number, air_date, season_number=None):
        if season_number is None:
            season_number = season.season_number
        return Episode.objects.create(
            show=show,
            season=season,
            season_number=season_number,
            episode_number=number,
            air_date=air_date,
            name=f"Episode {number}",
        )

    def watch(self, episode, user=None):
        return UserEpisode.objects.create(user=user or self.user, episode=episode)

    def test_all_and_status_sections_are_title_sorted(self):
        paused, _ = self.make_show("Bravo", "paused", UserShow.Status.PAUSED)
        tracked, _ = self.make_show("Charlie", "tracked")
        dropped, _ = self.make_show("Alpha", "dropped", UserShow.Status.DROPPED)
        self.make_show("Other user's show", "other", user=self.other_user)

        self.assertEqual(
            list(services.get_watchlist_shows(self.user, "all")),
            [dropped, paused, tracked],
        )
        self.assertEqual(list(services.get_watchlist_shows(self.user, "paused")), [paused])
        self.assertEqual(list(services.get_watchlist_shows(self.user, "dropped")), [dropped])

    def test_rejects_unknown_section(self):
        with self.assertRaises(ValueError):
            services.get_watchlist_shows(self.user, "unknown")

    def test_watching_and_completed_are_based_on_aired_numbered_episodes(self):
        watching, watching_season = self.make_show("Watching", "watching")
        watching_episode = self.make_episode(
            watching, watching_season, 1, self.today - timedelta(days=2)
        )
        self.make_episode(watching, watching_season, 2, self.today - timedelta(days=1))
        self.watch(watching_episode)

        completed, completed_season = self.make_show("Completed", "completed")
        completed_episode_one = self.make_episode(
            completed, completed_season, 1, self.today - timedelta(days=2)
        )
        completed_episode_two = self.make_episode(
            completed, completed_season, 2, self.today - timedelta(days=1)
        )
        self.watch(completed_episode_one)
        self.watch(completed_episode_two)

        unreleased, unreleased_season = self.make_show("Unreleased", "unreleased")
        self.make_episode(unreleased, unreleased_season, 1, self.today + timedelta(days=1))

        self.assertEqual(list(services.get_watchlist_shows(self.user, "watching")), [watching])
        self.assertEqual(list(services.get_watchlist_shows(self.user, "completed")), [completed])

    def test_special_future_and_undated_episodes_do_not_affect_completion(self):
        show, season = self.make_show("Completed", "completed")
        aired_episode = self.make_episode(
            show, season, 1, self.today - timedelta(days=1)
        )
        self.watch(aired_episode)

        specials = Season.objects.create(show=show, season_number=0, name="Specials")
        self.make_episode(show, specials, 1, self.today - timedelta(days=2), season_number=0)
        self.make_episode(show, season, 2, self.today + timedelta(days=1))
        self.make_episode(show, season, 3, None)

        self.assertEqual(list(services.get_watchlist_shows(self.user, "completed")), [show])
        self.assertEqual(list(services.get_watchlist_shows(self.user, "watching")), [])

    def test_watched_episodes_belong_to_the_current_user(self):
        show, season = self.make_show("Still Watching", "still-watching")
        episode = self.make_episode(show, season, 1, self.today - timedelta(days=1))
        self.watch(episode, self.other_user)

        self.assertEqual(list(services.get_watchlist_shows(self.user, "watching")), [show])
        self.assertEqual(list(services.get_watchlist_shows(self.user, "completed")), [])

    def test_paused_and_dropped_shows_do_not_enter_watching_or_completed(self):
        paused, paused_season = self.make_show("Paused", "paused", UserShow.Status.PAUSED)
        paused_episode = self.make_episode(
            paused, paused_season, 1, self.today - timedelta(days=1)
        )
        self.watch(paused_episode)

        dropped, dropped_season = self.make_show("Dropped", "dropped", UserShow.Status.DROPPED)
        dropped_episode = self.make_episode(
            dropped, dropped_season, 1, self.today - timedelta(days=1)
        )
        self.watch(dropped_episode)

        self.assertEqual(list(services.get_watchlist_shows(self.user, "paused")), [paused])
        self.assertEqual(list(services.get_watchlist_shows(self.user, "dropped")), [dropped])
        self.assertEqual(list(services.get_watchlist_shows(self.user, "watching")), [])
        self.assertEqual(list(services.get_watchlist_shows(self.user, "completed")), [])
