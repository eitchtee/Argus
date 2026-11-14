from datetime import timedelta

from django.contrib.auth import get_user_model
from django.http import QueryDict
from django.test import TestCase
from django.utils import timezone

from apps.catalog.models import Genre
from apps.tv.filters import filter_watchlist
from apps.tv.models import Episode, Season, Show, UserEpisode, UserShow


class FilterWatchlistTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("user@example.com")
        self.today = timezone.localdate()
        self.drama = Genre.objects.create(provider="tvdb", external_id="1", name="Drama")

    def make_show(
        self,
        name,
        *,
        condition=UserShow.Status.TRACKED,
        normalized_status=Show.NormalizedStatus.CONTINUING,
        genres=(),
        watched=False,
    ):
        show = Show.objects.create(
            name=name, external_id=name, normalized_status=normalized_status
        )
        show.genres.set(genres)
        season = Season.objects.create(show=show, season_number=1, name="Season 1")
        episode = Episode.objects.create(
            show=show,
            season=season,
            season_number=1,
            episode_number=1,
            name="Pilot",
            air_date=self.today - timedelta(days=1),
        )
        UserShow.objects.create(user=self.user, show=show, status=condition)
        if watched:
            UserEpisode.objects.create(user=self.user, episode=episode)
        return show

    def run_filter(self, query_string=""):
        return filter_watchlist(self.user, QueryDict(query_string))

    def names(self, context):
        return [show.name for show in context["shows"]]

    def counts(self, context):
        return {value: count for value, _label, count in context["conditions"]}

    def test_defaults_to_watching_and_counts_every_tab(self):
        self.make_show("Watching")
        self.make_show("Completed", watched=True)
        self.make_show("Paused", condition=UserShow.Status.PAUSED)

        context = self.run_filter()

        self.assertEqual(self.names(context), ["Watching"])
        self.assertEqual(
            self.counts(context),
            {"watching": 1, "completed": 1, "paused": 1, "dropped": 0, "all": 3},
        )

    def test_genre_and_status_narrow_results_and_tab_counts(self):
        self.make_show("Match", genres=[self.drama])
        self.make_show("Wrong genre")
        self.make_show(
            "Wrong status",
            genres=[self.drama],
            normalized_status=Show.NormalizedStatus.ENDED,
        )
        self.make_show("Paused match", genres=[self.drama], condition=UserShow.Status.PAUSED)

        context = self.run_filter("condition=all&genre=Drama&status=Continuing")

        self.assertEqual(self.names(context), ["Match", "Paused match"])
        self.assertEqual(self.counts(context)["watching"], 1)
        self.assertEqual(self.counts(context)["paused"], 1)

    def test_unknown_values_fall_back_to_defaults(self):
        self.make_show("Watching")

        context = self.run_filter("condition=bogus&genre=Nope&status=Nope&sort=bogus")

        self.assertEqual(
            (context["condition"], context["genre"], context["status"], context["sort"]),
            ("watching", "", "", "title"),
        )
        self.assertEqual(self.names(context), ["Watching"])

    def test_recently_watched_sort_puts_unwatched_shows_last(self):
        self.make_show("Never watched")
        older = self.make_show("Older")
        newer = self.make_show("Newer")
        UserEpisode.objects.create(
            user=self.user,
            episode=Episode.objects.create(
                show=older, season=older.seasons.get(), season_number=1,
                episode_number=2, air_date=self.today - timedelta(days=1),
            ),
            seen_at=timezone.now() - timedelta(days=5),
        )
        UserEpisode.objects.create(
            user=self.user,
            episode=Episode.objects.create(
                show=newer, season=newer.seasons.get(), season_number=1,
                episode_number=2, air_date=self.today - timedelta(days=1),
            ),
        )

        context = self.run_filter("sort=watched")

        self.assertEqual(self.names(context), ["Newer", "Older", "Never watched"])
