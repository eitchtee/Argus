from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase
from django.utils import timezone

from apps.tv.filters import WatchlistFilter
from apps.tv.models import Episode, Season, Show, UserEpisode, UserShow


class WatchlistFilterTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("user@example.com")
        self.other_user = get_user_model().objects.create_user("other@example.com")
        self.factory = RequestFactory()
        self.today = timezone.localdate()

    def make_show(
        self,
        name,
        external_id,
        *,
        condition=UserShow.Status.TRACKED,
        normalized_status=None,
        user=None,
    ):
        show = Show.objects.create(
            name=name,
            external_id=external_id,
            normalized_status=normalized_status,
        )
        season = Season.objects.create(show=show, season_number=1, name="Season 1")
        UserShow.objects.create(user=user or self.user, show=show, status=condition)
        return show, season

    def make_episode(self, show, season, number, air_date):
        return Episode.objects.create(
            show=show,
            season=season,
            season_number=1,
            episode_number=number,
            name=f"Episode {number}",
            air_date=air_date,
        )

    def filter(self, query_string=""):
        request = self.factory.get(f"/tv/watchlist/?{query_string}")
        request.user = self.user
        return WatchlistFilter(
            request.GET,
            queryset=Show.objects.filter(user_states__user=self.user),
            request=request,
        )

    def test_condition_defaults_to_watching(self):
        watching, season = self.make_show("Watching", "watching")
        self.make_episode(watching, season, 1, self.today - timedelta(days=1))
        self.make_show("Paused", "paused", condition=UserShow.Status.PAUSED)

        watchlist_filter = self.filter()

        self.assertEqual(watchlist_filter.selected_conditions, ["watching"])
        self.assertEqual(list(watchlist_filter.qs), [watching])

    def test_condition_and_normalized_status_filters_combine(self):
        matching, season = self.make_show(
            "Matching",
            "matching",
            normalized_status=Show.NormalizedStatus.CONTINUING,
        )
        self.make_episode(matching, season, 1, self.today - timedelta(days=1))
        other_status, season = self.make_show(
            "Other Status",
            "other-status",
            normalized_status=Show.NormalizedStatus.ENDED,
        )
        self.make_episode(other_status, season, 1, self.today - timedelta(days=1))
        self.make_show(
            "Other User",
            "other-user",
            normalized_status=Show.NormalizedStatus.CONTINUING,
            user=self.other_user,
        )

        watchlist_filter = self.filter("condition=watching&normalized_status=Continuing")

        self.assertEqual(list(watchlist_filter.qs), [matching])
        self.assertEqual(
            watchlist_filter.selected_normalized_statuses,
            [Show.NormalizedStatus.CONTINUING],
        )

    def test_multiple_condition_values_are_combined(self):
        watching, watching_season = self.make_show("Watching", "watching")
        self.make_episode(watching, watching_season, 1, self.today - timedelta(days=1))
        completed, completed_season = self.make_show("Completed", "completed")
        completed_episode = self.make_episode(
            completed, completed_season, 1, self.today - timedelta(days=1)
        )
        UserEpisode.objects.create(user=self.user, episode=completed_episode)

        watchlist_filter = self.filter("condition=watching&condition=completed")

        self.assertEqual(
            watchlist_filter.selected_conditions,
            ["watching", "completed"],
        )
        self.assertEqual(list(watchlist_filter.qs), [completed, watching])

    def test_multiple_normalized_status_values_are_combined(self):
        for name, external_id, normalized_status in (
            ("Continuing", "continuing", Show.NormalizedStatus.CONTINUING),
            ("Ended", "ended", Show.NormalizedStatus.ENDED),
        ):
            show, season = self.make_show(
                name,
                external_id,
                normalized_status=normalized_status,
            )
            self.make_episode(show, season, 1, self.today - timedelta(days=1))

        watchlist_filter = self.filter(
            "condition=watching&normalized_status=Continuing&normalized_status=Ended"
        )

        self.assertEqual(
            watchlist_filter.selected_normalized_statuses,
            [Show.NormalizedStatus.CONTINUING, Show.NormalizedStatus.ENDED],
        )
        self.assertEqual(
            list(watchlist_filter.qs.values_list("name", flat=True)),
            ["Continuing", "Ended"],
        )
