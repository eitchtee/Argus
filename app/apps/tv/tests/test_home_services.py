from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from apps.tv import services
from apps.tv.models import Episode, Season, Show, UserEpisode, UserShow
from apps.tv.services import (
    countdown_label,
    get_upcoming_episodes,
    get_watchlist,
    get_watchlist_entry,
)


class GetWatchlistServiceTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("user@example.com")
        self.today = timezone.localdate()

    def _make_show(self, name, external_id):
        show = Show.objects.create(external_id=external_id, name=name)
        UserShow.objects.create(user=self.user, show=show, status=UserShow.Status.TRACKED)
        return show

    def _make_episode(self, show, season_number, episode_number, air_date, name="Ep"):
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

    def test_watchlist_entries_can_access_episode_shows_without_n_plus_one_queries(self):
        first_show = self._make_show("First", "1")
        second_show = self._make_show("Second", "2")
        self._make_episode(first_show, 1, 1, self.today - timedelta(days=2))
        self._make_episode(second_show, 1, 1, self.today - timedelta(days=1))

        with self.assertNumQueries(4):
            entries = get_watchlist(self.user)
            [entry.next_episode.show.name for entry in entries]


class GetUpNextServiceTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("user@example.com")
        self.today = timezone.localdate()

    def _make_show(self, name, external_id):
        show = Show.objects.create(external_id=external_id, name=name)
        UserShow.objects.create(
            user=self.user,
            show=show,
            status=UserShow.Status.TRACKED,
        )
        season = Season.objects.create(show=show, season_number=1, name="Season 1")
        return show, season

    def _make_episode(self, show, season, number, air_date, name):
        return Episode.objects.create(
            show=show,
            season=season,
            season_number=1,
            episode_number=number,
            air_date=air_date,
            name=name,
        )

    def _get_up_next(self):
        get_up_next = getattr(services, "get_up_next", None)
        self.assertIsNotNone(get_up_next)
        return get_up_next(self.user)

    def test_returns_pending_entries_in_newest_first_order(self):
        older, older_season = self._make_show("Older", "older")
        newer, newer_season = self._make_show("Newer", "newer")
        self._make_episode(
            older, older_season, 1, self.today - timedelta(days=10), "Older pending"
        )
        newer_episode = self._make_episode(
            newer, newer_season, 1, self.today - timedelta(days=1), "Newer pending"
        )

        sections = self._get_up_next()

        self.assertEqual([entry.show for entry in sections.not_started], [newer, older])
        self.assertEqual(sections.not_started[0].next_episode, newer_episode)
        self.assertEqual(sections.active, [])
        self.assertEqual(sections.not_seen_in_a_while, [])

    def test_classifies_shows_by_current_users_seen_at(self):
        not_started, not_started_season = self._make_show("Not started", "not-started")
        stale, stale_season = self._make_show("Stale", "stale")
        active, active_season = self._make_show("Active", "active")

        self._make_episode(
            not_started, not_started_season, 1, self.today - timedelta(days=1), "Pilot"
        )
        stale_watched = self._make_episode(
            stale, stale_season, 1, self.today - timedelta(days=60), "Old watched"
        )
        self._make_episode(stale, stale_season, 2, self.today - timedelta(days=1), "Stale pending")
        active_watched = self._make_episode(
            active, active_season, 1, self.today - timedelta(days=10), "Recent watched"
        )
        self._make_episode(active, active_season, 2, self.today - timedelta(days=1), "Active pending")
        now = timezone.now()
        UserEpisode.objects.create(
            user=self.user,
            episode=stale_watched,
            seen_at=now - timedelta(days=31),
        )
        UserEpisode.objects.create(
            user=self.user,
            episode=active_watched,
            seen_at=now - timedelta(days=29),
        )

        sections = self._get_up_next()

        self.assertEqual([entry.show for entry in sections.active], [active])
        self.assertEqual([entry.show for entry in sections.not_seen_in_a_while], [stale])
        self.assertEqual([entry.show for entry in sections.not_started], [not_started])

    def test_uses_strict_thirty_day_seen_at_cutoff(self):
        boundary_active, boundary_active_season = self._make_show("Boundary active", "active")
        boundary_stale, boundary_stale_season = self._make_show("Boundary stale", "stale")
        active_episode = self._make_episode(
            boundary_active, boundary_active_season, 1, self.today - timedelta(days=2), "Active"
        )
        self._make_episode(
            boundary_active,
            boundary_active_season,
            2,
            self.today - timedelta(days=1),
            "Active pending",
        )
        stale_episode = self._make_episode(
            boundary_stale, boundary_stale_season, 1, self.today - timedelta(days=1), "Stale"
        )
        self._make_episode(
            boundary_stale,
            boundary_stale_season,
            2,
            self.today,
            "Stale pending",
        )
        now = timezone.now()
        UserEpisode.objects.create(
            user=self.user,
            episode=active_episode,
            seen_at=now - timedelta(days=30) + timedelta(seconds=5),
        )
        UserEpisode.objects.create(
            user=self.user,
            episode=stale_episode,
            seen_at=now - timedelta(days=30) - timedelta(seconds=5),
        )

        sections = self._get_up_next()

        self.assertEqual([entry.show for entry in sections.active], [boundary_active])
        self.assertEqual([entry.show for entry in sections.not_seen_in_a_while], [boundary_stale])

    def test_omits_shows_without_pending_episodes(self):
        show, season = self._make_show("Finished", "finished")
        episode = self._make_episode(
            show, season, 1, self.today - timedelta(days=1), "Finished episode"
        )
        UserEpisode.objects.create(user=self.user, episode=episode)

        sections = self._get_up_next()

        self.assertEqual(sections.active, [])
        self.assertEqual(sections.not_seen_in_a_while, [])
        self.assertEqual(sections.not_started, [])

    def test_does_not_use_another_users_seen_state(self):
        other_user = get_user_model().objects.create_user("other@example.com")
        show, season = self._make_show("Shared", "shared")
        episode = self._make_episode(
            show, season, 1, self.today - timedelta(days=1), "Shared episode"
        )
        UserEpisode.objects.create(
            user=other_user,
            episode=episode,
            seen_at=timezone.now() - timedelta(days=60),
        )

        sections = self._get_up_next()

        self.assertEqual([entry.show for entry in sections.not_started], [show])
        self.assertEqual(sections.active, [])
        self.assertEqual(sections.not_seen_in_a_while, [])


class UpcomingMonthServiceTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("user@example.com")
        self.today = timezone.localdate()

    def _make_show(self, name, external_id):
        show = Show.objects.create(external_id=external_id, name=name)
        UserShow.objects.create(user=self.user, show=show, status=UserShow.Status.TRACKED)
        season = Season.objects.create(show=show, season_number=1, name="Season 1")
        return show, season

    def _make_episode(self, show, season, number, air_date, name):
        return Episode.objects.create(
            show=show,
            season=season,
            season_number=1,
            episode_number=number,
            air_date=air_date,
            name=name,
        )

    def _get_upcoming_month(self, after_month=None):
        get_upcoming_month = getattr(services, "get_upcoming_month", None)
        self.assertIsNotNone(get_upcoming_month)
        return get_upcoming_month(self.user, after_month=after_month)

    def _next_month_start(self, month_start):
        if month_start.month == 12:
            return month_start.replace(year=month_start.year + 1, month=1)
        return month_start.replace(month=month_start.month + 1)

    def test_returns_all_episodes_in_month_with_current_users_watched_state(self):
        show, season = self._make_show("My Show", "my-show")
        first = self._make_episode(show, season, 1, self.today, "Today")
        second = self._make_episode(
            show, season, 2, self.today + timedelta(days=5), "Later"
        )
        other_user = get_user_model().objects.create_user("other@example.com")
        UserEpisode.objects.create(user=self.user, episode=first)
        UserEpisode.objects.create(user=other_user, episode=second)

        month = self._get_upcoming_month()

        self.assertEqual(month.month_start, self.today.replace(day=1))
        self.assertEqual([entry.episode.name for entry in month.entries], ["Today", "Later"])
        self.assertTrue(month.entries[0].watched)
        self.assertFalse(month.entries[1].watched)

    def test_uses_the_shared_yesterday_onward_window(self):
        show, season = self._make_show("My Show", "my-show")
        self._make_episode(show, season, 1, self.today - timedelta(days=2), "Too old")
        self._make_episode(show, season, 2, self.today - timedelta(days=1), "Yesterday")
        self._make_episode(show, season, 3, self.today, "Today")
        self._make_episode(show, season, 4, self.today + timedelta(days=1), "Tomorrow")

        month = self._get_upcoming_month()

        self.assertEqual(
            [entry.episode.name for entry in month.entries],
            ["Yesterday", "Today", "Tomorrow"],
        )

    def test_advances_to_next_available_month_and_skips_empty_months(self):
        show, season = self._make_show("My Show", "my-show")
        current_month = self.today.replace(day=1)
        empty_month = self._next_month_start(current_month)
        next_month = self._next_month_start(empty_month)
        self._make_episode(show, season, 1, self.today, "Current month")
        self._make_episode(show, season, 2, next_month, "Next available month")

        first = self._get_upcoming_month()
        second = self._get_upcoming_month(after_month=first.month_start)

        self.assertEqual(first.next_cursor, current_month)
        self.assertEqual(second.month_start, next_month)
        self.assertEqual([entry.episode.name for entry in second.entries], ["Next available month"])

    def test_returns_none_after_last_available_month(self):
        show, season = self._make_show("My Show", "my-show")
        self._make_episode(show, season, 1, self.today, "Only month")

        month = self._get_upcoming_month()

        self.assertIsNone(month.next_cursor)
        self.assertIsNone(self._get_upcoming_month(after_month=month.month_start))


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
