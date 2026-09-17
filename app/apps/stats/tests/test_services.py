from datetime import date, datetime, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.test import TestCase
from django.utils import timezone

from apps.catalog.models import Genre, MediaRating
from apps.common.templatetags.dates import duration
from apps.movies.models import Movie, UserMovie
from apps.stats.services import _streaks, get_user_stats
from apps.tv.models import Episode, Season, Show, UserEpisode, UserShow


class UserStatsTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("user@example.com")
        self.today = timezone.localdate()

    def make_show(self, external_id, *, average_runtime=None, status=UserShow.Status.TRACKED):
        show = Show.objects.create(
            external_id=external_id,
            name=f"Show {external_id}",
            average_runtime=average_runtime,
        )
        UserShow.objects.create(user=self.user, show=show, status=status)
        season = Season.objects.create(show=show, season_number=1)
        return show, season

    def make_episode(self, show, season, number, *, runtime=None, air_date=None):
        return Episode.objects.create(
            show=show,
            season=season,
            season_number=season.season_number,
            episode_number=number,
            runtime=runtime,
            air_date=air_date,
        )

    def test_totals_and_backlog_use_runtimes_with_show_average_fallback(self):
        watched = Movie.objects.create(external_id="1", title="Watched", runtime=120)
        wanted = Movie.objects.create(
            external_id="2",
            title="Wanted",
            runtime=90,
            release_date=self.today - timedelta(days=100),
        )
        upcoming = Movie.objects.create(
            external_id="3",
            title="Upcoming",
            runtime=100,
            release_date=self.today + timedelta(days=10),
        )
        UserMovie.objects.create(user=self.user, movie=watched, is_seen=True, seen_at=timezone.now())
        UserMovie.objects.create(user=self.user, movie=wanted, on_watchlist=True)
        UserMovie.objects.create(user=self.user, movie=upcoming, on_watchlist=True)

        aired = self.today - timedelta(days=5)
        show, season = self.make_show("a", average_runtime=40)
        first = self.make_episode(show, season, 1, runtime=50, air_date=aired)
        self.make_episode(show, season, 2, air_date=aired)  # pending, 40m fallback
        self.make_episode(show, season, 3, runtime=45, air_date=self.today + timedelta(days=3))
        UserEpisode.objects.create(user=self.user, episode=first)

        paused, paused_season = self.make_show("b", average_runtime=30, status=UserShow.Status.PAUSED)
        self.make_episode(paused, paused_season, 1, air_date=aired)

        stats = get_user_stats(self.user)

        self.assertEqual(stats.movies_watched, 1)
        self.assertEqual(stats.movie_minutes, 120)
        self.assertEqual(stats.episodes_watched, 1)
        self.assertEqual(stats.episode_minutes, 50)
        self.assertEqual(stats.shows_started, 1)
        self.assertEqual(stats.watched_minutes, 170)
        self.assertEqual(stats.watchlist_movies, 2)
        self.assertEqual(stats.unreleased_movies, 1)
        self.assertEqual(stats.pending_episodes, 1)
        self.assertEqual(stats.pending_episode_minutes, 40)
        self.assertEqual(stats.upcoming_episodes, 1)
        self.assertEqual(stats.backlog_minutes, 230)
        self.assertEqual(stats.completion_percent, round(170 * 100 / 400))
        self.assertEqual(stats.show_conditions["watching"], 1)
        self.assertEqual(stats.show_conditions["paused"], 1)

    def test_pace_estimates_days_to_clear_backlog(self):
        watched = Movie.objects.create(external_id="1", title="Watched", runtime=300)
        wanted = Movie.objects.create(external_id="2", title="Wanted", runtime=100)
        UserMovie.objects.create(user=self.user, movie=watched, is_seen=True, seen_at=timezone.now())
        UserMovie.objects.create(user=self.user, movie=wanted, on_watchlist=True)

        stats = get_user_stats(self.user)

        self.assertEqual(stats.daily_pace_minutes, 10)
        self.assertEqual(stats.days_to_clear, 10)

    def test_genres_merge_across_providers_and_rank_by_minutes(self):
        tmdb_drama = Genre.objects.create(provider="tmdb", external_id="18", name="Drama")
        tvdb_drama = Genre.objects.create(provider="tvdb", external_id="7", name="Drama")
        comedy = Genre.objects.create(provider="tmdb", external_id="35", name="Comedy")
        long_movie = Movie.objects.create(external_id="1", title="Long", runtime=150)
        short_movie = Movie.objects.create(external_id="2", title="Short", runtime=90)
        long_movie.genres.add(tmdb_drama)
        short_movie.genres.add(comedy)
        UserMovie.objects.create(user=self.user, movie=long_movie, is_seen=True)
        UserMovie.objects.create(user=self.user, movie=short_movie, is_seen=True)
        show, season = self.make_show("a")
        show.genres.add(tvdb_drama)
        UserEpisode.objects.create(
            user=self.user,
            episode=self.make_episode(show, season, 1, runtime=30),
        )

        stats = get_user_stats(self.user)

        self.assertEqual(
            [(genre.label, genre.minutes, genre.count) for genre in stats.genres],
            [("Drama", 180, 2), ("Comedy", 90, 1)],
        )
        self.assertEqual(stats.genres[0].percent, 100)
        self.assertEqual(stats.genres[1].percent, 50)

    def test_activity_buckets_streaks_and_records(self):
        now = timezone.localtime()
        show, season = self.make_show("a", average_runtime=20)
        for number, days_ago in ((1, 0), (2, 1), (3, 1), (4, 1), (5, 5)):
            UserEpisode.objects.create(
                user=self.user,
                episode=self.make_episode(show, season, number),
                seen_at=now - timedelta(days=days_ago),
            )
        movie = Movie.objects.create(external_id="1", title="Epic", runtime=200, release_date=date(1994, 1, 1))
        UserMovie.objects.create(user=self.user, movie=movie, is_seen=True, seen_at=now - timedelta(days=5))

        stats = get_user_stats(self.user)

        self.assertEqual(len(stats.months), 12)
        self.assertEqual(stats.months[-1].start, self.today.replace(day=1))
        self.assertEqual(sum(month.minutes for month in stats.months), 300)
        self.assertEqual(stats.active_days, 3)
        self.assertEqual(stats.current_streak, 2)
        self.assertEqual(stats.longest_streak, 2)
        self.assertEqual(stats.busiest_day, (now - timedelta(days=5)).date())
        self.assertEqual(stats.busiest_day_minutes, 220)
        self.assertEqual(stats.binge.episodes, 3)
        self.assertEqual(stats.longest_movie, movie)
        self.assertEqual(stats.top_shows[0].episodes, 5)
        self.assertEqual(stats.top_shows[0].minutes, 100)
        self.assertEqual([decade.label for decade in stats.decades], ["1990s"])
        self.assertEqual(sum(sum(cells) > 0 for _weekday, cells in stats.heatmap), 3)
        self.assertIsNotNone(stats.peak_hour)

    def test_records_skip_days_with_more_runtime_than_a_day(self):
        now = timezone.localtime()
        imported_at = now - timedelta(days=40)
        show, season = self.make_show("a", average_runtime=60)
        for number in range(1, 31):  # 30h stamped on one day by an import
            UserEpisode.objects.create(
                user=self.user,
                episode=self.make_episode(show, season, number),
                seen_at=imported_at,
            )
        for number in (31, 32):
            UserEpisode.objects.create(
                user=self.user,
                episode=self.make_episode(show, season, number),
                seen_at=now,
            )

        stats = get_user_stats(self.user)

        self.assertEqual(stats.busiest_day, now.date())
        self.assertEqual(stats.busiest_day_minutes, 120)
        self.assertEqual(stats.binge.episodes, 2)
        self.assertEqual(stats.binge.day, now.date())
        self.assertEqual(stats.active_days, 2)

    def test_genre_merge_prefers_the_localized_label(self):
        self.user.settings.tmdb_metadata_language = "pt-BR"
        self.user.settings.save()
        tmdb_comedy = Genre.objects.create(
            provider="tmdb",
            external_id="35",
            name="Comedy",
            translations={"pt-BR": {"name": "Comédia"}},
        )
        tvdb_comedy = Genre.objects.create(provider="tvdb", external_id="3", name="Comedy")
        movie = Movie.objects.create(external_id="1", title="Funny", runtime=100)
        movie.genres.add(tmdb_comedy)
        UserMovie.objects.create(user=self.user, movie=movie, is_seen=True)
        show, season = self.make_show("a")
        show.genres.add(tvdb_comedy)
        UserEpisode.objects.create(
            user=self.user,
            episode=self.make_episode(show, season, 1, runtime=30),
        )

        stats = get_user_stats(self.user)

        self.assertEqual(
            [(genre.label, genre.minutes, genre.count) for genre in stats.genres],
            [("Comédia", 130, 2)],
        )

    def test_ratings_distribution_covers_every_half_star(self):
        movie = Movie.objects.create(external_id="1", title="Rated")
        content_type = ContentType.objects.get_for_model(Movie)
        MediaRating.objects.create(
            user=self.user,
            media_type="movie",
            content_type=content_type,
            object_id=movie.pk,
            score=Decimal("4.5"),
        )
        show = Show.objects.create(external_id="s", name="Rated show")
        MediaRating.objects.create(
            user=self.user,
            media_type="show",
            content_type=ContentType.objects.get_for_model(Show),
            object_id=show.pk,
            score=Decimal("3.0"),
        )

        stats = get_user_stats(self.user)

        self.assertEqual(len(stats.ratings), 10)
        self.assertEqual(stats.rating_count, 2)
        self.assertEqual(stats.rating_average, Decimal("3.8"))
        by_label = {bar.label: bar.count for bar in stats.ratings}
        self.assertEqual(by_label["4.5"], 1)
        self.assertEqual(by_label["3.0"], 1)
        self.assertEqual(by_label["0.5"], 0)

    def test_other_users_data_is_ignored(self):
        other = get_user_model().objects.create_user("other@example.com")
        movie = Movie.objects.create(external_id="1", title="Theirs", runtime=100)
        UserMovie.objects.create(user=other, movie=movie, is_seen=True, seen_at=timezone.now())

        stats = get_user_stats(self.user)

        self.assertFalse(stats.has_data)
        self.assertEqual(stats.watched_minutes, 0)
        self.assertIsNone(stats.longest_movie)


class StreakTests(TestCase):
    def test_current_streak_survives_until_the_day_after(self):
        today = date(2026, 9, 15)
        days = [date(2026, 9, 1), date(2026, 9, 2), date(2026, 9, 3), date(2026, 9, 13), date(2026, 9, 14)]

        self.assertEqual(_streaks(days, today), (2, 3))
        self.assertEqual(_streaks(days, date(2026, 9, 16)), (0, 3))
        self.assertEqual(_streaks([], today), (0, 0))


class DurationFilterTests(TestCase):
    def test_formats_long_spans_in_days(self):
        self.assertEqual(duration(0), "0m")
        self.assertEqual(duration(45), "45m")
        self.assertEqual(duration(125), "2h 5m")
        self.assertEqual(duration(24 * 60), "1d")
        self.assertEqual(duration(3 * 24 * 60 + 250), "3d 4h")
        self.assertEqual(duration(None), "")
