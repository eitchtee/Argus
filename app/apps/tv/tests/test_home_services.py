from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from cachalot.api import cachalot_disabled

from apps.tv.models import Episode, Season, Show, UserEpisode, UserShow
from apps.tv.services import countdown_label, get_upcoming_episodes, get_watchlist, get_watchlist_entry


class GetWatchlistServiceTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("user@example.com")
        self.today = timezone.localdate()

    def _make_show(self, name, external_id):
        show = Show.objects.create(external_id=external_id, name=name)
        UserShow.objects.create(user=self.user, show=show, status=UserShow.Status.TRACKED)
        return show

    def _make_episode(self, show, season_number, episode_number, air_date, name="Ep"):
        with cachalot_disabled():
            season, _ = Season.objects.get_or_create(
                show=show, season_number=season_number, defaults={"name": f"Season {season_number}"}
            )
        return Episode.objects.create(
            show=show,
            season=season,
            season_number=season_number,
            episode_number=episode_number,
            name=name,
            air_date=air_date,
        )

    def test_omits_shows_with_no_pending_aired_episodes(self):
        show = self._make_show("Fully Watched", "1")
        episode = self._make_episode(show, 1, 1, self.today - timedelta(days=1))
        UserEpisode.objects.create(user=self.user, episode=episode)

        self.assertEqual(get_watchlist(self.user), [])

    def test_omits_untracked_shows(self):
        show = Show.objects.create(external_id="1", name="Untracked")
        self._make_episode(show, 1, 1, self.today - timedelta(days=1))

        self.assertEqual(get_watchlist(self.user), [])

    def test_picks_earliest_unwatched_aired_episode_and_counts_the_rest(self):
        show = self._make_show("My Show", "1")
        self._make_episode(show, 1, 1, self.today - timedelta(days=10), name="First")
        second = self._make_episode(show, 1, 2, self.today - timedelta(days=5), name="Second")
        self._make_episode(show, 1, 3, self.today - timedelta(days=1), name="Third")
        self._make_episode(show, 1, 4, self.today + timedelta(days=5), name="Unaired")
        UserEpisode.objects.create(user=self.user, episode=Episode.objects.get(name="First"))

        entries = get_watchlist(self.user)

        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].show, show)
        self.assertEqual(entries[0].next_episode, second)
        self.assertEqual(entries[0].pending_count, 1)

    def test_excludes_specials(self):
        show = self._make_show("My Show", "1")
        self._make_episode(show, 0, 1, self.today - timedelta(days=1), name="Special")

        self.assertEqual(get_watchlist(self.user), [])

    def test_sorts_by_next_episode_air_date_descending(self):
        older_show = self._make_show("Older", "1")
        newer_show = self._make_show("Newer", "2")
        self._make_episode(older_show, 1, 1, self.today - timedelta(days=10))
        self._make_episode(newer_show, 1, 1, self.today - timedelta(days=1))

        entries = get_watchlist(self.user)

        self.assertEqual([entry.show for entry in entries], [newer_show, older_show])

    def test_does_not_leak_other_users_watched_state(self):
        other_user = get_user_model().objects.create_user("other@example.com")
        show = self._make_show("Shared Show", "1")
        UserShow.objects.create(user=other_user, show=show, status=UserShow.Status.TRACKED)
        episode = self._make_episode(show, 1, 1, self.today - timedelta(days=1))
        UserEpisode.objects.create(user=other_user, episode=episode)

        entries = get_watchlist(self.user)

        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].next_episode, episode)


class GetWatchlistEntryServiceTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("user@example.com")
        self.today = timezone.localdate()
        self.show = Show.objects.create(external_id="1", name="My Show")
        self.season = Season.objects.create(show=self.show, season_number=1, name="Season 1")
        UserShow.objects.create(user=self.user, show=self.show, status=UserShow.Status.TRACKED)

    def test_returns_none_when_no_pending_episodes(self):
        self.assertIsNone(get_watchlist_entry(self.user, self.show))

    def test_returns_next_pending_episode(self):
        episode = Episode.objects.create(
            show=self.show,
            season=self.season,
            season_number=1,
            episode_number=1,
            name="Pilot",
            air_date=self.today - timedelta(days=1),
        )

        entry = get_watchlist_entry(self.user, self.show)

        self.assertEqual(entry.next_episode, episode)
        self.assertEqual(entry.pending_count, 0)


class CountdownLabelServiceTests(TestCase):
    def setUp(self):
        self.today = date(2026, 7, 5)

    def test_yesterday(self):
        self.assertEqual(countdown_label(self.today - timedelta(days=1), self.today), "Yesterday")

    def test_today(self):
        self.assertEqual(countdown_label(self.today, self.today), "Today")

    def test_tomorrow(self):
        self.assertEqual(countdown_label(self.today + timedelta(days=1), self.today), "Tomorrow")

    def test_further_out(self):
        self.assertEqual(countdown_label(self.today + timedelta(days=300), self.today), "300 days")


class GetUpcomingEpisodesServiceTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("user@example.com")
        self.today = timezone.localdate()
        self.show = Show.objects.create(external_id="1", name="My Show")
        self.season = Season.objects.create(show=self.show, season_number=1, name="Season 1")
        UserShow.objects.create(user=self.user, show=self.show, status=UserShow.Status.TRACKED)

    def _make_episode(self, episode_number, air_date, name="Ep"):
        return Episode.objects.create(
            show=self.show,
            season=self.season,
            season_number=1,
            episode_number=episode_number,
            name=name,
            air_date=air_date,
        )

    def test_excludes_episodes_older_than_yesterday(self):
        self._make_episode(1, self.today - timedelta(days=2), name="Too old")
        yesterday_ep = self._make_episode(2, self.today - timedelta(days=1), name="Yesterday")

        entries = get_upcoming_episodes(self.user)

        self.assertEqual([entry.episode for entry in entries], [yesterday_ep])

    def test_excludes_specials(self):
        specials = Season.objects.create(show=self.show, season_number=0, name="Specials")
        Episode.objects.create(
            show=self.show,
            season=specials,
            season_number=0,
            episode_number=1,
            name="Special",
            air_date=self.today,
        )

        self.assertEqual(get_upcoming_episodes(self.user), [])

    def test_orders_by_air_date_ascending(self):
        later = self._make_episode(1, self.today + timedelta(days=10), name="Later")
        sooner = self._make_episode(2, self.today + timedelta(days=1), name="Sooner")

        entries = get_upcoming_episodes(self.user)

        self.assertEqual([entry.episode for entry in entries], [sooner, later])

    def test_returns_at_most_count_soonest_first(self):
        for i in range(15):
            self._make_episode(i, self.today + timedelta(days=i))

        entries = get_upcoming_episodes(self.user, count=10)

        self.assertEqual(len(entries), 10)
        self.assertEqual(entries[0].episode.episode_number, 0)
        self.assertEqual(entries[-1].episode.episode_number, 9)

    def test_reports_watched_state_per_episode(self):
        watched = self._make_episode(1, self.today, name="Watched")
        unwatched = self._make_episode(2, self.today, name="Unwatched")
        UserEpisode.objects.create(user=self.user, episode=watched)

        entries = {entry.episode: entry.watched for entry in get_upcoming_episodes(self.user)}

        self.assertTrue(entries[watched])
        self.assertFalse(entries[unwatched])
