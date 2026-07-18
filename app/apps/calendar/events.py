from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone

from cachalot.api import cachalot_disabled
from django.db.models import Prefetch
from django.utils import timezone as django_timezone

from apps.catalog.localization import metadata_language_for_user, resolve_field
from apps.movies.models import Movie
from apps.tv.models import Episode, UserShow

from .models import CalendarFeed

UTC = timezone.utc
_TRUE_VALUES = {"1", "true", "on"}
_FALSE_VALUES = {"0", "false", "off"}


@dataclass(frozen=True)
class CalendarFilters:
    include_tracked: bool = True
    include_paused: bool = False
    include_dropped: bool = False
    include_movies: bool = False

    @property
    def statuses(self) -> tuple[str, ...]:
        statuses = []
        if self.include_tracked:
            statuses.append(UserShow.Status.TRACKED)
        if self.include_paused:
            statuses.append(UserShow.Status.PAUSED)
        if self.include_dropped:
            statuses.append(UserShow.Status.DROPPED)
        return tuple(statuses)


@dataclass(frozen=True)
class CalendarEvent:
    kind: str
    object_id: int
    external_id: str
    title: str
    subtitle: str
    overview: str
    release_date: date
    starts_at: datetime | None
    ends_at: datetime | None
    runtime: int | None
    status: str
    show_name: str | None = None
    network: str | None = None
    director: str | None = None
    genres: tuple[str, ...] = ()
    episode_id: int | None = None
    show_id: int | None = None
    season_number: int | None = None
    episode_number: int | None = None
    movie_id: int | None = None
    provider: str = "tvdb"


def _enabled(query_params, name: str, *, default: bool) -> bool:
    value = query_params.get(name)
    if value is None:
        return default
    if value in _TRUE_VALUES:
        return True
    if value in _FALSE_VALUES:
        return False
    return default


def parse_filters(query_params) -> CalendarFilters:
    return CalendarFilters(
        include_tracked=_enabled(query_params, "tracked", default=True),
        include_paused=_enabled(query_params, "paused", default=False),
        include_dropped=_enabled(query_params, "dropped", default=False),
        include_movies=_enabled(query_params, "movies", default=False),
    )


def filter_query_params(filters: CalendarFilters) -> dict[str, str]:
    params = {"tracked": "1" if filters.include_tracked else "0"}
    if filters.include_paused:
        params["paused"] = "1"
    if filters.include_dropped:
        params["dropped"] = "1"
    if filters.include_movies:
        params["movies"] = "1"
    return params


def get_calendar_events(
    user,
    start_date: date,
    end_date: date,
    *,
    filters: CalendarFilters,
) -> list[CalendarEvent]:
    events = []
    if filters.statuses:
        events.extend(
            _get_episode_events(
                user,
                start_date,
                end_date,
                filters,
            )
        )
    if filters.include_movies:
        events.extend(
            _get_movie_events(
                user,
                start_date,
                end_date,
            )
        )
    return sorted(events, key=_event_sort_key)


def get_calendar_event(
    user,
    object_id: int,
    *,
    kind: str = "episode",
) -> CalendarEvent | None:
    if kind == "movie":
        return _get_movie_event(
            user,
            object_id,
            metadata_language_for_user(user, "tmdb"),
        )
    if kind != "episode":
        return None

    episode = (
        Episode.objects.filter(id=object_id, show__user_states__user=user)
        .select_related("show", "season")
        .prefetch_related(
            Prefetch(
                "show__user_states",
                queryset=UserShow.objects.filter(user=user),
                to_attr="_calendar_user_states",
            )
        )
        .first()
    )
    if episode is None or episode.air_date is None:
        return None

    status = UserShow.objects.get(user=user, show=episode.show).status
    return _episode_event(
        episode,
        status=status,
        language=metadata_language_for_user(user, episode.show.provider),
    )


def get_calendar_feed(user) -> CalendarFeed:
    with cachalot_disabled():
        feed, _created = CalendarFeed.objects.get_or_create(user=user)
    return feed


def get_feed_window(*, now: datetime | None = None) -> tuple[date, date]:
    current_date = (now or django_timezone.now()).astimezone(UTC).date()
    return current_date - timedelta(days=30), current_date + timedelta(days=90)


def _get_episode_events(user, start_date, end_date, filters):
    episodes = (
        Episode.objects.filter(
            show__user_states__user=user,
            show__user_states__status__in=filters.statuses,
            season_number__gt=0,
            air_date__gte=start_date,
            air_date__lte=end_date,
        )
        .select_related("show", "season")
        .prefetch_related(
            Prefetch(
                "show__user_states",
                queryset=UserShow.objects.filter(user=user),
                to_attr="_calendar_user_states",
            )
        )
    )
    return [
        _episode_event(
            episode,
            language=metadata_language_for_user(user, episode.show.provider),
        )
        for episode in episodes
    ]


def _get_movie_events(user, start_date, end_date):
    movies = (
        Movie.objects.filter(
            user_states__user=user,
            user_states__on_watchlist=True,
            release_date__gte=start_date,
            release_date__lte=end_date,
        )
        .prefetch_related("genres")
        .order_by("release_date", "title", "id")
    )
    return [
        _movie_event(movie, metadata_language_for_user(user, movie.provider))
        for movie in movies
    ]


def _get_movie_event(user, movie_id: int, language: str) -> CalendarEvent | None:
    movie = (
        Movie.objects.filter(
            id=movie_id,
            user_states__user=user,
            user_states__on_watchlist=True,
            release_date__isnull=False,
        )
        .prefetch_related("genres")
        .first()
    )
    if movie is None:
        return None
    return _movie_event(movie, language)


def _episode_event(
    episode: Episode,
    *,
    status: str | None = None,
    language: str = "eng",
) -> CalendarEvent:
    if status is None:
        status = episode.show._calendar_user_states[0].status

    starts_at = None
    if episode.show.airs_time is not None:
        starts_at = datetime.combine(episode.air_date, episode.show.airs_time, tzinfo=UTC)

    ends_at = None
    if starts_at is not None and episode.runtime is not None:
        ends_at = starts_at + timedelta(minutes=episode.runtime)

    return CalendarEvent(
        kind="episode",
        object_id=episode.id,
        external_id=episode.show.external_id,
        title=resolve_field(episode, "name", language),
        subtitle=f"S{episode.season_number:02d}E{episode.episode_number:02d}",
        overview=resolve_field(episode, "overview", language),
        release_date=episode.air_date,
        starts_at=starts_at,
        ends_at=ends_at,
        runtime=episode.runtime,
        status=status,
        show_name=resolve_field(episode.show, "name", language),
        network=episode.show.network,
        provider=episode.show.provider,
        episode_id=episode.id,
        show_id=episode.show_id,
        season_number=episode.season_number,
        episode_number=episode.episode_number,
    )


def _movie_event(movie: Movie, language: str = "en-US") -> CalendarEvent:
    return CalendarEvent(
        kind="movie",
        object_id=movie.id,
        external_id=movie.external_id,
        title=resolve_field(movie, "title", language),
        subtitle="Movie",
        overview=resolve_field(movie, "overview", language),
        release_date=movie.release_date,
        starts_at=None,
        ends_at=None,
        runtime=movie.runtime,
        status="tracked",
        director=movie.director,
        genres=tuple(resolve_field(genre, "name", language) for genre in movie.genres.all()),
        provider=movie.provider,
        movie_id=movie.id,
    )


def _event_sort_key(event: CalendarEvent):
    starts_at = event.starts_at or datetime.combine(
        event.release_date,
        time.max,
        tzinfo=UTC,
    )
    return event.release_date, starts_at, event.title.casefold(), event.object_id
