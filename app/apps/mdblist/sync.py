"""Two-way synchronisation between Argus and an MDBList account.

The first run pulls the whole library (watched history, ratings, watchlist
and dropped shows). Later runs ask ``/sync/last_activities`` what moved and
fetch only that: the sync journal replays per-item watched and rated changes,
removals included, and the watchlist or dropped list is re-read when its
stamp changed. A local mirror of the library (:class:`MdblistLibraryItem`)
records the state each change is compared with.

Watched state is the ground truth on whichever side holds it: anything watched
on one side and not the other is marked watched there. Unwatching only travels
through explicit actions -- a local unwatch queues a removal intent, an
MDBList unwatch arrives as a journal removal.
"""

from dataclasses import dataclass, field, replace
from datetime import datetime

from cachalot.api import cachalot_disabled
from django.contrib.contenttypes.models import ContentType
from django.utils import timezone

from apps.catalog.localization import PROVIDER_DEFAULT_LANGUAGES
from apps.catalog.models import MediaRating
from apps.catalog.providers.exceptions import ProviderError
from apps.catalog.providers.registry import get_provider
from apps.mdblist.changes import mdblist_rating_from_score
from apps.mdblist.client import MAX_SHOWS_PER_WRITE
from apps.mdblist.identities import (
    episode_key,
    identity_key_for_payload,
    mdblist_ids,
    media_identity_key,
    media_tokens,
    normalize_ids,
    parse_episode_key,
    parse_timestamp,
    serialize_timestamp,
)
from apps.mdblist.models import MdblistAccount, MdblistLibraryItem, MdblistSyncIntent
from apps.movies import services as movie_services
from apps.movies.models import Movie, UserMovie
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


MOVIE = "movie"
SHOW = "show"
# Stamps whose change means the journal has something new for us.
JOURNAL_STAMPS = ("journal_at", "watched_at", "season_watched_at", "episode_watched_at", "rated_at")
# Refusing to mirror a wipe protects the local library from an empty or
# truncated snapshot.
REMOVAL_GUARD_MIN_ITEMS = 20

Kind = MdblistSyncIntent.Kind


@dataclass(frozen=True)
class RemoteState:
    """What MDBList holds for one movie or show."""

    media_type: str
    tmdb_id: int
    ids: dict = field(default_factory=dict)
    title: str = ""
    watched_at: datetime | None = None
    on_watchlist: bool = False
    dropped: bool = False
    user_rating: int | None = None
    episodes: dict = field(default_factory=dict)

    @property
    def key(self) -> tuple[str, int]:
        return (self.media_type, self.tmdb_id)

    @property
    def is_empty(self) -> bool:
        return not (
            self.watched_at or self.on_watchlist or self.dropped or self.user_rating or self.episodes
        )

    @classmethod
    def from_row(cls, row: MdblistLibraryItem) -> "RemoteState":
        return cls(
            media_type=row.media_type,
            tmdb_id=row.tmdb_id,
            ids=dict(row.ids or {}),
            title=row.title,
            watched_at=row.watched_at,
            on_watchlist=row.on_watchlist,
            dropped=row.dropped,
            user_rating=row.user_rating,
            episodes={
                key: parse_timestamp(value) for key, value in (row.watched_episodes or {}).items()
            },
        )


@dataclass
class RemoteChanges:
    # New state of every item that changed, keyed like the mirror.
    items: dict[tuple[str, int], RemoteState] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


@dataclass
class Outbound:
    watched_add_movies: list[dict] = field(default_factory=list)
    watched_add_shows: dict[str, dict] = field(default_factory=dict)
    watched_remove_movies: list[dict] = field(default_factory=list)
    watched_remove_shows: dict[str, dict] = field(default_factory=dict)
    watchlist_add_movies: list[dict] = field(default_factory=list)
    watchlist_add_shows: list[dict] = field(default_factory=list)
    watchlist_remove_movies: list[dict] = field(default_factory=list)
    watchlist_remove_shows: list[dict] = field(default_factory=list)
    dropped_add: list[dict] = field(default_factory=list)
    dropped_remove: list[dict] = field(default_factory=list)
    ratings_add_movies: list[dict] = field(default_factory=list)
    ratings_add_shows: list[dict] = field(default_factory=list)
    ratings_remove_movies: list[dict] = field(default_factory=list)
    ratings_remove_shows: list[dict] = field(default_factory=list)
    # identity key -> pending-push record, for everything sent from local state
    pending: dict[str, dict] = field(default_factory=dict)


@dataclass
class SyncReport:
    movies_imported: int = 0
    shows_imported: int = 0
    episodes_marked: int = 0
    episodes_unmarked: int = 0
    ratings_applied: int = 0
    items_pushed: int = 0
    warnings: list[str] = field(default_factory=list)


def sync_account(account_id: int, *, client_factory=None) -> SyncReport:
    from apps.mdblist.config import build_client

    account = MdblistAccount.objects.select_related("user").get(id=account_id)
    client = (client_factory or build_client)(account)
    user = account.user
    report = SyncReport()
    initial = not account.initial_sync_complete or not account.activities_cursor

    with cachalot_disabled():
        intents = list(
            MdblistSyncIntent.objects.filter(user=user).order_by("updated_at", "id")
        )
        rows = {(row.media_type, row.tmdb_id): row for row in account.library_items.all()}
        # Read before anything else: its ``server_time`` is the next cursor,
        # so whatever changes during this run is replayed next time.
        activities = client.get_activities()
        if initial:
            _refresh_profile(account, client)
            changes = fetch_full_library(client, rows, guard=False)
        else:
            changes = fetch_changes(client, account, activities, rows)
        report.warnings.extend(changes.warnings)

        previous = {key: RemoteState.from_row(row) for key, row in rows.items()}
        cache = _merge_cache(account, rows, changes)
        _prune_pending_pushes(account, cache)

        with suppress_local_intents():
            _apply_remote(user, changes, previous, intents, report, initial=initial)

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
            MdblistAccount.objects.filter(id=account.id).update(
                pending_pushes=account.pending_pushes,
                updated_at=timezone.now(),
            )
            raise

        MdblistAccount.objects.filter(id=account.id).update(
            activities_cursor=(activities.get("server_time") or account.activities_cursor or ""),
            last_activities=activities,
            initial_sync_complete=True,
            pending_pushes=account.pending_pushes,
            last_warning="\n".join(report.warnings[:10]),
            updated_at=timezone.now(),
        )
    return report


# -- Remote reads ------------------------------------------------------------


class _StateBuilder:
    """Collects the new state of the items a pull touched, starting from the
    mirror so a partial read only changes the fields it covers."""

    def __init__(self, rows):
        self.rows = rows
        self._base: dict[tuple[str, int], RemoteState] = {}
        self.states: dict[tuple[str, int], RemoteState] = {}

    def base(self, key: tuple[str, int]) -> RemoteState:
        """The mirrored state of ``key`` before this pull."""
        if key not in self._base:
            row = self.rows.get(key)
            self._base[key] = RemoteState.from_row(row) if row is not None else RemoteState(*key)
        return self._base[key]

    def get(self, media_type: str, tmdb_id: int) -> RemoteState:
        key = (media_type, tmdb_id)
        if key not in self.states:
            self.states[key] = self.base(key)
        return self.states[key]

    def update(self, media_type: str, tmdb_id: int, media: dict | None = None, **fields) -> None:
        state = self.get(media_type, tmdb_id)
        if media:
            ids = {**state.ids, **normalize_ids(media.get("ids"))}
            fields.setdefault("ids", ids)
            if media.get("title") and not state.title:
                fields.setdefault("title", str(media["title"])[:255])
        self.states[(media_type, tmdb_id)] = replace(state, **fields)

    def replace_field(self, media_type: str, name: str, present: dict[int, object], *, empty, warnings, label: str) -> None:
        """Make ``name`` match a full snapshot: ``present`` holds the value for
        every item the snapshot listed, the rest get ``empty``."""
        keys = {key for key in (*self.rows, *self.states) if key[0] == media_type}
        holding = [key for key in keys if getattr(self.get(*key), name) != empty]
        stale = [key for key in holding if key[1] not in present]
        if len(stale) > REMOVAL_GUARD_MIN_ITEMS and len(stale) * 2 > len(holding):
            warnings.append(
                f"MDBList reported {len(stale)} of {len(holding)} {label} removed; "
                "ignoring the removal as a safety measure."
            )
        else:
            for _media_type, tmdb_id in stale:
                self.update(media_type, tmdb_id, **{name: empty})
        for tmdb_id, value in present.items():
            if getattr(self.get(media_type, tmdb_id), name) != value:
                self.update(media_type, tmdb_id, **{name: value})


def fetch_full_library(client, rows, *, guard: bool = True) -> RemoteChanges:
    """Read the whole library; items absent from it lose their state."""
    changes = RemoteChanges()
    builder = _StateBuilder(rows)
    warnings = changes.warnings if guard else []
    _apply_watched_snapshot(builder, client.get_watched(), warnings)
    _apply_ratings_snapshot(builder, client.get_ratings(), warnings)
    _apply_watchlist_snapshot(builder, client.get_watchlist(ids_only=False), warnings)
    _apply_dropped_snapshot(builder, client.get_dropped(), warnings)
    changes.items = _changed(builder)
    return changes


def fetch_changes(client, account, activities: dict, rows) -> RemoteChanges:
    """Replay what moved since the last run."""
    previous = account.last_activities or {}
    changes = RemoteChanges()
    builder = _StateBuilder(rows)

    if any(activities.get(stamp) != previous.get(stamp) for stamp in JOURNAL_STAMPS):
        journal = client.get_journal(account.activities_cursor)
        if journal.get("requires_full_sync"):
            # The journal only keeps 30 days; start over from a snapshot.
            return fetch_full_library(client, rows)
        needs_watched_snapshot = False
        entries = sorted(journal.get("journal") or [], key=lambda row: str(row.get("action_at") or ""))
        for entry in entries:
            needs_watched_snapshot |= _apply_journal_entry(builder, entry)
        if needs_watched_snapshot:
            # A whole show or season changed at once; the journal does not
            # list its episodes, so re-read the watched snapshot instead.
            _apply_watched_snapshot(builder, client.get_watched(), changes.warnings)
    if activities.get("watchlisted_at") != previous.get("watchlisted_at"):
        _apply_watchlist_snapshot(builder, client.get_watchlist(ids_only=False), changes.warnings)
    if activities.get("dropped_at") != previous.get("dropped_at"):
        _apply_dropped_snapshot(builder, client.get_dropped(), changes.warnings)
    changes.items = _changed(builder)
    return changes


def _apply_journal_entry(builder: _StateBuilder, entry: dict) -> bool:
    """Apply one journal row; returns True when a watched snapshot is needed."""
    if not isinstance(entry, dict):
        return False
    tmdb_id = _as_int(normalize_ids(entry.get("ids")).get("tmdb"), default=0)
    if not tmdb_id:
        return False
    item_type = entry.get("item_type")
    added = entry.get("status") == "added"
    media = {"ids": entry.get("ids")}
    if entry.get("category") == "watched":
        if item_type == MOVIE:
            watched_at = parse_timestamp(entry.get("value_at")) or timezone.now() if added else None
            builder.update(MOVIE, tmdb_id, media, watched_at=watched_at)
            return False
        if item_type == "episode":
            key = episode_key(_as_int(entry.get("season"), default=0), _as_int(entry.get("episode"), default=0))
            episodes = dict(builder.get(SHOW, tmdb_id).episodes)
            if added:
                episodes[key] = parse_timestamp(entry.get("value_at")) or timezone.now()
            else:
                episodes.pop(key, None)
            builder.update(SHOW, tmdb_id, media, episodes=episodes)
            return False
        return item_type in {"show", "season"}
    if entry.get("category") == "rated" and item_type in {MOVIE, SHOW}:
        rating = _as_int(entry.get("rating"), default=0) or None if added else None
        builder.update(item_type, tmdb_id, media, user_rating=rating)
    return False


def _apply_watched_snapshot(builder: _StateBuilder, payload: dict, warnings) -> None:
    movies: dict[int, datetime] = {}
    for item in payload.get("movies") or []:
        media = item.get("movie") if isinstance(item.get("movie"), dict) else item
        tmdb_id = _tmdb_of(media)
        if not tmdb_id:
            continue
        watched_at = parse_timestamp(item.get("last_watched_at") or item.get("watched_at")) or timezone.now()
        movies[tmdb_id] = watched_at
        builder.update(MOVIE, tmdb_id, media)
    builder.replace_field(MOVIE, "watched_at", movies, empty=None, warnings=warnings, label="watched movies")

    shows: dict[int, dict] = {}
    for item in payload.get("episodes") or []:
        episode = item.get("episode") if isinstance(item.get("episode"), dict) else item
        show = episode.get("show")
        show_tmdb = _tmdb_of(show) if isinstance(show, dict) else _as_int(show, default=0)
        season = _as_int(episode.get("season"), default=-1)
        number = _as_int(episode.get("number") or episode.get("episode"), default=0)
        if not show_tmdb or season < 0 or number <= 0:
            continue
        watched_at = parse_timestamp(item.get("last_watched_at") or item.get("watched_at")) or timezone.now()
        shows.setdefault(show_tmdb, {})[episode_key(season, number)] = watched_at
        if isinstance(show, dict):
            builder.update(SHOW, show_tmdb, show)
    builder.replace_field(SHOW, "episodes", shows, empty={}, warnings=warnings, label="watched shows")


def _apply_ratings_snapshot(builder: _StateBuilder, payload: dict, warnings) -> None:
    for media_type, key in ((MOVIE, "movies"), (SHOW, "shows")):
        ratings: dict[int, int] = {}
        for item in payload.get(key) or []:
            media = item.get(media_type) if isinstance(item.get(media_type), dict) else item
            tmdb_id = _tmdb_of(media)
            rating = _as_int(item.get("rating"), default=0)
            if not tmdb_id or not rating:
                continue
            ratings[tmdb_id] = rating
            builder.update(media_type, tmdb_id, media)
        builder.replace_field(media_type, "user_rating", ratings, empty=None, warnings=warnings, label=f"rated {key}")


def _apply_watchlist_snapshot(builder: _StateBuilder, payload: dict, warnings) -> None:
    for media_type, key in ((MOVIE, "movies"), (SHOW, "shows")):
        present: dict[int, bool] = {}
        for item in payload.get(key) or []:
            tmdb_id = _tmdb_of(item) or _as_int(item.get("id"), default=0)
            if not tmdb_id:
                continue
            present[tmdb_id] = True
            builder.update(media_type, tmdb_id, item)
        builder.replace_field(media_type, "on_watchlist", present, empty=False, warnings=warnings, label=f"watchlisted {key}")


def _apply_dropped_snapshot(builder: _StateBuilder, payload: dict, warnings) -> None:
    present: dict[int, bool] = {}
    for item in payload.get("shows") or []:
        media = item.get("show") if isinstance(item.get("show"), dict) else item
        tmdb_id = _tmdb_of(media)
        if not tmdb_id:
            continue
        present[tmdb_id] = True
        builder.update(SHOW, tmdb_id, media)
    builder.replace_field(SHOW, "dropped", present, empty=False, warnings=warnings, label="dropped shows")


def _changed(builder: _StateBuilder) -> dict[tuple[str, int], RemoteState]:
    return {
        key: state
        for key, state in builder.states.items()
        if _comparable(state) != _comparable(builder.base(key))
    }


def _comparable(state: RemoteState):
    return (
        state.watched_at,
        state.on_watchlist,
        state.dropped,
        state.user_rating,
        tuple(sorted(state.episodes)),
        tuple(sorted(state.ids.items())),
        state.title,
    )


def _tmdb_of(media) -> int:
    if not isinstance(media, dict):
        return 0
    ids = normalize_ids(media.get("ids"))
    return _as_int(ids.get("tmdb") or media.get("tmdb"), default=0)


# -- Library mirror ----------------------------------------------------------


def _merge_cache(account, rows, changes: RemoteChanges) -> dict[tuple[str, int], MdblistLibraryItem]:
    rows = dict(rows)
    to_create = []
    to_update = []
    to_delete = []
    for key, state in changes.items.items():
        row = rows.get(key)
        if state.is_empty:
            if row is not None:
                to_delete.append(row.id)
                rows.pop(key)
            continue
        if row is None:
            row = MdblistLibraryItem(account=account, media_type=state.media_type, tmdb_id=state.tmdb_id)
            to_create.append(row)
        else:
            to_update.append(row)
        row.ids = state.ids
        row.title = state.title[:255]
        row.watched_at = state.watched_at
        row.on_watchlist = state.on_watchlist
        row.dropped = state.dropped
        row.user_rating = state.user_rating
        row.watched_episodes = {
            episode: serialize_timestamp(watched_at) if watched_at else None
            for episode, watched_at in state.episodes.items()
        }
        rows[key] = row
    if to_create:
        MdblistLibraryItem.objects.bulk_create(to_create, batch_size=500)
    if to_update:
        now = timezone.now()
        for row in to_update:
            row.updated_at = now
        MdblistLibraryItem.objects.bulk_update(
            to_update,
            [
                "ids",
                "title",
                "watched_at",
                "on_watchlist",
                "dropped",
                "user_rating",
                "watched_episodes",
                "updated_at",
            ],
            batch_size=500,
        )
    if to_delete:
        MdblistLibraryItem.objects.filter(id__in=to_delete).delete()
    return rows


def _prune_pending_pushes(account, cache) -> None:
    """Forget pushes MDBList has echoed back through the library mirror."""
    pending = dict(account.pending_pushes or {})
    if not pending:
        return
    index = _token_index(cache.values())
    for key, record in list(pending.items()):
        if not isinstance(record, dict) or record.get("reason") == "not_found":
            continue
        rows = _rows_for_tokens(set(record.get("tokens") or []), index)
        if any(_echoed(row, record) for row in rows):
            pending.pop(key)
    account.pending_pushes = pending


def _echoed(row: MdblistLibraryItem, record: dict) -> bool:
    what = record.get("field")
    if what == "episode":
        return record.get("episode") in (row.watched_episodes or {})
    if what == "watched":
        return bool(row.watched_at)
    if what == "watchlist":
        return row.on_watchlist or bool(row.watched_at)
    if what == "dropped":
        return row.dropped
    if what == "rating":
        return row.user_rating == record.get("rating")
    return True


def _row_tokens(row) -> set[str]:
    return media_tokens({**(row.ids or {}), "tmdb": row.tmdb_id})


def _token_index(rows) -> dict[str, list[MdblistLibraryItem]]:
    index: dict[str, list[MdblistLibraryItem]] = {}
    for row in rows:
        for token in _row_tokens(row):
            index.setdefault(token, []).append(row)
    return index


def _rows_for_tokens(tokens: set[str], index, media_type: str | None = None) -> list[MdblistLibraryItem]:
    seen: dict[int, MdblistLibraryItem] = {}
    for token in tokens:
        for row in index.get(token, []):
            if media_type is None or row.media_type == media_type:
                seen[id(row)] = row
    return list(seen.values())


# -- Remote -> local ---------------------------------------------------------


def _apply_remote(user, changes, previous, intents, report, *, initial: bool):
    _apply_remote_movies(user, changes, previous, intents, report)
    _apply_remote_shows(user, changes, previous, intents, report, initial=initial)


def _apply_remote_movies(user, changes, previous, intents, report):
    movie_type = ContentType.objects.get_for_model(Movie)
    for key, item in changes.items.items():
        if item.media_type != MOVIE:
            continue
        prev = previous.get(key) or RemoteState(*key)
        gains = bool(item.watched_at or item.on_watchlist or item.user_rating)
        try:
            movie, created = _ensure_movie(user, item, create=gains)
        except (ProviderError, ValueError) as exc:
            report.warnings.append(f"Movie import failed ({item.title or item.tmdb_id}): {exc}")
            continue
        if movie is None:
            continue
        if created:
            report.movies_imported += 1
        tokens = media_tokens(item.ids) | _object_tokens(movie)
        state = UserMovie.objects.filter(user=user, movie=movie).first()
        history_pending = _pending_desired(intents, Kind.MOVIE_HISTORY, tokens)
        watchlist_pending = _pending_desired(intents, Kind.MOVIE_WATCHLIST, tokens)
        dirty = False

        if item.watched_at and item.watched_at != prev.watched_at:
            if history_pending is not False:
                state = state or UserMovie(user=user, movie=movie)
                if not state.is_seen or state.seen_at is None or item.watched_at > state.seen_at:
                    state.seen_at = item.watched_at
                state.is_seen = True
                state.on_watchlist = False
                state.watchlist_added_at = None
                dirty = True
        elif prev.watched_at and not item.watched_at:
            if state is not None and state.is_seen and history_pending is not True:
                state.is_seen = False
                state.seen_at = None
                dirty = True

        if item.on_watchlist and not prev.on_watchlist:
            if watchlist_pending is not False and (state is None or not state.is_seen):
                state = state or UserMovie(user=user, movie=movie)
                if not state.on_watchlist:
                    state.on_watchlist = True
                    state.watchlist_added_at = timezone.now()
                    dirty = True
        elif prev.on_watchlist and not item.on_watchlist and not item.watched_at:
            # MDBList drops a movie from the watchlist once it is watched;
            # only a removal without a watch is the user's doing.
            if state is not None and state.on_watchlist and watchlist_pending is not True:
                state.on_watchlist = False
                state.watchlist_added_at = None
                dirty = True

        if state is not None and dirty:
            if not state.is_seen and not state.on_watchlist and state.pk is not None:
                state.delete()
                state = None
            elif state.is_seen or state.on_watchlist:
                state.save()

        if item.user_rating != prev.user_rating:
            _apply_rating(
                user,
                movie,
                movie_type,
                MediaRating.MediaType.MOVIE,
                rating=item.user_rating,
                previous_rating=prev.user_rating,
                pending=_pending_desired(intents, Kind.MOVIE_RATING, tokens),
                rateable=bool(state and state.is_seen),
                report=report,
            )


def _apply_remote_shows(user, changes, previous, intents, report, *, initial: bool):
    show_type = ContentType.objects.get_for_model(Show)
    episode_requests: list[tuple[_EpisodeRequest, Show]] = []
    episode_removals: list[tuple[Show, int, int]] = []

    for key, item in changes.items.items():
        if item.media_type != SHOW:
            continue
        prev = previous.get(key) or RemoteState(*key)
        gains = bool(item.episodes or item.on_watchlist or item.dropped or item.user_rating)
        try:
            show, created = _ensure_show(user, item, create=gains)
        except (ProviderError, ValueError) as exc:
            report.warnings.append(f"Show import failed ({item.title or item.tmdb_id}): {exc}")
            continue
        if show is None:
            continue
        if created:
            report.shows_imported += 1
        tokens = media_tokens(item.ids) | _object_tokens(show)
        dropped_pending = _pending_desired(intents, Kind.SHOW_DROPPED, tokens)
        watchlist_pending = _pending_desired(intents, Kind.SHOW_WATCHLIST, tokens)
        has_local_episodes = UserEpisode.objects.filter(user=user, episode__show=show).exists()

        user_show = UserShow.objects.filter(user=user, show=show).first()
        if user_show is None and gains:
            user_show = UserShow.objects.create(
                user=user,
                show=show,
                status=UserShow.Status.DROPPED if item.dropped else UserShow.Status.TRACKED,
                on_watchlist=item.on_watchlist and not item.episodes and not has_local_episodes,
            )
        elif user_show is not None:
            # MDBList has no paused state; the first pull leaves a show paused
            # in Argus alone rather than reading it as dropped.
            sticky = initial and user_show.status == UserShow.Status.PAUSED
            if item.dropped and not prev.dropped:
                if (
                    dropped_pending is not False
                    and not sticky
                    and user_show.status != UserShow.Status.DROPPED
                ):
                    user_show.status = UserShow.Status.DROPPED
                    user_show.save(update_fields=["status", "updated_at"])
            elif prev.dropped and not item.dropped:
                if dropped_pending is not True and user_show.status == UserShow.Status.DROPPED:
                    user_show.status = UserShow.Status.TRACKED
                    user_show.save(update_fields=["status", "updated_at"])

            if item.on_watchlist and not prev.on_watchlist:
                if (
                    watchlist_pending is not False
                    and user_show.status == UserShow.Status.TRACKED
                    and not has_local_episodes
                    and not item.episodes
                    and not user_show.on_watchlist
                ):
                    user_show.on_watchlist = True
                    user_show.save(update_fields=["on_watchlist", "updated_at"])
            elif (
                user_show.on_watchlist
                and watchlist_pending is not True
                and ((prev.on_watchlist and not item.on_watchlist) or (item.episodes and not prev.episodes))
            ):
                user_show.on_watchlist = False
                user_show.save(update_fields=["on_watchlist", "updated_at"])

        for episode in set(item.episodes) - set(prev.episodes):
            season_number, episode_number = parse_episode_key(episode)
            if _pending_episode_desired(intents, tokens, season_number, episode_number) is False:
                continue
            episode_requests.append(
                (
                    _EpisodeRequest(
                        show={"ids": item.ids},
                        episode={},
                        season_number=season_number,
                        episode_number=episode_number,
                        watched_at=item.episodes[episode] or timezone.now(),
                    ),
                    show,
                )
            )
        for episode in set(prev.episodes) - set(item.episodes):
            season_number, episode_number = parse_episode_key(episode)
            if _pending_episode_desired(intents, tokens, season_number, episode_number) is True:
                continue
            episode_removals.append((show, season_number, episode_number))

        if item.user_rating != prev.user_rating:
            _apply_rating(
                user,
                show,
                show_type,
                MediaRating.MediaType.SHOW,
                rating=item.user_rating,
                previous_rating=prev.user_rating,
                pending=_pending_desired(intents, Kind.SHOW_RATING, tokens),
                rateable=True,
                report=report,
            )

        if item.is_empty and user_show is not None and watchlist_pending is not True:
            # Gone from MDBList altogether: untrack it once nothing local
            # holds it either (after the episode removals below).
            episode_removals_for_show = [removal for removal in episode_removals if removal[0].id == show.id]
            _unmark_episodes(user, episode_removals_for_show, report)
            episode_removals = [removal for removal in episode_removals if removal[0].id != show.id]
            if (
                user_show.status == UserShow.Status.TRACKED
                and not UserEpisode.objects.filter(user=user, episode__show=show).exists()
            ):
                user_show.delete()

    _mark_episodes(user, episode_requests, report)
    _unmark_episodes(user, episode_removals, report)


# -- Catalog resolution ------------------------------------------------------


def _ensure_movie(user, item: RemoteState, *, create: bool) -> tuple[Movie | None, bool]:
    ids = {**item.ids, "tmdb": str(item.tmdb_id)}
    media = {"title": item.title, "ids": ids}
    movie = _find_by_ids(Movie, media, user=user, user_state_relation="user_states")
    if movie is None:
        if not create:
            return None, False
        movie = _import_with_provider_fallback(
            ids,
            ("tmdb",),
            lambda provider, external_id: movie_services.import_movie(
                provider,
                external_id,
                language=PROVIDER_DEFAULT_LANGUAGES[provider],
            ),
        )
        if movie is None:
            raise ValueError("MDBList movie has no TMDB identifier")
        _normalize_movie_title(movie, media)
        _save_media_ids(movie, _identity_media(ids, movie))
        return movie, True
    _save_media_ids(movie, _identity_media(ids, movie))
    return movie, False


def _ensure_show(user, item: RemoteState, *, create: bool) -> tuple[Show | None, bool]:
    ids = {**item.ids, "tmdb": str(item.tmdb_id)}
    media = {"title": item.title, "ids": ids}
    show = _find_by_ids(Show, media, user=user, user_state_relation="user_states")
    if show is None:
        if not create:
            return None, False
        if not ids.get("tvdb"):
            # MDBList sync rows only carry TMDB and IMDb ids; TVDB stays the
            # preferred show source, so look its id up before importing.
            ids.update(_lookup_show_ids(item.tmdb_id))
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
            raise ValueError("MDBList show has no TVDB or TMDB identifier")
        _normalize_show_title(show, media)
        _save_media_ids(show, _identity_media(ids, show))
        return show, True
    _save_media_ids(show, _identity_media(ids, show))
    return show, False


def _lookup_show_ids(tmdb_id: int) -> dict:
    """TVDB and IMDb ids for a TMDB show, from TMDB's own external ids."""
    try:
        detail = get_provider("tmdb").fetch_detail(
            str(tmdb_id),
            language=PROVIDER_DEFAULT_LANGUAGES["tmdb"],
            media_type="tv",
        )
    except (ProviderError, ValueError):
        return {}
    return {
        key: str(value)
        for key, value in {"tvdb": detail.tvdb_id, "imdb": detail.imdb_id}.items()
        if value
    }


def _identity_media(ids: dict, obj) -> dict:
    """Ids worth persisting on the local record: only the providers Argus
    tracks, and only when the record does not already carry them."""
    return {
        "ids": {
            key: value
            for key, value in ids.items()
            if key in {"imdb", "tmdb", "tvdb"} and value and not getattr(obj, f"{key}_id", None)
        }
    }


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
        rows = _rows_for_tokens(tokens, index, MOVIE)
        return rows[0] if rows else None

    def show_row(tokens):
        rows = _rows_for_tokens(tokens, index, SHOW)
        return rows[0] if rows else None

    def queue(identity, tokens, what, **extra):
        outbound.pending[identity] = {
            "at": serialize_timestamp(timezone.now()),
            "tokens": sorted(tokens),
            "field": what,
            "reason": "pushed",
            **extra,
        }

    def skipped(identity) -> bool:
        return identity in pending_pushes

    tracked_show_ids = set(UserShow.objects.filter(user=user).values_list("show_id", flat=True))
    local_episode_show_ids = {state.episode.show_id for state in local.episode_history}
    seen_movie_ids = {state.movie_id for state in local.movie_history}

    # -- Projection of local watched state (the union rule) ------------------
    for state in local.movie_history:
        payload = intent_movie_payload(state.movie, watched_at=state.seen_at)
        tokens = media_tokens(payload["ids"])
        if not tokens:
            continue
        row = movie_row(tokens)
        if row is not None and row.watched_at:
            continue
        identity = media_identity_key(payload["ids"])
        if skipped(identity):
            continue
        _append_unique(outbound.watched_add_movies, _watched_item(payload, MOVIE))
        queue(identity, tokens, "watched")

    for state in local.episode_history:
        episode = state.episode
        payload = intent_episode_payload(episode, watched_at=state.seen_at)
        tokens = media_tokens(payload["show"]["ids"])
        if not tokens:
            continue
        key = episode_key(episode.season_number, episode.episode_number)
        row = show_row(tokens)
        if row is not None and key in (row.watched_episodes or {}):
            continue
        identity = identity_key_for_payload(Kind.EPISODE_HISTORY, payload)
        if skipped(identity):
            continue
        _add_episode(outbound.watched_add_shows, payload)
        queue(identity, tokens, "episode", episode=key)

    if initial:
        for state in local.movie_watchlist:
            payload = intent_movie_payload(state.movie)
            tokens = media_tokens(payload["ids"])
            row = movie_row(tokens)
            if not tokens or (row is not None and (row.on_watchlist or row.watched_at)):
                continue
            identity = f"{media_identity_key(payload['ids'])}#list"
            if skipped(identity):
                continue
            _append_unique(outbound.watchlist_add_movies, _bare_item(payload, MOVIE))
            queue(identity, tokens, "watchlist")
        for state in local.show_watchlist:
            if state.status != UserShow.Status.TRACKED or state.show_id in local_episode_show_ids:
                continue
            payload = intent_show_payload(state.show)
            tokens = media_tokens(payload["ids"])
            row = show_row(tokens)
            if not tokens or (row is not None and (row.on_watchlist or row.watched_episodes)):
                continue
            identity = f"{media_identity_key(payload['ids'])}#list"
            if skipped(identity):
                continue
            _append_unique(outbound.watchlist_add_shows, _bare_item(payload, SHOW))
            queue(identity, tokens, "watchlist")
        for state in local.show_dropped:
            payload = intent_show_payload(state.show)
            tokens = media_tokens(payload["ids"])
            row = show_row(tokens)
            if not tokens or (row is not None and row.dropped):
                continue
            identity = f"{media_identity_key(payload['ids'])}#dropped"
            if skipped(identity):
                continue
            _append_unique(outbound.dropped_add, _bare_item(payload, SHOW))
            queue(identity, tokens, "dropped")

    # -- Ratings projection (episode ratings stay local) ---------------------
    movie_type = ContentType.objects.get_for_model(Movie)
    show_type = ContentType.objects.get_for_model(Show)
    ratings = list(MediaRating.objects.filter(user=user, content_type__in=[movie_type, show_type]))
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
        value = mdblist_rating_from_score(rating.score)
        if rating.content_type_id == movie_type.id:
            media = rated_movies.get(rating.object_id)
            if media is None or media.id not in seen_movie_ids:
                continue
            payload, media_type, target = intent_movie_payload(media), MOVIE, outbound.ratings_add_movies
            row = movie_row(media_tokens(payload["ids"]))
        else:
            media = rated_shows.get(rating.object_id)
            if media is None or media.id not in tracked_show_ids:
                continue
            payload, media_type, target = intent_show_payload(media), SHOW, outbound.ratings_add_shows
            row = show_row(media_tokens(payload["ids"]))
        tokens = media_tokens(payload["ids"])
        if not tokens or (row is not None and row.user_rating == value):
            continue
        identity = f"{media_identity_key(payload['ids'])}#rating"
        if skipped(identity) and (pending_pushes.get(identity) or {}).get("rating") in (None, value):
            continue
        _append_unique(target, {**_bare_item(payload, media_type), "rating": value})
        queue(identity, tokens, "rating", rating=value)

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
            row = show_row(tokens)
            remote = set((row.watched_episodes or {}) if row is not None else {})
            for season in payload.get("seasons") or []:
                for episode in season.get("episodes") or []:
                    key = episode_key(
                        _as_int(season.get("number"), default=0),
                        _as_int(episode.get("number"), default=0),
                    )
                    if desired and key not in remote:
                        _add_episode(outbound.watched_add_shows, payload)
                    elif not desired and key in remote:
                        _add_episode(outbound.watched_remove_shows, payload, strip_timestamps=True)
            continue

        media_type = MOVIE if kind.startswith("movie_") else SHOW
        media = payload.get(media_type) if isinstance(payload.get(media_type), dict) else payload
        tokens = media_tokens(media.get("ids"))
        if not tokens:
            continue

        if media_type == MOVIE:
            row = movie_row(tokens)
            locally_seen = any(tokens & _object_tokens(state.movie) for state in local.movie_history)
            if kind == Kind.MOVIE_WATCHLIST:
                listed = row is not None and row.on_watchlist
                if desired and not listed and not locally_seen:
                    _append_unique(outbound.watchlist_add_movies, _bare_item(payload, MOVIE))
                elif not desired and listed:
                    _append_unique(outbound.watchlist_remove_movies, _bare_item(payload, MOVIE))
            elif kind == Kind.MOVIE_HISTORY:
                watched = row is not None and bool(row.watched_at)
                if desired and not watched:
                    _append_unique(outbound.watched_add_movies, _watched_item(payload, MOVIE))
                elif not desired and watched:
                    _append_unique(outbound.watched_remove_movies, _bare_item(payload, MOVIE))
            elif kind == Kind.MOVIE_RATING:
                rating = _as_int(payload.get("rating"), default=0)
                if desired and rating and (row is None or row.user_rating != rating):
                    _append_unique(outbound.ratings_add_movies, {**_bare_item(payload, MOVIE), "rating": rating})
                elif not desired and row is not None and row.user_rating:
                    _append_unique(outbound.ratings_remove_movies, _bare_item(payload, MOVIE))
            continue

        row = show_row(tokens)
        locally_has_episodes = any(
            tokens & _object_tokens(state.episode.show) for state in local.episode_history
        )
        if kind == Kind.SHOW_WATCHLIST:
            listed = row is not None and row.on_watchlist
            if desired and not listed and not locally_has_episodes:
                _append_unique(outbound.watchlist_add_shows, _bare_item(payload, SHOW))
            elif not desired and listed:
                _append_unique(outbound.watchlist_remove_shows, _bare_item(payload, SHOW))
        elif kind == Kind.SHOW_DROPPED:
            dropped = row is not None and row.dropped
            if desired and not dropped:
                _append_unique(outbound.dropped_add, _bare_item(payload, SHOW))
            elif not desired and dropped:
                _append_unique(outbound.dropped_remove, _bare_item(payload, SHOW))
        elif kind == Kind.SHOW_RATING:
            rating = _as_int(payload.get("rating"), default=0)
            if desired and rating and (row is None or row.user_rating != rating):
                _append_unique(outbound.ratings_add_shows, {**_bare_item(payload, SHOW), "rating": rating})
            elif not desired and row is not None and row.user_rating:
                _append_unique(outbound.ratings_remove_shows, _bare_item(payload, SHOW))

    return outbound


def _push(client, account, outbound: Outbound, intents, report) -> int:
    """Send the batched writes in dependency order and settle the bookkeeping."""
    sent = 0
    not_found: list[dict] = []

    def collect_not_found(response, keys=("movies", "shows")):
        block = (response or {}).get("not_found") or {}
        for key in keys:
            items = block.get(key)
            if isinstance(items, list):
                not_found.extend(item for item in items if isinstance(item, dict))

    def send(method, movies, shows):
        nonlocal sent
        shows = list(shows)
        chunks = [shows[start:start + MAX_SHOWS_PER_WRITE] for start in range(0, len(shows), MAX_SHOWS_PER_WRITE)] or [[]]
        for position, chunk in enumerate(chunks):
            body = {
                key: value
                for key, value in (("movies", movies if position == 0 else []), ("shows", chunk))
                if value
            }
            if body:
                collect_not_found(method(body))
        sent += len(movies) + len(shows)

    # Removals first: a movie unwatched locally but kept on the watchlist is
    # unwatched on MDBList before it is listed again.
    if outbound.watched_remove_movies or outbound.watched_remove_shows:
        send(client.remove_watched, outbound.watched_remove_movies, outbound.watched_remove_shows.values())
    if outbound.watched_add_movies or outbound.watched_add_shows:
        send(client.add_watched, outbound.watched_add_movies, outbound.watched_add_shows.values())
    if outbound.watchlist_remove_movies or outbound.watchlist_remove_shows:
        send(client.remove_from_watchlist, outbound.watchlist_remove_movies, outbound.watchlist_remove_shows)
    if outbound.watchlist_add_movies or outbound.watchlist_add_shows:
        send(client.add_to_watchlist, outbound.watchlist_add_movies, outbound.watchlist_add_shows)
    if outbound.dropped_remove:
        send(client.remove_dropped, [], outbound.dropped_remove)
    if outbound.dropped_add:
        send(client.add_dropped, [], outbound.dropped_add)
    if outbound.ratings_remove_movies or outbound.ratings_remove_shows:
        send(client.remove_ratings, outbound.ratings_remove_movies, outbound.ratings_remove_shows)
    if outbound.ratings_add_movies or outbound.ratings_add_shows:
        send(client.add_ratings, outbound.ratings_add_movies, outbound.ratings_add_shows)

    pending = dict(account.pending_pushes or {})
    pending.update(outbound.pending)
    account.pending_pushes = pending
    for item in not_found:
        _remember_not_found(account, item)
        tokens = media_tokens(item.get("ids"))
        # Episodes queued for an unmatched show are not echoed back either.
        for identity, record in outbound.pending.items():
            if tokens & set(record.get("tokens") or []):
                account.pending_pushes[identity] = {**record, "reason": "not_found"}
        title = item.get("title") or ", ".join(
            f"{key} {value}" for key, value in (item.get("ids") or {}).items()
        )
        report.warnings.append(f"MDBList could not match {title or 'an item'}.")
    for intent in intents:
        _delete_intent_if_unchanged(intent)
    return sent


def _remember_not_found(account, item: dict) -> None:
    ids = normalize_ids(item.get("ids"))
    tokens = media_tokens(ids)
    if not tokens:
        return
    pending = dict(account.pending_pushes or {})
    record = {
        "at": serialize_timestamp(timezone.now()),
        "tokens": sorted(tokens),
        "field": None,
        "reason": "not_found",
    }
    identity = media_identity_key(ids, title=item.get("title", ""), year=item.get("year"))
    # Every key the projection might derive for this title.
    for suffix in ("", "#list", "#dropped", "#rating"):
        pending[f"{identity}{suffix}"] = record
    for season in item.get("seasons") or []:
        for episode in season.get("episodes") or []:
            season_number = _as_int(season.get("number"), default=0)
            episode_number = _as_int(episode.get("number"), default=0)
            pending[f"episode:{identity}:s{season_number}:e{episode_number}"] = {
                **record,
                "episode": episode_key(season_number, episode_number),
            }
    account.pending_pushes = pending


def _delete_intent_if_unchanged(intent) -> None:
    MdblistSyncIntent.objects.filter(id=intent.id, updated_at=intent.updated_at).delete()


# -- Helpers -----------------------------------------------------------------


def _refresh_profile(account, client) -> None:
    profile = client.get_user() or {}
    account.mdblist_username = str(profile.get("username") or "")[:255]
    account.mdblist_user_id = str(profile.get("user_id") or "")[:32]
    account.plan = str(profile.get("plan") or "")[:32]
    account.is_supporter = bool(profile.get("is_supporter"))
    account.save(
        update_fields=["mdblist_username", "mdblist_user_id", "plan", "is_supporter", "updated_at"]
    )


def _object_tokens(obj) -> set[str]:
    return media_tokens(
        {
            "imdb": getattr(obj, "imdb_id", None),
            "tmdb": getattr(obj, "tmdb_id", None),
            "tvdb": getattr(obj, "tvdb_id", None),
        }
    )


def _pending_desired(intents, kind: str, tokens: set[str]) -> bool | None:
    media_type = MOVIE if str(kind).startswith("movie_") else SHOW
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
    item = {"ids": mdblist_ids(media.get("ids"))}
    if media.get("title"):
        item["title"] = media["title"]
    return item


def _watched_item(payload: dict, media_type: str) -> dict:
    media = payload.get(media_type) if isinstance(payload.get(media_type), dict) else payload
    item = _bare_item(payload, media_type)
    if media.get("watched_at"):
        item["watched_at"] = media["watched_at"]
    return item


def _add_episode(target: dict[str, dict], payload: dict, *, strip_timestamps: bool = False):
    show = payload.get("show") or {}
    if not show:
        return
    show_key = media_identity_key(show.get("ids"), title=show.get("title", ""))
    entry = target.setdefault(
        show_key,
        {
            **({"title": show["title"]} if show.get("title") else {}),
            "ids": mdblist_ids(show.get("ids")),
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
