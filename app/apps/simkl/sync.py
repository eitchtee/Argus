"""Two-way synchronisation between Argus and a SIMKL account.

SIMKL's sync model is "one full pull, then deltas": every run first asks
``/sync/activities`` whether anything moved and only then fetches the changed
items with ``date_from``. Because a delta never says what disappeared, a local
mirror of the SIMKL library (:class:`SimklLibraryItem`) is kept so that
episodes unmarked or titles removed on SIMKL can be detected by diffing.

Watched state is the ground truth on whichever side holds it: anything watched
on one side and not the other is marked watched there. Unwatching only travels
through explicit actions -- a local unwatch queues a removal intent, a SIMKL
unwatch shows up as a diff against the mirror.
"""

from dataclasses import dataclass, field
from datetime import datetime

from cachalot.api import cachalot_disabled
from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from django.utils import timezone

from apps.catalog.localization import PROVIDER_DEFAULT_LANGUAGES
from apps.catalog.models import MediaRating
from apps.catalog.providers.exceptions import ProviderError
from apps.catalog.providers.registry import get_provider
from apps.movies import services as movie_services
from apps.movies.models import Movie, UserMovie
from apps.simkl.changes import simkl_rating_from_score
from apps.simkl.client import SimklClient
from apps.simkl.identities import (
    episode_key,
    history_item,
    identity_key_for_payload,
    media_identity_key,
    media_tokens,
    normalize_ids,
    parse_episode_key,
    parse_timestamp,
    serialize_timestamp,
    simkl_ids,
)
from apps.simkl.models import SimklAccount, SimklLibraryItem, SimklSyncIntent
from apps.sync.changes import suppress_local_intents
from apps.sync.identities import (
    episode_payload as intent_episode_payload,
    movie_payload as intent_movie_payload,
    show_payload as intent_show_payload,
)
from apps.sync.library import (
    WatchedEpisode as _EpisodeRequest,
    _apply_rating,
    _collect_local_snapshot,
    _find_by_ids,
    _import_with_provider_fallback,
    _mark_episodes,
    _normalize_movie_title,
    _normalize_show_title,
    _save_media_ids,
    _unmark_episodes,
)
from apps.tv import services as tv_services
from apps.tv.models import Show, UserEpisode, UserShow


COMPLETED = "completed"
WATCHING = "watching"
PLANTOWATCH = "plantowatch"
HOLD = "hold"
DROPPED = "dropped"

TYPE_TO_MEDIA = {"shows": "show", "movies": "movie", "anime": "anime"}
ACTIVITY_DOMAINS = {"tv_shows": "shows", "movies": "movies", "anime": "anime"}
SHOW_MEDIA_TYPES = {"show", "anime"}
# Refusing to mirror a wipe protects the local library from an empty or
# truncated ids-only response.
REMOVAL_GUARD_MIN_ITEMS = 20

Kind = SimklSyncIntent.Kind


@dataclass(frozen=True)
class RemoteItem:
    media_type: str
    simkl_id: int
    ids: dict
    title: str
    year: int | None
    status: str
    last_watched_at: datetime | None
    added_at: datetime | None
    user_rating: int | None
    episodes: dict[str, datetime | None]
    has_episode_data: bool
    watched_episodes_count: int
    total_episodes_count: int

    @property
    def key(self) -> tuple[str, int]:
        return (self.media_type, self.simkl_id)

    @property
    def is_show(self) -> bool:
        return self.media_type in SHOW_MEDIA_TYPES


@dataclass(frozen=True)
class CachedState:
    status: str
    watched_episodes: dict
    user_rating: int | None


@dataclass
class RemoteChanges:
    items: dict[tuple[str, int], RemoteItem] = field(default_factory=dict)
    removed: dict[tuple[str, int], SimklLibraryItem] = field(default_factory=dict)
    full: bool = False
    warnings: list[str] = field(default_factory=list)


@dataclass
class Outbound:
    history_add_movies: list[dict] = field(default_factory=list)
    history_add_shows: dict[str, dict] = field(default_factory=dict)
    history_remove_movies: list[dict] = field(default_factory=list)
    history_remove_shows: dict[str, dict] = field(default_factory=dict)
    list_movies: list[dict] = field(default_factory=list)
    list_shows: list[dict] = field(default_factory=list)
    ratings_add_movies: list[dict] = field(default_factory=list)
    ratings_add_shows: list[dict] = field(default_factory=list)
    ratings_remove_movies: list[dict] = field(default_factory=list)
    ratings_remove_shows: list[dict] = field(default_factory=list)
    # identity key -> pending-push record, for everything sent from local state
    pending: dict[str, dict] = field(default_factory=dict)

    @property
    def count(self) -> int:
        return (
            len(self.history_add_movies)
            + sum(_episode_count(item) for item in self.history_add_shows.values())
            + len(self.history_remove_movies)
            + sum(max(1, _episode_count(item)) for item in self.history_remove_shows.values())
            + len(self.list_movies)
            + len(self.list_shows)
            + len(self.ratings_add_movies)
            + len(self.ratings_add_shows)
            + len(self.ratings_remove_movies)
            + len(self.ratings_remove_shows)
        )


@dataclass
class SyncReport:
    skipped: bool = False
    movies_imported: int = 0
    shows_imported: int = 0
    episodes_marked: int = 0
    episodes_unmarked: int = 0
    ratings_applied: int = 0
    items_pushed: int = 0
    warnings: list[str] = field(default_factory=list)


def sync_account(account_id: int, *, client_factory=None) -> SyncReport:
    account = SimklAccount.objects.select_related("user").get(id=account_id)
    client = _build_client(account, client_factory=client_factory)
    user = account.user
    report = SyncReport()
    initial = not account.initial_sync_complete or not account.activities_cursor

    with cachalot_disabled():
        intents = list(
            SimklSyncIntent.objects.filter(user=user).order_by("updated_at", "id")
        )
        if initial:
            _refresh_profile(account, client)
            changes = fetch_full_library(client)
            activities = client.get_activities()
        else:
            activities = client.get_activities()
            previous_activities = account.last_activities or {}
            settings_block = activities.get("settings") or {}
            if settings_block.get("all") != (previous_activities.get("settings") or {}).get("all"):
                _refresh_profile(account, client)
            if (activities.get("all") or "") != account.activities_cursor:
                changes = fetch_changes(client, account, activities)
            else:
                changes = RemoteChanges()
        report.warnings.extend(changes.warnings)

        previous = {
            (row.media_type, row.simkl_id): CachedState(
                status=row.status,
                watched_episodes=dict(row.watched_episodes or {}),
                user_rating=row.user_rating,
            )
            for row in account.library_items.all()
        }
        cache = _merge_cache(account, changes)
        _prune_pending_pushes(account, cache)

        with suppress_local_intents():
            _apply_remote(
                user,
                changes,
                previous,
                cache,
                intents,
                report,
                initial=initial,
            )

        local = _collect_local_snapshot(user)
        outbound = build_outbound(
            user,
            local,
            cache,
            intents,
            pending_pushes=account.pending_pushes or {},
            initial=initial,
        )
        try:
            report.items_pushed = _push(client, account, outbound, intents, report)
        except Exception:
            # Whatever did get through is remembered so it is not re-sent on
            # the retry; the cursor stays put so the echo is still fetched.
            SimklAccount.objects.filter(id=account.id).update(
                pending_pushes=account.pending_pushes,
                updated_at=timezone.now(),
            )
            raise

        SimklAccount.objects.filter(id=account.id).update(
            activities_cursor=(activities.get("all") or account.activities_cursor or ""),
            last_activities=activities,
            initial_sync_complete=True,
            pending_pushes=account.pending_pushes,
            last_warning="\n".join(report.warnings[:10]),
            updated_at=timezone.now(),
        )
    return report


# -- Remote reads ------------------------------------------------------------


def fetch_full_library(client) -> RemoteChanges:
    """Phase 1: pull the whole library, one type at a time as SIMKL asks."""
    changes = RemoteChanges(full=True)
    for media_type, extended in (
        ("shows", "full"),
        ("movies", None),
        ("anime", "full_anime_seasons"),
    ):
        payload = client.get_all_items(
            media_type,
            extended=extended,
            episode_watched_at=extended is not None,
            include_all_episodes=extended is not None,
        )
        changes.items.update(normalize_items(payload))
    return changes


def fetch_changes(client, account, activities: dict) -> RemoteChanges:
    """Phase 2: one combined ``date_from`` delta across every type.

    SIMKL asks continuous syncs to use the bare ``/sync/all-items`` endpoint
    so all types travel in a single request. Anime episodes need a second,
    anime-only read to carry their TVDB coordinates; it is made only when the
    delta actually brought anime with episode data.
    """
    changes = RemoteChanges()
    previous = account.last_activities or {}
    payload = client.get_all_items(
        None,
        date_from=account.activities_cursor,
        extended="full",
        episode_watched_at=True,
        include_all_episodes=True,
    )
    changes.items.update(normalize_items(payload))
    if any(
        item.media_type == "anime" and item.has_episode_data and item.episodes
        for item in changes.items.values()
    ):
        anime_payload = client.get_all_items(
            "anime",
            date_from=account.activities_cursor,
            extended="full_anime_seasons",
            episode_watched_at=True,
            include_all_episodes=True,
        )
        changes.items.update(normalize_items(anime_payload))

    removed_types = []
    for domain, media_type in ACTIVITY_DOMAINS.items():
        block = activities.get(domain) or {}
        previous_block = previous.get(domain) or {}
        removed_at = block.get("removed_from_list")
        if removed_at and removed_at != previous_block.get("removed_from_list"):
            removed_types.append(media_type)

    for media_type in removed_types:
        payload = client.get_all_items(media_type, extended="ids_only")
        present = {
            simkl_id
            for _media_type, _media, _entry, simkl_id in _iter_entries(payload)
        }
        rows = list(
            account.library_items.filter(media_type=TYPE_TO_MEDIA[media_type])
        )
        gone = [row for row in rows if row.simkl_id not in present]
        if len(gone) > REMOVAL_GUARD_MIN_ITEMS and len(gone) * 2 > len(rows):
            changes.warnings.append(
                f"SIMKL reported {len(gone)} of {len(rows)} {media_type} removed; "
                "ignoring the removal as a safety measure."
            )
            continue
        for row in gone:
            changes.removed[(row.media_type, row.simkl_id)] = row
    return changes


def _iter_entries(payload):
    if not isinstance(payload, dict):
        return
    for type_name, media_type in TYPE_TO_MEDIA.items():
        for entry in payload.get(type_name) or []:
            if not isinstance(entry, dict):
                continue
            media = entry.get("show") or entry.get("movie") or entry.get("anime")
            if not isinstance(media, dict):
                continue
            ids = normalize_ids(media.get("ids"))
            try:
                simkl_id = int(ids["simkl"])
            except (KeyError, ValueError):
                continue
            yield media_type, media, entry, simkl_id


def normalize_items(payload) -> dict[tuple[str, int], RemoteItem]:
    items: dict[tuple[str, int], RemoteItem] = {}
    for media_type, media, entry, simkl_id in _iter_entries(payload):
        ids = normalize_ids(media.get("ids"))
        last_watched_at = parse_timestamp(entry.get("last_watched_at"))
        seasons = entry.get("seasons")
        flat_episodes = entry.get("episodes")
        has_episode_data = isinstance(seasons, list) or isinstance(flat_episodes, list)
        mapped = entry.get("mapped_tvdb_seasons") or []
        default_season = _as_int(mapped[0], default=1) if len(mapped) == 1 else None
        episodes: dict[str, datetime | None] = {}

        def add_episode(episode: dict, season_number: int | None):
            if not isinstance(episode, dict):
                return
            if episode.get("watched") is False:
                return
            tvdb = episode.get("tvdb")
            if (
                isinstance(tvdb, dict)
                and tvdb.get("season") is not None
                and tvdb.get("episode") is not None
            ):
                season_value = _as_int(tvdb.get("season"), default=-1)
                episode_value = _as_int(tvdb.get("episode"), default=0)
            else:
                if season_number is None:
                    season_number = default_season if default_season is not None else 1
                season_value = season_number
                episode_value = _as_int(episode.get("number"), default=0)
            if season_value < 0 or episode_value <= 0:
                return
            watched_at = parse_timestamp(episode.get("watched_at")) or last_watched_at
            episodes[episode_key(season_value, episode_value)] = watched_at

        for season in seasons or []:
            if not isinstance(season, dict):
                continue
            season_number = _as_int(season.get("number"), default=-1)
            if media_type == "anime" and default_season is not None:
                season_number = None
            elif season_number < 0:
                season_number = None
            for episode in season.get("episodes") or []:
                add_episode(episode, season_number)
        for episode in flat_episodes or []:
            add_episode(episode, None)

        items[(media_type, simkl_id)] = RemoteItem(
            media_type=media_type,
            simkl_id=simkl_id,
            ids=ids,
            title=str(media.get("title") or ""),
            year=_as_int(media.get("year"), default=0) or None,
            status=str(entry.get("status") or ""),
            last_watched_at=last_watched_at,
            added_at=parse_timestamp(entry.get("added_to_watchlist_at")),
            user_rating=_as_int(entry.get("user_rating"), default=0) or None,
            episodes=episodes,
            has_episode_data=has_episode_data,
            watched_episodes_count=_as_int(entry.get("watched_episodes_count"), default=0),
            total_episodes_count=_as_int(entry.get("total_episodes_count"), default=0),
        )
    return items


# -- Library mirror ----------------------------------------------------------


def _merge_cache(account, changes: RemoteChanges) -> dict[tuple[str, int], SimklLibraryItem]:
    rows = {(row.media_type, row.simkl_id): row for row in account.library_items.all()}
    to_create = []
    to_update = []
    for key, item in changes.items.items():
        row = rows.get(key)
        if item.has_episode_data:
            watched_episodes = {
                episode: serialize_timestamp(watched_at) if watched_at else None
                for episode, watched_at in item.episodes.items()
            }
        elif row is not None:
            watched_episodes = row.watched_episodes or {}
        else:
            watched_episodes = {}
        if row is None:
            row = SimklLibraryItem(
                account=account,
                media_type=item.media_type,
                simkl_id=item.simkl_id,
            )
            to_create.append(row)
        else:
            to_update.append(row)
        row.ids = item.ids
        row.title = item.title[:255]
        row.year = item.year
        row.status = item.status
        row.last_watched_at = item.last_watched_at
        row.user_rating = item.user_rating
        row.watched_episodes = watched_episodes
        row.watched_episodes_count = item.watched_episodes_count
        row.total_episodes_count = item.total_episodes_count
        rows[key] = row
    if to_create:
        SimklLibraryItem.objects.bulk_create(to_create, batch_size=500)
    if to_update:
        now = timezone.now()
        for row in to_update:
            row.updated_at = now
        SimklLibraryItem.objects.bulk_update(
            to_update,
            [
                "ids",
                "title",
                "year",
                "status",
                "last_watched_at",
                "user_rating",
                "watched_episodes",
                "watched_episodes_count",
                "total_episodes_count",
                "updated_at",
            ],
            batch_size=500,
        )
    removed_ids = [row.id for row in changes.removed.values() if row.id is not None]
    if removed_ids:
        SimklLibraryItem.objects.filter(id__in=removed_ids).delete()
    for key in changes.removed:
        rows.pop(key, None)
    return rows


def _prune_pending_pushes(account, cache) -> None:
    """Forget pushes SIMKL has echoed back through the library mirror."""
    pending = dict(account.pending_pushes or {})
    if not pending:
        return
    index = _token_index(cache.values())
    for key, record in list(pending.items()):
        if not isinstance(record, dict) or record.get("reason") == "not_found":
            continue
        rows = _rows_for_tokens(set(record.get("tokens") or []), index)
        if not rows:
            continue
        episode = record.get("episode")
        if episode and not any(episode in (row.watched_episodes or {}) for row in rows):
            continue
        pending.pop(key)
    account.pending_pushes = pending


def _token_index(rows) -> dict[str, list[SimklLibraryItem]]:
    index: dict[str, list[SimklLibraryItem]] = {}
    for row in rows:
        for token in media_tokens(row.ids):
            index.setdefault(token, []).append(row)
    return index


def _rows_for_tokens(tokens: set[str], index) -> list[SimklLibraryItem]:
    seen: dict[int, SimklLibraryItem] = {}
    for token in tokens:
        for row in index.get(token, []):
            seen[id(row)] = row
    return list(seen.values())


# -- Remote -> local ---------------------------------------------------------


def _apply_remote(user, changes, previous, cache, intents, report, *, initial: bool):
    _apply_remote_movies(user, changes, previous, intents, report)
    _apply_remote_shows(user, changes, previous, cache, intents, report, initial=initial)


def _apply_remote_movies(user, changes, previous, intents, report):
    movie_type = ContentType.objects.get_for_model(Movie)
    for item in changes.items.values():
        if item.media_type != "movie":
            continue
        try:
            movie, created = _ensure_movie(user, item)
        except (ProviderError, ValueError) as exc:
            report.warnings.append(f"Movie import failed ({item.title or item.simkl_id}): {exc}")
            continue
        if created:
            report.movies_imported += 1
        tokens = media_tokens(item.ids) | _object_tokens(movie)
        prev = previous.get(item.key)
        state = UserMovie.objects.filter(user=user, movie=movie).first()
        history_pending = _pending_desired(intents, Kind.MOVIE_HISTORY, tokens)
        watchlist_pending = _pending_desired(intents, Kind.MOVIE_WATCHLIST, tokens)
        was_completed = prev is not None and prev.status == COMPLETED

        if item.status == COMPLETED:
            if history_pending is False:
                pass
            else:
                state = state or UserMovie(user=user, movie=movie)
                watched_at = item.last_watched_at or timezone.now()
                state.is_seen = True
                if state.seen_at is None or watched_at > state.seen_at:
                    state.seen_at = watched_at
                state.on_watchlist = False
                state.watchlist_added_at = None
                state.save()
        elif item.status == PLANTOWATCH:
            if was_completed and state is not None and state.is_seen and history_pending is not True:
                state.is_seen = False
                state.seen_at = None
            if watchlist_pending is not False and (state is None or not state.is_seen):
                state = state or UserMovie(user=user, movie=movie)
                if not state.on_watchlist:
                    state.on_watchlist = True
                    state.watchlist_added_at = item.added_at or timezone.now()
            if state is not None:
                state.save()
        elif item.status == DROPPED:
            if state is not None:
                if was_completed and state.is_seen and history_pending is not True:
                    state.is_seen = False
                    state.seen_at = None
                if state.on_watchlist and watchlist_pending is not True:
                    state.on_watchlist = False
                    state.watchlist_added_at = None
                state.save()

        _apply_rating(
            user,
            movie,
            movie_type,
            MediaRating.MediaType.MOVIE,
            rating=item.user_rating,
            previous_rating=prev.user_rating if prev else None,
            pending=_pending_desired(intents, Kind.MOVIE_RATING, tokens),
            rateable=bool(state and state.is_seen),
            report=report,
        )

    for key, row in changes.removed.items():
        if row.media_type != "movie":
            continue
        movie = _find_by_ids(Movie, {"ids": row.ids}, user=user, user_state_relation="user_states")
        if movie is None:
            continue
        tokens = media_tokens(row.ids) | _object_tokens(movie)
        state = UserMovie.objects.filter(user=user, movie=movie).first()
        if state is not None:
            if state.is_seen and _pending_desired(intents, Kind.MOVIE_HISTORY, tokens) is not True:
                state.is_seen = False
                state.seen_at = None
            if state.on_watchlist and _pending_desired(intents, Kind.MOVIE_WATCHLIST, tokens) is not True:
                state.on_watchlist = False
                state.watchlist_added_at = None
            state.save()
        if _pending_desired(intents, Kind.MOVIE_RATING, tokens) is not True:
            MediaRating.objects.filter(user=user, content_type=movie_type, object_id=movie.pk).delete()


def _apply_remote_shows(user, changes, previous, cache, intents, report, *, initial: bool):
    show_type = ContentType.objects.get_for_model(Show)
    grouped: dict[int, list[RemoteItem]] = {}
    shows: dict[int, Show] = {}
    for item in changes.items.values():
        if not item.is_show:
            continue
        try:
            show, created = _ensure_show(user, item)
        except (ProviderError, ValueError) as exc:
            report.warnings.append(f"Show import failed ({item.title or item.simkl_id}): {exc}")
            continue
        if created:
            report.shows_imported += 1
        shows[show.id] = show
        grouped.setdefault(show.id, []).append(item)

    user_shows = {
        user_show.show_id: user_show
        for user_show in UserShow.objects.filter(user=user, show_id__in=list(grouped))
    }
    local_episode_shows = set(
        UserEpisode.objects.filter(user=user, episode__show_id__in=list(grouped))
        .values_list("episode__show_id", flat=True)
        .distinct()
    )

    episode_requests: list[tuple[_EpisodeRequest, Show]] = []
    episode_removals: list[tuple[Show, int, int]] = []

    for show_id, items in grouped.items():
        show = shows[show_id]
        tokens = _object_tokens(show)
        for item in items:
            tokens |= media_tokens(item.ids)
        remote_status = _combined_status([item.status for item in items])
        status_changed = initial or any(
            previous.get(item.key) is None or previous[item.key].status != item.status
            for item in items
        )
        dropped_pending = _pending_desired(intents, Kind.SHOW_DROPPED, tokens)
        paused_pending = _pending_desired(intents, Kind.SHOW_PAUSED, tokens)
        watchlist_pending = _pending_desired(intents, Kind.SHOW_WATCHLIST, tokens)
        target = _local_status(remote_status)

        user_show = user_shows.get(show_id)
        if user_show is None:
            user_show = UserShow.objects.create(
                user=user,
                show=show,
                status=target,
                on_watchlist=remote_status == PLANTOWATCH,
            )
            user_shows[show_id] = user_show
        elif status_changed:
            sticky = initial and user_show.status in {
                UserShow.Status.PAUSED,
                UserShow.Status.DROPPED,
            }
            blocked = (
                (target == UserShow.Status.DROPPED and dropped_pending is False)
                or (target == UserShow.Status.PAUSED and paused_pending is False)
                or (
                    target == UserShow.Status.TRACKED
                    and (dropped_pending is True or paused_pending is True)
                )
            )
            if not sticky and not blocked and user_show.status != target:
                user_show.status = target
                user_show.save(update_fields=["status", "updated_at"])

        remote_episode_keys: set[str] = set()
        for item in items:
            remote_episode_keys |= set(item.episodes)

        if remote_status == PLANTOWATCH:
            if (
                watchlist_pending is not False
                and user_show.status == UserShow.Status.TRACKED
                and show_id not in local_episode_shows
                and not remote_episode_keys
                and not user_show.on_watchlist
            ):
                user_show.on_watchlist = True
                user_show.save(update_fields=["on_watchlist", "updated_at"])
        elif user_show.on_watchlist and watchlist_pending is not True and (
            status_changed or remote_episode_keys
        ):
            user_show.on_watchlist = False
            user_show.save(update_fields=["on_watchlist", "updated_at"])

        for item in items:
            for key, watched_at in item.episodes.items():
                season_number, episode_number = parse_episode_key(key)
                if _pending_episode_desired(intents, tokens, season_number, episode_number) is False:
                    continue
                episode_requests.append(
                    (
                        _EpisodeRequest(
                            show={"ids": item.ids},
                            episode={},
                            season_number=season_number,
                            episode_number=episode_number,
                            watched_at=watched_at or item.last_watched_at or timezone.now(),
                        ),
                        show,
                    )
                )
            prev = previous.get(item.key)
            if prev is None or not prev.watched_episodes:
                continue
            reset = item.status == PLANTOWATCH and item.watched_episodes_count == 0
            if not item.has_episode_data and not reset:
                continue
            for key in set(prev.watched_episodes) - remote_episode_keys:
                season_number, episode_number = parse_episode_key(key)
                if _pending_episode_desired(intents, tokens, season_number, episode_number) is True:
                    continue
                episode_removals.append((show, season_number, episode_number))

        rating = next((item.user_rating for item in items if item.user_rating), None)
        previous_rating = next(
            (
                previous[item.key].user_rating
                for item in items
                if previous.get(item.key) is not None and previous[item.key].user_rating
            ),
            None,
        )
        _apply_rating(
            user,
            show,
            show_type,
            MediaRating.MediaType.SHOW,
            rating=rating,
            previous_rating=previous_rating,
            pending=_pending_desired(intents, Kind.SHOW_RATING, tokens),
            rateable=True,
            report=report,
        )

    _mark_episodes(user, episode_requests, report)
    _unmark_episodes(user, episode_removals, report)

    remaining_index = _token_index(cache.values())
    for key, row in changes.removed.items():
        if row.media_type not in SHOW_MEDIA_TYPES:
            continue
        show = _find_by_ids(Show, {"ids": row.ids}, user=user, user_state_relation="user_states")
        if show is None:
            continue
        tokens = media_tokens(row.ids) | _object_tokens(show)
        removals = []
        for episode_key_value in row.watched_episodes or {}:
            season_number, episode_number = parse_episode_key(episode_key_value)
            if _pending_episode_desired(intents, tokens, season_number, episode_number) is True:
                continue
            removals.append((show, season_number, episode_number))
        _unmark_episodes(user, removals, report)
        still_remote = bool(_rows_for_tokens(tokens, remaining_index))
        user_show = UserShow.objects.filter(user=user, show=show).first()
        if user_show is not None:
            if user_show.on_watchlist and _pending_desired(intents, Kind.SHOW_WATCHLIST, tokens) is not True:
                user_show.on_watchlist = False
                user_show.save(update_fields=["on_watchlist", "updated_at"])
            if (
                not still_remote
                and not UserEpisode.objects.filter(user=user, episode__show=show).exists()
                and _pending_desired(intents, Kind.SHOW_WATCHLIST, tokens) is not True
            ):
                user_show.delete()
        if _pending_desired(intents, Kind.SHOW_RATING, tokens) is not True:
            MediaRating.objects.filter(user=user, content_type=show_type, object_id=show.pk).delete()


# -- Catalog resolution ------------------------------------------------------


def _ensure_movie(user, item: RemoteItem) -> tuple[Movie, bool]:
    media = {"title": item.title, "year": item.year, "ids": dict(item.ids)}
    movie = _find_by_ids(Movie, media, user=user, user_state_relation="user_states")
    created = movie is None
    if movie is None:
        ids = dict(item.ids)
        if not ids.get("tmdb") and ids.get("imdb"):
            tmdb_id = _tmdb_id_from_imdb(ids["imdb"], "movie")
            if tmdb_id:
                ids["tmdb"] = tmdb_id
        movie = _import_with_provider_fallback(
            ids,
            ("tmdb", "tvdb"),
            lambda provider, external_id: movie_services.import_movie(
                provider,
                external_id,
                language=PROVIDER_DEFAULT_LANGUAGES[provider],
            ),
        )
        if movie is None:
            raise ValueError("SIMKL movie has no TMDB or TVDB identifier")
    _normalize_movie_title(movie, media)
    _save_media_ids(movie, _identity_media(item, movie))
    return movie, created


def _ensure_show(user, item: RemoteItem) -> tuple[Show, bool]:
    media = {"title": item.title, "year": item.year, "ids": dict(item.ids)}
    show = _find_by_ids(Show, media, user=user, user_state_relation="user_states")
    created = show is None
    if show is None:
        ids = dict(item.ids)
        if not ids.get("tvdb") and not ids.get("tmdb") and ids.get("imdb"):
            tmdb_id = _tmdb_id_from_imdb(ids["imdb"], "tv")
            if tmdb_id:
                ids["tmdb"] = tmdb_id
        show = _import_with_provider_fallback(
            ids,
            ("tvdb", "tmdb"),
            lambda provider, external_id: tv_services.import_show(
                external_id,
                provider=provider,
                language=PROVIDER_DEFAULT_LANGUAGES[provider],
            ),
        )
        if show is None:
            raise ValueError("SIMKL show has no TVDB or TMDB identifier")
    if item.media_type != "anime":
        # Anime cours share their parent's TVDB/TMDB ids but not its title;
        # never let a cour rename the show.
        _normalize_show_title(show, media)
    _save_media_ids(show, _identity_media(item, show))
    return show, created


def _identity_media(item: RemoteItem, obj) -> dict:
    """Ids worth persisting on the local record: only the providers Argus
    tracks, and only when the record does not already carry them."""
    ids = {
        key: value
        for key, value in item.ids.items()
        if key in {"imdb", "tmdb", "tvdb"} and not getattr(obj, f"{key}_id", None)
    }
    return {"ids": ids}


def _tmdb_id_from_imdb(imdb_id: str, media_type: str) -> str | None:
    try:
        return get_provider("tmdb").find_by_imdb_id(imdb_id, media_type)
    except (ProviderError, ValueError, AttributeError):
        return None


# -- Local -> remote ---------------------------------------------------------


def build_outbound(
    user,
    local,
    cache,
    intents,
    *,
    pending_pushes: dict,
    initial: bool,
) -> Outbound:
    outbound = Outbound()
    index = _token_index(cache.values())

    def movie_row(tokens):
        rows = [row for row in _rows_for_tokens(tokens, index) if row.media_type == "movie"]
        return rows[0] if rows else None

    def show_rows(tokens):
        return [row for row in _rows_for_tokens(tokens, index) if row.media_type in SHOW_MEDIA_TYPES]

    def remote_episode_keys(rows) -> set[str]:
        keys: set[str] = set()
        for row in rows:
            keys |= set(row.watched_episodes or {})
        return keys

    def queue(identity, tokens, *, episode=None):
        outbound.pending[identity] = {
            "at": serialize_timestamp(timezone.now()),
            "tokens": sorted(tokens),
            "episode": episode,
            "reason": "pushed",
        }

    def skipped(identity) -> bool:
        return identity in pending_pushes

    tracked_show_ids = set(
        UserShow.objects.filter(user=user).values_list("show_id", flat=True)
    )
    tracked_show_tokens: set[str] = set()
    for show in Show.objects.filter(id__in=tracked_show_ids).only(
        "id", "imdb_id", "tmdb_id", "tvdb_id"
    ):
        tracked_show_tokens |= _object_tokens(show)
    local_episode_show_ids = {state.episode.show_id for state in local.episode_history}
    seen_movie_ids = {state.movie_id for state in local.movie_history}

    # -- Projection of local watched state (the union rule) ------------------
    for state in local.movie_history:
        payload = intent_movie_payload(state.movie, watched_at=state.seen_at)
        tokens = media_tokens(payload["ids"])
        if not tokens:
            continue
        identity = media_identity_key(payload["ids"])
        row = movie_row(tokens)
        if row is not None and row.status == COMPLETED:
            continue
        if skipped(identity):
            continue
        _append_unique(outbound.history_add_movies, history_item(payload, "movie"))
        queue(identity, tokens)

    for state in local.episode_history:
        episode = state.episode
        payload = intent_episode_payload(episode, watched_at=state.seen_at)
        tokens = media_tokens(payload["show"]["ids"])
        if not tokens:
            continue
        key = episode_key(episode.season_number, episode.episode_number)
        if key in remote_episode_keys(show_rows(tokens)):
            continue
        identity = identity_key_for_payload(Kind.EPISODE_HISTORY, payload)
        if skipped(identity):
            continue
        _add_episode(outbound.history_add_shows, payload)
        queue(identity, tokens, episode=key)

    if initial:
        for state in local.movie_watchlist:
            payload = intent_movie_payload(state.movie)
            tokens = media_tokens(payload["ids"])
            if not tokens or movie_row(tokens) is not None:
                continue
            identity = f"{media_identity_key(payload['ids'])}#list"
            if skipped(identity):
                continue
            _append_unique(outbound.list_movies, _list_item(payload, "movie", PLANTOWATCH))
            queue(identity, tokens)
        for state in local.show_watchlist:
            if state.status != UserShow.Status.TRACKED or state.show_id in local_episode_show_ids:
                continue
            payload = intent_show_payload(state.show)
            tokens = media_tokens(payload["ids"])
            if not tokens or show_rows(tokens):
                continue
            identity = f"{media_identity_key(payload['ids'])}#list"
            if skipped(identity):
                continue
            _append_unique(outbound.list_shows, _list_item(payload, "show", PLANTOWATCH))
            queue(identity, tokens)
        for state in local.show_dropped:
            payload = intent_show_payload(state.show)
            tokens = media_tokens(payload["ids"])
            if not tokens or _combined_status([row.status for row in show_rows(tokens)]) == DROPPED:
                continue
            identity = f"{media_identity_key(payload['ids'])}#list"
            if skipped(identity):
                continue
            _append_unique(outbound.list_shows, _list_item(payload, "show", DROPPED))
            queue(identity, tokens)
        for state in UserShow.objects.filter(user=user, status=UserShow.Status.PAUSED).select_related("show"):
            payload = intent_show_payload(state.show)
            tokens = media_tokens(payload["ids"])
            if not tokens or _combined_status([row.status for row in show_rows(tokens)]) == HOLD:
                continue
            identity = f"{media_identity_key(payload['ids'])}#list"
            if skipped(identity):
                continue
            _append_unique(outbound.list_shows, _list_item(payload, "show", HOLD))
            queue(identity, tokens)

    # -- Ratings projection (SIMKL has no episode ratings) --------------------
    movie_type = ContentType.objects.get_for_model(Movie)
    show_type = ContentType.objects.get_for_model(Show)
    ratings = list(
        MediaRating.objects.filter(user=user, content_type__in=[movie_type, show_type])
    )
    rated_movies = {
        movie.id: movie
        for movie in Movie.objects.filter(
            id__in=[rating.object_id for rating in ratings if rating.content_type_id == movie_type.id]
        ).only("id", "title", "release_date", "imdb_id", "tmdb_id", "tvdb_id", "trakt_id")
    }
    rated_shows = {
        show.id: show
        for show in Show.objects.filter(
            id__in=[rating.object_id for rating in ratings if rating.content_type_id == show_type.id]
        ).only("id", "name", "first_aired", "imdb_id", "tmdb_id", "tvdb_id", "trakt_id")
    }
    for rating in ratings:
        simkl_rating = simkl_rating_from_score(rating.score)
        if rating.content_type_id == movie_type.id:
            movie = rated_movies.get(rating.object_id)
            if movie is None or movie.id not in seen_movie_ids:
                continue
            payload = intent_movie_payload(movie)
            tokens = media_tokens(payload["ids"])
            row = movie_row(tokens)
            if not tokens or (row is not None and row.user_rating == simkl_rating):
                continue
            identity = f"{media_identity_key(payload['ids'])}#rating"
            if skipped(identity):
                continue
            _append_unique(
                outbound.ratings_add_movies,
                {**_bare_item(payload, "movie"), "rating": simkl_rating},
            )
            queue(identity, tokens)
        else:
            show = rated_shows.get(rating.object_id)
            if show is None or show.id not in tracked_show_ids:
                continue
            payload = intent_show_payload(show)
            tokens = media_tokens(payload["ids"])
            rows = show_rows(tokens)
            if not tokens or any(row.user_rating == simkl_rating for row in rows):
                continue
            identity = f"{media_identity_key(payload['ids'])}#rating"
            if skipped(identity):
                continue
            _append_unique(
                outbound.ratings_add_shows,
                {**_bare_item(payload, "show"), "rating": simkl_rating},
            )
            queue(identity, tokens)

    # -- Explicit local changes ----------------------------------------------
    for intent in intents:
        kind = intent.kind
        payload = intent.payload or {}
        desired = intent.desired
        if kind == Kind.EPISODE_HISTORY:
            show = payload.get("show") or {}
            tokens = media_tokens(show.get("ids"))
            if not tokens:
                continue
            remote = remote_episode_keys(show_rows(tokens))
            for season in payload.get("seasons") or []:
                for episode in season.get("episodes") or []:
                    key = episode_key(
                        _as_int(season.get("number"), default=0),
                        _as_int(episode.get("number"), default=0),
                    )
                    if desired and key not in remote:
                        _add_episode(outbound.history_add_shows, payload)
                    elif not desired and key in remote:
                        _add_episode(outbound.history_remove_shows, payload, strip_timestamps=True)
            continue

        media_type = "movie" if kind.startswith("movie_") else "show"
        media = payload.get(media_type) if isinstance(payload.get(media_type), dict) else payload
        tokens = media_tokens(media.get("ids"))
        if not tokens:
            continue

        if media_type == "movie":
            row = movie_row(tokens)
            status = row.status if row else None
            locally_seen = any(
                tokens & _object_tokens(state.movie) for state in local.movie_history
            )
            locally_listed = any(
                tokens & _object_tokens(state.movie) for state in local.movie_watchlist
            )
            if kind == Kind.MOVIE_WATCHLIST:
                if desired and status not in {PLANTOWATCH, COMPLETED} and not locally_seen:
                    _append_unique(outbound.list_movies, _list_item(payload, "movie", PLANTOWATCH))
                elif (
                    not desired
                    and status == PLANTOWATCH
                    and not locally_seen
                    and _pending_desired(intents, Kind.MOVIE_HISTORY, tokens) is not True
                ):
                    _append_unique(outbound.history_remove_movies, _bare_item(payload, "movie"))
            elif kind == Kind.MOVIE_HISTORY:
                if desired and status != COMPLETED:
                    _append_unique(outbound.history_add_movies, history_item(payload, "movie"))
                elif not desired and status == COMPLETED:
                    _append_unique(outbound.history_remove_movies, _bare_item(payload, "movie"))
                    if locally_listed:
                        _append_unique(
                            outbound.list_movies,
                            _list_item(payload, "movie", PLANTOWATCH),
                        )
            elif kind == Kind.MOVIE_RATING:
                rating = _as_int(payload.get("rating"), default=0)
                if desired and rating and (row is None or row.user_rating != rating):
                    _append_unique(
                        outbound.ratings_add_movies,
                        {**_bare_item(payload, "movie"), "rating": rating},
                    )
                elif not desired and row is not None and row.user_rating:
                    _append_unique(outbound.ratings_remove_movies, _bare_item(payload, "movie"))
            continue

        rows = show_rows(tokens)
        status = _combined_status([row.status for row in rows]) if rows else None
        has_remote_episodes = bool(remote_episode_keys(rows))
        locally_has_episodes = any(
            tokens & _object_tokens(state.episode.show) for state in local.episode_history
        )
        locally_tracked = bool(tokens & tracked_show_tokens) or locally_has_episodes
        resume_status = WATCHING if has_remote_episodes else PLANTOWATCH

        if kind == Kind.SHOW_WATCHLIST:
            if desired and not rows and not locally_has_episodes:
                _append_unique(outbound.list_shows, _list_item(payload, "show", PLANTOWATCH))
            elif not desired and status == PLANTOWATCH and not locally_tracked:
                _append_unique(outbound.history_remove_shows, _bare_item(payload, "show"))
        elif kind == Kind.SHOW_DROPPED:
            if desired and status != DROPPED:
                _append_unique(outbound.list_shows, _list_item(payload, "show", DROPPED))
            elif (
                not desired
                and status == DROPPED
                and _pending_desired(intents, Kind.SHOW_PAUSED, tokens) is not True
            ):
                _append_unique(outbound.list_shows, _list_item(payload, "show", resume_status))
        elif kind == Kind.SHOW_PAUSED:
            if desired and status != HOLD:
                _append_unique(outbound.list_shows, _list_item(payload, "show", HOLD))
            elif (
                not desired
                and status == HOLD
                and _pending_desired(intents, Kind.SHOW_DROPPED, tokens) is not True
            ):
                _append_unique(outbound.list_shows, _list_item(payload, "show", resume_status))
        elif kind == Kind.SHOW_RATING:
            rating = _as_int(payload.get("rating"), default=0)
            if desired and rating and not any(row.user_rating == rating for row in rows):
                _append_unique(
                    outbound.ratings_add_shows,
                    {**_bare_item(payload, "show"), "rating": rating},
                )
            elif not desired and any(row.user_rating for row in rows):
                _append_unique(outbound.ratings_remove_shows, _bare_item(payload, "show"))

    return outbound


def _push(client, account, outbound: Outbound, intents, report) -> int:
    """Send the batched writes in dependency order and settle the bookkeeping."""
    sent = 0
    not_found: list[dict] = []

    def collect_not_found(response, media_types=("movies", "shows")):
        block = (response or {}).get("not_found") or {}
        for media_type in media_types:
            for item in block.get(media_type) or []:
                if isinstance(item, dict):
                    not_found.append(item)

    # Removals first: a movie unwatched locally but still on the watchlist is
    # removed from the SIMKL library and re-added as plan-to-watch afterwards.
    if outbound.history_remove_movies or outbound.history_remove_shows:
        response = client.remove_from_history(
            {
                "movies": outbound.history_remove_movies,
                "shows": list(outbound.history_remove_shows.values()),
            }
        )
        collect_not_found(response)
        sent += len(outbound.history_remove_movies) + len(outbound.history_remove_shows)
    if outbound.history_add_movies or outbound.history_add_shows:
        response = client.add_to_history(
            {
                "movies": outbound.history_add_movies,
                "shows": list(outbound.history_add_shows.values()),
            }
        )
        collect_not_found(response)
        for status in ((response or {}).get("added") or {}).get("statuses") or []:
            if not isinstance(status, dict):
                continue
            request = status.get("request") or {}
            resolved = (status.get("response") or {}).get("status")
            if request.get("type") == "movie" and resolved and resolved != COMPLETED:
                # SIMKL will not file an unreleased movie as watched; remember
                # it so the projection does not retry every run.
                _remember_push(account, request, reason="not_found", suffixes=("",))
                report.warnings.append(
                    f"SIMKL filed {request.get('title') or 'a movie'} as {resolved} instead of watched."
                )
        sent += len(outbound.history_add_movies) + len(outbound.history_add_shows)
    if outbound.list_movies or outbound.list_shows:
        response = client.add_to_list(
            {"movies": outbound.list_movies, "shows": outbound.list_shows}
        )
        collect_not_found(response)
        sent += len(outbound.list_movies) + len(outbound.list_shows)
    if outbound.ratings_remove_movies or outbound.ratings_remove_shows:
        response = client.remove_ratings(
            {"movies": outbound.ratings_remove_movies, "shows": outbound.ratings_remove_shows}
        )
        collect_not_found(response)
        sent += len(outbound.ratings_remove_movies) + len(outbound.ratings_remove_shows)
    if outbound.ratings_add_movies or outbound.ratings_add_shows:
        response = client.add_ratings(
            {"movies": outbound.ratings_add_movies, "shows": outbound.ratings_add_shows}
        )
        collect_not_found(response)
        sent += len(outbound.ratings_add_movies) + len(outbound.ratings_add_shows)

    pending = dict(account.pending_pushes or {})
    pending.update(outbound.pending)
    account.pending_pushes = pending
    for item in not_found:
        _remember_push(account, item, reason="not_found")
        title = item.get("title") or ", ".join(
            f"{key} {value}" for key, value in (item.get("ids") or {}).items()
        )
        report.warnings.append(f"SIMKL could not match {title or 'an item'}.")
    for intent in intents:
        _delete_intent_if_unchanged(intent)
    return sent


def _remember_push(
    account,
    item: dict,
    *,
    reason: str,
    suffixes: tuple[str, ...] = ("", "#list", "#rating"),
) -> None:
    ids = normalize_ids(item.get("ids"))
    tokens = media_tokens(ids)
    if not tokens:
        return
    pending = dict(account.pending_pushes or {})
    record = {
        "at": serialize_timestamp(timezone.now()),
        "tokens": sorted(tokens),
        "episode": None,
        "reason": reason,
    }
    identity = media_identity_key(ids, title=item.get("title", ""), year=item.get("year"))
    # Every key the projection might derive for this title: the bare identity
    # (history) plus the list and rating variants.
    for suffix in suffixes:
        pending[f"{identity}{suffix}"] = record
    for season in item.get("seasons") or []:
        for episode in season.get("episodes") or []:
            key = (
                f"episode:{identity}:s{_as_int(season.get('number'), default=0)}"
                f":e{_as_int(episode.get('number'), default=0)}"
            )
            pending[key] = {**record, "episode": episode_key(
                _as_int(season.get("number"), default=0),
                _as_int(episode.get("number"), default=0),
            )}
    account.pending_pushes = pending


def _delete_intent_if_unchanged(intent) -> None:
    SimklSyncIntent.objects.filter(id=intent.id, updated_at=intent.updated_at).delete()


# -- Helpers -----------------------------------------------------------------


def _refresh_profile(account, client) -> None:
    profile = client.get_user_settings() or {}
    user_block = profile.get("user") or {}
    account_block = profile.get("account") or {}
    account.simkl_username = str(user_block.get("name") or "")[:255]
    account.simkl_user_id = str(account_block.get("id") or "")[:32]
    account.account_type = str(account_block.get("type") or "")[:16]
    account.save(
        update_fields=["simkl_username", "simkl_user_id", "account_type", "updated_at"]
    )


def _build_client(account, *, client_factory=None):
    if client_factory is not None:
        return client_factory(account)
    return SimklClient(
        account.access_token,
        client_id=settings.SIMKL_CLIENT_ID,
        client_secret=settings.SIMKL_CLIENT_SECRET,
        app_name=settings.SIMKL_APP_NAME,
        app_version=settings.SIMKL_APP_VERSION,
    )


def _combined_status(statuses) -> str | None:
    """Several SIMKL entries (anime cours) can map onto one Argus show; the
    most active status wins."""
    statuses = [status for status in statuses if status]
    if not statuses:
        return None
    for candidate in (WATCHING, HOLD, DROPPED, COMPLETED, PLANTOWATCH):
        if candidate in statuses:
            return candidate
    return statuses[0]


def _local_status(remote_status: str | None) -> str:
    if remote_status == HOLD:
        return UserShow.Status.PAUSED
    if remote_status == DROPPED:
        return UserShow.Status.DROPPED
    return UserShow.Status.TRACKED


def _object_tokens(obj) -> set[str]:
    return media_tokens(
        {
            "imdb": getattr(obj, "imdb_id", None),
            "tmdb": getattr(obj, "tmdb_id", None),
            "tvdb": getattr(obj, "tvdb_id", None),
        }
    )


def _pending_desired(intents, kind: str, tokens: set[str]) -> bool | None:
    media_type = "movie" if str(kind).startswith("movie_") else "show"
    for intent in reversed(intents):
        if intent.kind != kind:
            continue
        payload = intent.payload or {}
        media = payload.get(media_type) if isinstance(payload.get(media_type), dict) else payload
        if tokens & media_tokens(media.get("ids")):
            return intent.desired
    return None


def _pending_episode_desired(intents, tokens: set[str], season_number: int, episode_number: int):
    for intent in reversed(intents):
        if intent.kind != Kind.EPISODE_HISTORY:
            continue
        payload = intent.payload or {}
        show = payload.get("show") or {}
        if not tokens & media_tokens(show.get("ids")):
            continue
        for season in payload.get("seasons") or []:
            if _as_int(season.get("number"), default=-1) != season_number:
                continue
            for episode in season.get("episodes") or []:
                if _as_int(episode.get("number"), default=-1) == episode_number:
                    return intent.desired
    return None


def _bare_item(payload: dict, media_type: str) -> dict:
    media = payload.get(media_type) if isinstance(payload.get(media_type), dict) else payload
    item = {"ids": simkl_ids(media.get("ids"))}
    for key in ("title", "year"):
        if media.get(key) not in (None, ""):
            item[key] = media[key]
    if media_type == "show":
        item["use_tvdb_anime_seasons"] = True
    return item


def _list_item(payload: dict, media_type: str, status: str) -> dict:
    return {**_bare_item(payload, media_type), "to": status}


def _add_episode(target: dict[str, dict], payload: dict, *, strip_timestamps: bool = False):
    show = payload.get("show") or {}
    if not show:
        return
    show_key = media_identity_key(show.get("ids"), title=show.get("title", ""))
    entry = target.setdefault(
        show_key,
        {
            **{key: show[key] for key in ("title", "year") if show.get(key) not in (None, "")},
            "ids": simkl_ids(show.get("ids")),
            "use_tvdb_anime_seasons": True,
            "seasons": [],
        },
    )
    for incoming_season in payload.get("seasons") or []:
        season_number = _as_int(incoming_season.get("number"), default=0)
        season = next(
            (item for item in entry["seasons"] if _as_int(item.get("number"), default=0) == season_number),
            None,
        )
        if season is None:
            season = {"number": season_number, "episodes": []}
            entry["seasons"].append(season)
        for incoming_episode in incoming_season.get("episodes") or []:
            episode_number = _as_int(incoming_episode.get("number"), default=0)
            item = {"number": episode_number}
            if not strip_timestamps and incoming_episode.get("watched_at"):
                item["watched_at"] = incoming_episode["watched_at"]
            existing = next(
                (
                    candidate
                    for candidate in season["episodes"]
                    if _as_int(candidate.get("number"), default=0) == episode_number
                ),
                None,
            )
            if existing is None:
                season["episodes"].append(item)
                continue
            existing_time = parse_timestamp(existing.get("watched_at"))
            incoming_time = parse_timestamp(item.get("watched_at"))
            if existing_time is None or (incoming_time is not None and incoming_time > existing_time):
                existing.update(item)


def _episode_count(item: dict) -> int:
    return sum(len(season.get("episodes") or []) for season in item.get("seasons") or [])


def _append_unique(items: list[dict], payload: dict) -> None:
    tokens = media_tokens(payload.get("ids"))
    for existing in items:
        if tokens & media_tokens(existing.get("ids")):
            existing.update({key: value for key, value in payload.items() if key != "ids"})
            return
    items.append(payload)


def _as_int(value, *, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
