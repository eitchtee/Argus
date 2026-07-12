import dataclasses
import itertools
from dataclasses import dataclass
from datetime import date, time, timedelta

from django.db import transaction
from django.db.models import Count, Max, Q
from django.utils import timezone

from apps.catalog.models import Genre, SyncStatus
from apps.catalog.providers.exceptions import ProviderError
from apps.catalog.providers.registry import get_provider
from apps.tv.models import Episode, Season, Show, UserEpisode, UserShow


def import_show(external_id: str, *, provider_getter=get_provider) -> Show:
    provider = "tvdb"
    provider_client = provider_getter(provider)

    try:
        detail = provider_client.fetch_detail(external_id)
        episodes = provider_client.fetch_episodes(external_id)
    except ProviderError:
        Show.objects.filter(provider=provider, external_id=external_id).update(
            sync_status=SyncStatus.ERROR,
        )
        raise

    today = timezone.localdate()

    with transaction.atomic():
        show, _created = Show.objects.update_or_create(
            provider=provider,
            external_id=detail.external_id,
            defaults={
                "name": detail.title,
                "overview": detail.overview,
                "poster_path": detail.poster_path,
                "backdrop_path": detail.backdrop_path,
                "first_aired": _parse_date(detail.release_date),
                "status": detail.status,
                "network": detail.network,
                "imdb_id": detail.imdb_id,
                "tmdb_id": detail.tmdb_id,
                "trailer_url": detail.trailer_url,
                "cast": [dataclasses.asdict(member) for member in detail.cast],
                "average_runtime": detail.average_runtime,
                "next_air_date": _parse_date(detail.next_air_date),
                "last_air_date": _parse_date(detail.last_air_date),
                "airs_time": _parse_time(detail.airs_time),
                "last_synced_at": timezone.now(),
                "sync_status": SyncStatus.OK,
            },
        )

        genres = [
            Genre.objects.update_or_create(
                provider=genre.provider,
                external_id=genre.external_id,
                defaults={"name": genre.name},
            )[0]
            for genre in detail.genres
        ]
        show.genres.set(genres)

        seasons = {}
        for item in episodes:
            season = seasons.get(item.season_number)
            if season is None:
                season = Season.objects.update_or_create(
                    show=show,
                    season_number=item.season_number,
                    defaults={
                        "name": _season_name(item.season_number),
                        "overview": "",
                        "poster_path": None,
                    },
                )[0]
                seasons[item.season_number] = season

            Episode.objects.update_or_create(
                show=show,
                season_number=item.season_number,
                episode_number=item.episode_number,
                defaults={
                    "season": season,
                    "absolute_number": item.absolute_number,
                    "name": item.name,
                    "overview": item.overview,
                    "still_path": item.still_path,
                    "air_date": _parse_date(item.air_date),
                    "runtime": item.runtime,
                    "finale_type": item.finale_type,
                },
            )

        show.aired_episode_count = (
            Episode.objects.filter(show=show, season_number__gt=0)
            .filter(Q(air_date__isnull=False) & Q(air_date__lte=today))
            .aggregate(count=Count("id"))["count"]
        )
        show.save(update_fields=["aired_episode_count", "updated_at"])

    return show


def track_show(user, external_id: str, *, import_func=import_show) -> UserShow:
    show = import_func(external_id)
    user_show, _created = UserShow.objects.get_or_create(user=user, show=show)
    user_show.status = UserShow.Status.TRACKED
    user_show.tracking_started_at = timezone.now()
    user_show.save(update_fields=["status", "tracking_started_at", "updated_at"])
    return user_show


def _season_name(season_number: int) -> str:
    if season_number == 0:
        return "Specials"
    return f"Season {season_number}"


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None

    return date.fromisoformat(value)


def _parse_time(value: str | None) -> time | None:
    if not value:
        return None

    try:
        return time.fromisoformat(value)
    except ValueError:
        return None


def drop_show(user, show: Show) -> UserShow:
    user_show, _created = UserShow.objects.get_or_create(user=user, show=show)
    user_show.status = UserShow.Status.DROPPED
    user_show.save(update_fields=["status", "updated_at"])
    return user_show


def pause_show(user, show: Show) -> UserShow:
    user_show, _created = UserShow.objects.get_or_create(user=user, show=show)
    user_show.status = UserShow.Status.PAUSED
    user_show.save(update_fields=["status", "updated_at"])
    return user_show


def delete_show_data(user, show: Show) -> None:
    UserEpisode.objects.filter(user=user, episode__show=show).delete()
    UserShow.objects.filter(user=user, show=show).delete()


def _require_tracking(user, show: Show) -> UserShow:
    try:
        return UserShow.objects.get(user=user, show=show, status=UserShow.Status.TRACKED)
    except UserShow.DoesNotExist as exc:
        raise ValueError("Show must be tracked before managing watched episodes.") from exc


def mark_episode_watched(user, episode: Episode) -> UserEpisode:
    _require_tracking(user, episode.show)
    user_episode, _created = UserEpisode.objects.get_or_create(user=user, episode=episode)
    return user_episode


def unmark_episode_watched(user, episode: Episode) -> None:
    _require_tracking(user, episode.show)
    UserEpisode.objects.filter(user=user, episode=episode).delete()


def mark_season_watched(user, season: Season) -> None:
    _require_tracking(user, season.show)
    today = timezone.localdate()
    aired_episodes = season.episodes.filter(air_date__isnull=False, air_date__lte=today)
    for episode in aired_episodes:
        UserEpisode.objects.get_or_create(user=user, episode=episode)


def unmark_season_watched(user, season: Season) -> None:
    _require_tracking(user, season.show)
    UserEpisode.objects.filter(user=user, episode__season=season).delete()


def mark_show_watched(user, show: Show) -> None:
    _require_tracking(user, show)
    today = timezone.localdate()
    aired_episodes = Episode.objects.filter(
        show=show,
        season_number__gt=0,
        air_date__isnull=False,
        air_date__lte=today,
    )
    for episode in aired_episodes:
        UserEpisode.objects.get_or_create(user=user, episode=episode)


def unmark_show_watched(user, show: Show) -> None:
    _require_tracking(user, show)
    UserEpisode.objects.filter(
        user=user, episode__show=show, episode__season_number__gt=0
    ).delete()


@dataclass
class WatchlistEntry:
    show: Show
    next_episode: Episode
    pending_count: int


@dataclass
class UpNextSections:
    active: list[WatchlistEntry]
    not_seen_in_a_while: list[WatchlistEntry]
    not_started: list[WatchlistEntry]


def get_watchlist(user) -> list[WatchlistEntry]:
    tracked_shows = list(
        Show.objects.filter(user_states__user=user, user_states__status=UserShow.Status.TRACKED)
    )
    if not tracked_shows:
        return []

    today = timezone.localdate()
    shows_by_id = {show.id: show for show in tracked_shows}

    aired_episodes = Episode.objects.filter(
        show__in=tracked_shows,
        season_number__gt=0,
        air_date__isnull=False,
        air_date__lte=today,
    ).order_by("show_id", "air_date", "episode_number")

    watched_ids = set(
        UserEpisode.objects.filter(user=user, episode__show__in=tracked_shows).values_list(
            "episode_id", flat=True
        )
    )

    entries = []
    for show_id, episodes in itertools.groupby(aired_episodes, key=lambda episode: episode.show_id):
        pending = [episode for episode in episodes if episode.id not in watched_ids]
        if not pending:
            continue
        entries.append(
            WatchlistEntry(
                show=shows_by_id[show_id],
                next_episode=pending[0],
                pending_count=len(pending) - 1,
            )
        )

    entries.sort(key=lambda entry: entry.next_episode.air_date, reverse=True)
    return entries


def get_up_next(user) -> UpNextSections:
    entries = get_watchlist(user)
    if not entries:
        return UpNextSections(active=[], not_seen_in_a_while=[], not_started=[])

    show_ids = [entry.show.id for entry in entries]
    activity = {
        row["episode__show_id"]: row
        for row in UserEpisode.objects.filter(
            user=user,
            episode__show_id__in=show_ids,
        )
        .values("episode__show_id")
        .annotate(watched_count=Count("id"), last_seen_at=Max("seen_at"))
    }
    stale_cutoff = timezone.now() - timedelta(days=30)
    active = []
    not_seen_in_a_while = []
    not_started = []

    for entry in entries:
        row = activity.get(entry.show.id)
        if row is None or row["watched_count"] == 0:
            not_started.append(entry)
        elif row["last_seen_at"] < stale_cutoff:
            not_seen_in_a_while.append(entry)
        else:
            active.append(entry)

    return UpNextSections(
        active=active,
        not_seen_in_a_while=not_seen_in_a_while,
        not_started=not_started,
    )


def get_watchlist_entry(user, show: Show) -> WatchlistEntry | None:
    today = timezone.localdate()
    pending = list(
        Episode.objects.filter(
            show=show, season_number__gt=0, air_date__isnull=False, air_date__lte=today
        )
        .exclude(user_states__user=user)
        .order_by("air_date", "episode_number")
    )
    if not pending:
        return None
    return WatchlistEntry(show=show, next_episode=pending[0], pending_count=len(pending) - 1)


@dataclass
class UpcomingEntry:
    episode: Episode
    countdown: str
    watched: bool


def countdown_label(air_date: date, today: date) -> str:
    delta = (air_date - today).days
    if delta == -1:
        return "Yesterday"
    if delta == 0:
        return "Today"
    if delta == 1:
        return "Tomorrow"
    return f"{delta} days"


def get_upcoming_episodes(user, count: int = 10) -> list[UpcomingEntry]:
    tracked_shows = Show.objects.filter(
        user_states__user=user,
        user_states__status=UserShow.Status.TRACKED,
    )
    today = timezone.localdate()
    yesterday = today - timedelta(days=1)

    episodes = list(
        Episode.objects.filter(
            show__in=tracked_shows,
            season_number__gt=0,
            air_date__gte=yesterday,
        )
        .select_related("show")
        .order_by("air_date", "show__name", "episode_number")[:count]
    )
    if not episodes:
        return []

    watched_ids = set(
        UserEpisode.objects.filter(user=user, episode__in=episodes).values_list(
            "episode_id", flat=True
        )
    )

    return [
        UpcomingEntry(
            episode=episode,
            countdown=countdown_label(episode.air_date, today),
            watched=episode.id in watched_ids,
        )
        for episode in episodes
    ]
