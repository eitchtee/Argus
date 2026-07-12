from datetime import date, datetime, time, timedelta, timezone

from django.contrib.auth import get_user_model
from django.http import QueryDict
from django.test import TestCase

from apps.calendar.events import (
    CalendarFilters,
    filter_query_params,
    get_calendar_event,
    get_calendar_events,
    get_calendar_feed,
    get_feed_window,
    parse_filters,
)
from apps.movies.models import Movie, UserMovie
from apps.tv.models import Episode, Season, Show, UserShow


class CalendarEventServiceTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("user@example.com")

    def make_show(self, name, status, *, airs_time=time(21, 0), external_id=None):
        show = Show.objects.create(
            external_id=external_id or name.lower().replace(" ", "-"),
            name=name,
            airs_time=airs_time,
        )
        UserShow.objects.create(user=self.user, show=show, status=status)
        Season.objects.create(show=show, season_number=1, name="Season 1")
        return show

    def make_episode(self, show, air_date, *, runtime=60, name="Episode"):
        season = show.seasons.get(season_number=1)
        return Episode.objects.create(
            show=show,
            season=season,
            season_number=1,
            episode_number=show.episodes.count() + 1,
            name=name,
            overview="Episode summary.",
            air_date=air_date,
            runtime=runtime,
        )

    def make_movie(self, external_id, *, on_watchlist, release_date):
        movie = Movie.objects.create(
            provider="tmdb",
            external_id=external_id,
            title=external_id.replace("-", " ").title(),
            overview="Movie summary.",
            director="Example Director",
            release_date=release_date,
            runtime=120,
        )
        UserMovie.objects.create(
            user=self.user,
            movie=movie,
            on_watchlist=on_watchlist,
        )
        return movie

    def test_tracked_filter_defaults_to_true_and_can_be_disabled(self):
        self.assertTrue(parse_filters(QueryDict()).include_tracked)
        self.assertFalse(parse_filters(QueryDict("tracked=0")).include_tracked)
        self.assertTrue(parse_filters(QueryDict("tracked=0&tracked=1")).include_tracked)
        self.assertTrue(parse_filters(QueryDict("tracked=maybe")).include_tracked)

        self.assertEqual(filter_query_params(CalendarFilters()), {"tracked": "1"})
        self.assertEqual(
            filter_query_params(CalendarFilters(False, True, True, True)),
            {"tracked": "0", "paused": "1", "dropped": "1", "movies": "1"},
        )

    def test_defaults_to_tracked_shows(self):
        tracked = self.make_show("Tracked", UserShow.Status.TRACKED)
        paused = self.make_show("Paused", UserShow.Status.PAUSED)
        dropped = self.make_show("Dropped", UserShow.Status.DROPPED)
        self.make_episode(tracked, date(2026, 7, 10))
        self.make_episode(paused, date(2026, 7, 11))
        self.make_episode(dropped, date(2026, 7, 12))

        events = get_calendar_events(
            self.user,
            date(2026, 7, 1),
            date(2026, 7, 31),
            filters=CalendarFilters(),
        )

        self.assertEqual([event.show_name for event in events], ["Tracked"])

    def test_season_zero_episodes_are_excluded_from_calendar_events(self):
        show = self.make_show("Calendar Show", UserShow.Status.TRACKED)
        special_season = Season.objects.create(
            show=show,
            season_number=0,
            name="Specials",
        )
        Episode.objects.create(
            show=show,
            season=special_season,
            season_number=0,
            episode_number=1,
            name="Behind the Scenes",
            air_date=date(2026, 7, 10),
        )
        self.make_episode(show, date(2026, 7, 10), name="Regular Episode")

        events = get_calendar_events(
            self.user,
            date(2026, 7, 1),
            date(2026, 7, 31),
            filters=CalendarFilters(),
        )

        self.assertEqual(
            [(event.season_number, event.title) for event in events],
            [(1, "Regular Episode")],
        )

    def test_optional_statuses_are_included_without_leaking_other_users(self):
        tracked = self.make_show("Tracked", UserShow.Status.TRACKED)
        paused = self.make_show("Paused", UserShow.Status.PAUSED)
        self.make_episode(tracked, date(2026, 7, 10))
        self.make_episode(paused, date(2026, 7, 11))
        other_user = get_user_model().objects.create_user("other@example.com")
        other_show = Show.objects.create(name="Other User Show", external_id="other")
        other_season = Season.objects.create(show=other_show, season_number=1, name="Season 1")
        UserShow.objects.create(user=other_user, show=other_show)
        Episode.objects.create(
            show=other_show,
            season=other_season,
            season_number=1,
            episode_number=1,
            air_date=date(2026, 7, 10),
        )

        events = get_calendar_events(
            self.user,
            date(2026, 7, 1),
            date(2026, 7, 31),
            filters=CalendarFilters(include_paused=True),
        )

        self.assertEqual([event.show_name for event in events], ["Tracked", "Paused"])

    def test_combines_utc_air_time_and_runtime(self):
        show = self.make_show("Timed", UserShow.Status.TRACKED, airs_time=time(21, 0))
        self.make_episode(show, date(2026, 7, 10), runtime=60)

        event = get_calendar_events(
            self.user,
            date(2026, 7, 10),
            date(2026, 7, 10),
            filters=CalendarFilters(),
        )[0]

        self.assertEqual(event.starts_at, datetime(2026, 7, 10, 21, 0, tzinfo=timezone.utc))
        self.assertEqual(event.ends_at, datetime(2026, 7, 10, 22, 0, tzinfo=timezone.utc))

    def test_missing_air_time_is_all_day_and_missing_date_is_excluded(self):
        no_time = self.make_show("Date Only", UserShow.Status.TRACKED, airs_time=None)
        missing_date = self.make_show("No Date", UserShow.Status.TRACKED)
        self.make_episode(no_time, date(2026, 7, 10))
        self.make_episode(missing_date, None)

        events = get_calendar_events(
            self.user,
            date(2026, 7, 1),
            date(2026, 7, 31),
            filters=CalendarFilters(),
        )

        self.assertEqual(len(events), 1)
        self.assertIsNone(events[0].starts_at)
        self.assertIsNone(events[0].ends_at)

    def test_movies_are_opt_in_and_use_the_watchlist_state(self):
        tracked_movie = self.make_movie(
            "tracked-movie",
            on_watchlist=True,
            release_date=date(2026, 7, 10),
        )
        self.make_movie(
            "not-tracked-movie",
            on_watchlist=False,
            release_date=date(2026, 7, 10),
        )

        without_movies = get_calendar_events(
            self.user,
            date(2026, 7, 1),
            date(2026, 7, 31),
            filters=CalendarFilters(),
        )
        with_movies = get_calendar_events(
            self.user,
            date(2026, 7, 1),
            date(2026, 7, 31),
            filters=CalendarFilters(include_movies=True),
        )

        self.assertEqual(without_movies, [])
        self.assertEqual(
            [(event.kind, event.movie_id) for event in with_movies],
            [("movie", tracked_movie.id)],
        )
        self.assertIsNone(with_movies[0].starts_at)
        self.assertIsNone(with_movies[0].ends_at)

    def test_combined_events_are_ordered_by_release_date_then_title(self):
        show = self.make_show("TV Release", UserShow.Status.TRACKED)
        self.make_episode(show, date(2026, 7, 10))
        self.make_movie(
            "movie-release",
            on_watchlist=True,
            release_date=date(2026, 7, 10),
        )

        events = get_calendar_events(
            self.user,
            date(2026, 7, 1),
            date(2026, 7, 31),
            filters=CalendarFilters(include_movies=True),
        )

        self.assertEqual([event.title for event in events], ["Episode", "Movie Release"])

    def test_movie_event_is_scoped_to_watchlisted_user(self):
        movie = self.make_movie(
            "tracked-movie",
            on_watchlist=True,
            release_date=date(2026, 7, 10),
        )
        other_user = get_user_model().objects.create_user("other@example.com")

        self.assertEqual(
            get_calendar_event(self.user, movie.id, kind="movie").movie_id,
            movie.id,
        )
        self.assertIsNone(get_calendar_event(other_user, movie.id, kind="movie"))

    def test_get_calendar_event_is_scoped_to_user_shows(self):
        show = self.make_show("Tracked", UserShow.Status.TRACKED)
        episode = self.make_episode(show, date(2026, 7, 10))
        other_user = get_user_model().objects.create_user("other@example.com")

        self.assertEqual(get_calendar_event(self.user, episode.id).episode_id, episode.id)
        self.assertIsNone(get_calendar_event(other_user, episode.id))

    def test_filters_parse_optional_statuses_and_ignore_invalid_values(self):
        self.assertEqual(parse_filters(QueryDict()), CalendarFilters())
        self.assertEqual(
            parse_filters(QueryDict("paused=1&dropped=true")),
            CalendarFilters(include_paused=True, include_dropped=True),
        )
        self.assertEqual(parse_filters(QueryDict("paused=maybe")), CalendarFilters())

    def test_feed_is_stable_and_window_is_rolling(self):
        first = get_calendar_feed(self.user)
        second = get_calendar_feed(self.user)
        start, end = get_feed_window(now=datetime(2026, 7, 10, 12, tzinfo=timezone.utc))

        self.assertEqual(first.pk, second.pk)
        self.assertEqual((start, end), (date(2026, 6, 10), date(2026, 10, 8)))
