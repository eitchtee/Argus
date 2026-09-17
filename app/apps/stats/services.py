import math
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, time, timedelta
from decimal import Decimal

from django.db.models import Avg, Count, IntegerField, Q, Sum, Value
from django.db.models.functions import (
    Coalesce,
    ExtractHour,
    ExtractIsoWeekDay,
    TruncDate,
    TruncMonth,
)
from django.utils import timezone

from apps.catalog.localization import metadata_language_for_user, resolve_field
from apps.catalog.models import Genre, MediaRating
from apps.catalog.ratings import HALF_STEP, MAX_SCORE, MIN_SCORE
from apps.movies.models import Movie, UserMovie
from apps.tv.models import Episode, Show, UserEpisode, UserShow
from apps.tv.services import get_watchlist_shows


ACTIVITY_MONTHS = 12
PACE_WINDOW_DAYS = 30
TOP_GENRES = 8
TOP_SHOWS = 5
DAY_MINUTES = 24 * 60

# Episodes without their own runtime borrow the show's average, so a show
# synced from a provider that only reports the latter still counts.
MOVIE_MINUTES = Coalesce("movie__runtime", Value(0), output_field=IntegerField())
EPISODE_MINUTES = Coalesce(
    "episode__runtime",
    "episode__show__average_runtime",
    Value(0),
    output_field=IntegerField(),
)


@dataclass(frozen=True)
class Bar:
    label: str
    minutes: int
    count: int
    percent: int
    start: date | None = None


@dataclass(frozen=True)
class ShowTime:
    show: Show
    minutes: int
    episodes: int


@dataclass(frozen=True)
class Binge:
    show: Show
    day: date
    episodes: int


@dataclass(frozen=True)
class UserStats:
    movies_watched: int
    movie_minutes: int
    episodes_watched: int
    episode_minutes: int
    shows_started: int
    watchlist_movies: int
    watchlist_movie_minutes: int
    unreleased_movies: int
    pending_episodes: int
    pending_episode_minutes: int
    upcoming_episodes: int
    daily_pace_minutes: int
    days_to_clear: int | None
    months: list[Bar]
    heatmap: list[tuple[int, list[int]]]
    peak_weekday: int | None
    peak_hour: time | None
    active_days: int
    current_streak: int
    longest_streak: int
    busiest_day: date | None
    busiest_day_minutes: int
    watching_since: date | None
    genres: list[Bar]
    top_shows: list[ShowTime]
    show_conditions: dict[str, int]
    binge: Binge | None
    longest_movie: Movie | None
    ratings: list[Bar]
    rating_count: int
    rating_average: Decimal | None
    decades: list[Bar]

    @property
    def watched_minutes(self):
        return self.movie_minutes + self.episode_minutes

    @property
    def backlog_minutes(self):
        return self.watchlist_movie_minutes + self.pending_episode_minutes

    @property
    def has_data(self):
        return bool(
            self.movies_watched
            or self.episodes_watched
            or self.watchlist_movies
            or self.pending_episodes
        )

    @property
    def completion_percent(self):
        total = self.watched_minutes + self.backlog_minutes
        return round(self.watched_minutes * 100 / total) if total else 0

    @property
    def movie_share_percent(self):
        total = self.watched_minutes
        return round(self.movie_minutes * 100 / total) if total else 0

    @property
    def tv_share_percent(self):
        return 100 - self.movie_share_percent if self.watched_minutes else 0

    @property
    def clear_date(self):
        if self.days_to_clear is None:
            return None
        return timezone.localdate() + timedelta(days=self.days_to_clear)


def get_user_stats(user) -> UserStats:
    today = timezone.localdate()
    watched_movies = _watched_movies(user)
    watched_episodes = _watched_episodes(user)

    movie_totals = watched_movies.aggregate(
        count=Count("id"),
        minutes=Sum(MOVIE_MINUTES),
    )
    episode_totals = watched_episodes.aggregate(
        count=Count("id"),
        minutes=Sum(EPISODE_MINUTES),
        shows=Count("episode__show", distinct=True),
    )

    watchlist = UserMovie.objects.filter(user=user, on_watchlist=True, is_seen=False)
    watchlist_totals = watchlist.aggregate(
        count=Count("id"),
        minutes=Sum(MOVIE_MINUTES),
        unreleased=Count(
            "id",
            filter=Q(movie__release_date__isnull=True)
            | Q(movie__release_date__gt=today),
        ),
    )

    tracked_episodes = Episode.objects.filter(
        show__user_states__user=user,
        show__user_states__status=UserShow.Status.TRACKED,
        season_number__gt=0,
    )
    pending_totals = (
        tracked_episodes.filter(air_date__isnull=False, air_date__lte=today)
        .exclude(user_states__user=user)
        .aggregate(
            count=Count("id"),
            minutes=Sum(
                Coalesce(
                    "runtime",
                    "show__average_runtime",
                    Value(0),
                    output_field=IntegerField(),
                )
            ),
        )
    )
    upcoming_episodes = tracked_episodes.filter(air_date__gt=today).count()

    pace_since = timezone.now() - timedelta(days=PACE_WINDOW_DAYS)
    recent_minutes = (
        watched_movies.filter(seen_at__gte=pace_since).aggregate(
            minutes=Sum(MOVIE_MINUTES)
        )["minutes"]
        or 0
    ) + (
        watched_episodes.filter(seen_at__gte=pace_since).aggregate(
            minutes=Sum(EPISODE_MINUTES)
        )["minutes"]
        or 0
    )
    daily_pace = recent_minutes / PACE_WINDOW_DAYS
    backlog_minutes = (watchlist_totals["minutes"] or 0) + (
        pending_totals["minutes"] or 0
    )
    days_to_clear = (
        math.ceil(backlog_minutes / daily_pace)
        if daily_pace and backlog_minutes
        else None
    )

    days = _activity(user, day=TruncDate("seen_at"))
    # Imports stamp a whole backlog with one date. A day holding more runtime
    # than it has minutes is bookkeeping, not viewing, so records skip it.
    bulk_days = {key[0] for key, (minutes, _plays) in days.items() if minutes > DAY_MINUTES}
    busiest = max(
        (item for item in days.items() if item[0][0] not in bulk_days),
        key=lambda item: (item[1][0], item[0]),
        default=None,
    )
    current_streak, longest_streak = _streaks([key[0] for key in days], today)
    heatmap, peak = _heatmap(user)

    ratings, rating_count, rating_average = _ratings(user)

    return UserStats(
        movies_watched=movie_totals["count"],
        movie_minutes=movie_totals["minutes"] or 0,
        episodes_watched=episode_totals["count"],
        episode_minutes=episode_totals["minutes"] or 0,
        shows_started=episode_totals["shows"],
        watchlist_movies=watchlist_totals["count"],
        watchlist_movie_minutes=watchlist_totals["minutes"] or 0,
        unreleased_movies=watchlist_totals["unreleased"],
        pending_episodes=pending_totals["count"],
        pending_episode_minutes=pending_totals["minutes"] or 0,
        upcoming_episodes=upcoming_episodes,
        daily_pace_minutes=round(daily_pace),
        days_to_clear=days_to_clear,
        months=_months(user, today),
        heatmap=heatmap,
        peak_weekday=peak[0] if peak else None,
        peak_hour=time(peak[1]) if peak else None,
        active_days=len(days),
        current_streak=current_streak,
        longest_streak=longest_streak,
        busiest_day=busiest[0][0] if busiest else None,
        busiest_day_minutes=busiest[1][0] if busiest else 0,
        watching_since=min((key[0] for key in days), default=None),
        genres=_genres(user),
        top_shows=_top_shows(user),
        show_conditions=Counter(
            show.user_condition for show in get_watchlist_shows(user)
        ),
        binge=_binge(user, bulk_days),
        longest_movie=_longest_movie(user),
        ratings=ratings,
        rating_count=rating_count,
        rating_average=rating_average,
        decades=_decades(user),
    )


def _watched_movies(user):
    return UserMovie.objects.filter(user=user, is_seen=True)


def _watched_episodes(user):
    return UserEpisode.objects.filter(user=user)


def _activity(user, **buckets):
    """Sum watched minutes and plays per bucket across movies and episodes.

    Buckets are expressions over ``seen_at``; Django evaluates them in the
    active timezone, so a late-night episode lands on the viewer's own day.
    """
    totals = defaultdict(lambda: [0, 0])
    sources = (
        (_watched_movies(user), MOVIE_MINUTES),
        (_watched_episodes(user), EPISODE_MINUTES),
    )
    for queryset, minutes in sources:
        rows = (
            queryset.filter(seen_at__isnull=False)
            .annotate(**buckets)
            .values(*buckets)
            .annotate(minutes=Sum(minutes), plays=Count("id"))
            .order_by()
        )
        for row in rows:
            key = tuple(row[name] for name in buckets)
            totals[key][0] += row["minutes"] or 0
            totals[key][1] += row["plays"]
    return totals


def _bars(rows):
    """Scale ``(label, minutes, count, start)`` rows against the largest one."""
    peak = max((row[1] for row in rows), default=0)
    return [
        Bar(
            label=label,
            minutes=minutes,
            count=count,
            percent=_percent(minutes, peak),
            start=start,
        )
        for label, minutes, count, start in rows
    ]


def _percent(value, peak):
    if not peak or not value:
        return 0
    # Keep any non-zero bar visible instead of rounding it away.
    return max(2, round(value * 100 / peak))


def _months(user, today):
    starts = []
    year, month = today.year, today.month
    for _ in range(ACTIVITY_MONTHS):
        starts.append(date(year, month, 1))
        year, month = (year - 1, 12) if month == 1 else (year, month - 1)
    starts.reverse()

    totals = defaultdict(lambda: [0, 0])
    for key, (minutes, plays) in _activity(user, month=TruncMonth("seen_at")).items():
        month_start = timezone.localtime(key[0]).date()
        totals[month_start][0] += minutes
        totals[month_start][1] += plays

    return _bars(
        [(start.strftime("%Y-%m"), *totals[start], start) for start in starts]
    )


def _heatmap(user):
    """Minutes watched per ISO weekday and hour, scaled 0-100 for shading."""
    grid = _activity(
        user,
        weekday=ExtractIsoWeekDay("seen_at"),
        hour=ExtractHour("seen_at"),
    )
    peak_minutes = max((minutes for minutes, _plays in grid.values()), default=0)
    rows = [
        (
            weekday,
            [
                _percent(grid.get((weekday, hour), (0, 0))[0], peak_minutes)
                for hour in range(24)
            ],
        )
        for weekday in range(1, 8)
    ]
    peak = (
        max(grid, key=lambda key: (grid[key][0], grid[key][1]))
        if peak_minutes
        else None
    )
    return rows, peak


def _streaks(days, today):
    longest = run = 0
    previous = None
    for day in sorted(days):
        run = run + 1 if previous and day - previous == timedelta(days=1) else 1
        longest = max(longest, run)
        previous = day
    # A streak survives until the end of the day after the last watch.
    current = run if previous and (today - previous).days <= 1 else 0
    return current, longest


def _genres(user):
    languages = {}
    totals = {}

    def add(genre, minutes, titles):
        if genre.provider not in languages:
            languages[genre.provider] = metadata_language_for_user(
                user, genre.provider
            )
        name = resolve_field(genre, "name", languages[genre.provider]) or genre.name
        # TMDB and TVDB keep separate genre catalogs, and one may be translated
        # where the other is not. Both store the English name, so merge on it
        # and prefer whichever label actually came back localized.
        entry = totals.setdefault(genre.name.casefold(), [name, 0, 0])
        if entry[0] == genre.name and name != genre.name:
            entry[0] = name
        entry[1] += minutes or 0
        entry[2] += titles

    movie_genres = Genre.objects.filter(
        movies__user_states__user=user,
        movies__user_states__is_seen=True,
    ).annotate(
        minutes=Sum(
            Coalesce("movies__runtime", Value(0), output_field=IntegerField())
        ),
        titles=Count("movies", distinct=True),
    )
    show_genres = Genre.objects.filter(
        shows__episodes__user_states__user=user,
    ).annotate(
        minutes=Sum(
            Coalesce(
                "shows__episodes__runtime",
                "shows__average_runtime",
                Value(0),
                output_field=IntegerField(),
            )
        ),
        titles=Count("shows", distinct=True),
    )
    for genre in [*movie_genres, *show_genres]:
        add(genre, genre.minutes, genre.titles)

    ranked = sorted(
        totals.values(),
        key=lambda entry: (-entry[1], -entry[2], entry[0].casefold()),
    )[:TOP_GENRES]
    return _bars([(name, minutes, titles, None) for name, minutes, titles in ranked])


def _top_shows(user):
    rows = list(
        _watched_episodes(user)
        .values("episode__show_id")
        .annotate(minutes=Sum(EPISODE_MINUTES), episodes=Count("id"))
        .order_by("-minutes", "-episodes", "episode__show_id")[:TOP_SHOWS]
    )
    shows = Show.objects.in_bulk([row["episode__show_id"] for row in rows])
    return [
        ShowTime(
            show=shows[row["episode__show_id"]],
            minutes=row["minutes"] or 0,
            episodes=row["episodes"],
        )
        for row in rows
    ]


def _binge(user, bulk_days):
    rows = (
        _watched_episodes(user)
        .filter(seen_at__isnull=False)
        .annotate(day=TruncDate("seen_at"))
        .values("day", "episode__show_id")
        .annotate(episodes=Count("id"))
        .order_by("-episodes", "-day")
    )
    row = next((row for row in rows if row["day"] not in bulk_days), None)
    # A single episode in a day is not much of a binge.
    if row is None or row["episodes"] < 2:
        return None
    return Binge(
        show=Show.objects.get(pk=row["episode__show_id"]),
        day=row["day"],
        episodes=row["episodes"],
    )


def _longest_movie(user):
    user_movie = (
        _watched_movies(user)
        .filter(movie__runtime__isnull=False)
        .select_related("movie")
        .order_by("-movie__runtime", "movie__title")
        .first()
    )
    return user_movie.movie if user_movie else None


def _ratings(user):
    ratings = MediaRating.objects.filter(user=user).order_by()
    summary = ratings.aggregate(count=Count("id"), average=Avg("score"))
    counts = dict(
        ratings.values("score").annotate(count=Count("id")).values_list("score", "count")
    )
    steps = int((MAX_SCORE - MIN_SCORE) / HALF_STEP) + 1
    scores = [MIN_SCORE + HALF_STEP * index for index in range(steps)]
    peak = max(counts.values(), default=0)
    bars = [
        Bar(
            label=str(score),
            minutes=0,
            count=counts.get(score, 0),
            percent=_percent(counts.get(score, 0), peak),
        )
        for score in scores
    ]
    average = summary["average"]
    return (
        bars,
        summary["count"],
        Decimal(average).quantize(Decimal("0.1")) if average is not None else None,
    )


def _decades(user):
    years = (
        _watched_movies(user)
        .filter(movie__release_date__isnull=False)
        .order_by()
        .values_list("movie__release_date__year", flat=True)
    )
    counts = Counter(year // 10 * 10 for year in years)
    if not counts:
        return []
    peak = max(counts.values())
    return [
        Bar(
            label=f"{decade}s",
            minutes=0,
            count=counts[decade],
            percent=_percent(counts[decade], peak),
        )
        for decade in range(min(counts), max(counts) + 10, 10)
    ]
