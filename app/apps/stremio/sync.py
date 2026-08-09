from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import base64
import binascii
import re
import zlib
from typing import Callable

from django.db.models import Q
from django.utils import timezone as django_timezone
from cachalot.api import cachalot_disabled

from apps.catalog.localization import PROVIDER_DEFAULT_LANGUAGES
from apps.catalog.providers.exceptions import ProviderError
from apps.catalog.providers.tmdb import TMDBProvider
from apps.movies import services as movie_services
from apps.movies.models import Movie, UserMovie
from apps.stremio.client import StremioClient
from apps.stremio.codec import decode_watched_bitfield, encode_watched_bitfield
from apps.stremio.models import StremioAccount, StremioSyncIntent
from apps.trakt.changes import suppress_local_intents
from apps.trakt.sync import LocalSnapshot, _collect_local_snapshot
from apps.tv import services as tv_services
from apps.tv.models import Episode, Season, Show, UserEpisode, UserShow


@dataclass(frozen=True)
class WatchedMovie:
    content_id: str
    title: str
    watched_at: datetime | None


@dataclass(frozen=True)
class WatchedEpisode:
    content_id: str
    title: str
    season_number: int
    episode_number: int
    watched_at: datetime | None


@dataclass
class RemoteSnapshot:
    items: list[dict] = field(default_factory=list)
    library_ids: set[str] = field(default_factory=set)
    watched_movies: dict[str, WatchedMovie] = field(default_factory=dict)
    watched_episodes: dict[tuple[str, int, int], WatchedEpisode] = field(default_factory=dict)
    series_watched: dict[str, set[tuple[int, int]]] = field(default_factory=dict)
    series_video_ids: dict[str, list[str]] = field(default_factory=dict)
    series_metadata_available: set[str] = field(default_factory=set)
    metadata_failures: set[str] = field(default_factory=set)
    series_state_valid: set[str] = field(default_factory=set)


@dataclass
class SyncReport:
    movies_imported: int = 0
    shows_imported: int = 0
    episodes_marked: int = 0
    items_pushed: int = 0
    warnings: list[str] = field(default_factory=list)
    initial_sync_complete: bool = False


def normalize_items(
    items: list[dict],
    *,
    cinemeta_getter: Callable[[str], dict],
    user=None,
) -> RemoteSnapshot:
    snapshot = RemoteSnapshot(items=[item for item in items if isinstance(item, dict)])
    metadata_cache: dict[str, dict | None] = {}
    metadata_failures: set[str] = set()

    for item in snapshot.items:
        content_id = _content_id(item.get("_id"))
        item_type = item.get("type")
        if not content_id or item_type not in {"movie", "series"}:
            continue
        if not item.get("removed") and not item.get("temp"):
            snapshot.library_ids.add(content_id)

        state = item.get("state") if isinstance(item.get("state"), dict) else {}
        watched_at = _parse_stremio_timestamp(state.get("lastWatched"))
        if item_type == "movie":
            if _as_int(state.get("timesWatched")) > 0 or _as_int(
                state.get("flaggedWatched")
            ) > 0:
                snapshot.watched_movies[content_id] = WatchedMovie(
                    content_id=content_id,
                    title=str(item.get("name") or content_id),
                    watched_at=watched_at,
                )
            continue

        metadata_id = _series_metadata_id(item, content_id, user=user)
        has_episode_state = state.get("watched") not in (None, "") or state.get(
            "video_id"
        ) not in (None, "")
        if not metadata_id.startswith("tt") and not has_episode_state:
            snapshot.series_video_ids[content_id] = []
            snapshot.series_watched[content_id] = set()
            continue
        metadata = metadata_cache.get(metadata_id, _MISSING)
        if metadata is _MISSING:
            try:
                metadata = cinemeta_getter(metadata_id)
            except Exception:
                metadata = None
                metadata_failures.add(metadata_id)
            metadata_cache[metadata_id] = metadata
        videos = _sorted_videos(metadata or {})
        video_ids = [str(video["id"]) for video in videos]
        if state.get("watched") not in (None, "") and not video_ids:
            snapshot.metadata_failures.add(content_id)
        if metadata_id in metadata_failures:
            snapshot.metadata_failures.add(content_id)
        if metadata is not None:
            snapshot.series_metadata_available.add(content_id)
        snapshot.series_video_ids[content_id] = video_ids
        state_valid = _valid_watched_bitfield(state.get("watched"), video_ids)
        if state_valid:
            snapshot.series_state_valid.add(content_id)
        watched_ids = (
            decode_watched_bitfield(state.get("watched"), video_ids)
            if state_valid
            else set()
        )
        pairs = {
            parts
            for video in videos
            if str(video["id"]) in watched_ids
            for parts in [_video_parts(video)]
            if parts is not None
        }
        snapshot.series_watched[content_id] = pairs
        for season_number, episode_number in pairs:
            video = next(
                (
                    video
                    for video in videos
                    if _video_parts(video) == (season_number, episode_number)
                ),
                {},
            )
            episode_id = (content_id, season_number, episode_number)
            snapshot.watched_episodes[episode_id] = WatchedEpisode(
                content_id=content_id,
                title=str(video.get("title") or video.get("name") or item.get("name") or content_id),
                season_number=season_number,
                episode_number=episode_number,
                watched_at=watched_at,
            )
    return snapshot


def build_outbound_items(
    local: LocalSnapshot,
    remote_items: list[dict],
    intents: list,
    *,
    cinemeta_getter: Callable[[str], dict],
    initial: bool,
    warnings: list[str] | None = None,
) -> list[dict]:
    now = _serialize_timestamp(django_timezone.now())
    remote_by_id = {
        (item.get("type"), content_id): item
        for item in remote_items
        if isinstance(item, dict)
        for content_id in [_content_id(item.get("_id"))]
        if content_id and item.get("type") in {"movie", "series"}
    }
    candidates: dict[tuple[str, str], dict] = {}
    candidate_aliases: dict[tuple[str, str], tuple[str, str]] = {}
    candidate_sources: dict[tuple[str, str], dict] = {}

    def add_warning(message):
        if warnings is not None and message not in warnings:
            warnings.append(message)

    def discard_candidate(candidate):
        for candidate_key, current in list(candidates.items()):
            if current is candidate:
                candidates.pop(candidate_key, None)
                candidate_sources.pop(candidate_key, None)

    def candidate_for(
        content_id: str,
        item_type: str,
        title: str,
        *,
        aliases=(),
        poster=None,
        in_library: bool,
    ):
        identity_keys = list(dict.fromkeys([content_id, *aliases]))
        scoped_identity_keys = [(item_type, key) for key in identity_keys]
        candidate_key = next(
            (
                candidate_aliases[key]
                for key in scoped_identity_keys
                if key in candidate_aliases
            ),
            (item_type, content_id),
        )
        remote_item = next(
            (remote_by_id[key] for key in scoped_identity_keys if key in remote_by_id),
            None,
        )
        candidate = dict(
            candidates.get(candidate_key)
            or remote_item
            or _new_item(
                content_id,
                item_type,
                title,
                now,
                in_library=in_library,
                poster=poster,
            )
        )
        if poster and not candidate.get("poster"):
            candidate["poster"] = poster
        state = dict(candidate.get("state") or {})
        candidate["state"] = state
        candidates[candidate_key] = candidate
        if remote_item is not None:
            candidate_sources[candidate_key] = remote_item
        for key in scoped_identity_keys:
            candidate_aliases[key] = candidate_key
        return candidate, state

    for state in [*local.movie_watchlist, *local.show_watchlist]:
        content_id = _model_imdb_id(state.movie if hasattr(state, "movie") else state.show)
        if not content_id:
            continue
        item_type = "movie" if hasattr(state, "movie") else "series"
        title = state.movie.title if item_type == "movie" else state.show.name
        model = state.movie if item_type == "movie" else state.show
        candidate, _ = candidate_for(
            content_id,
            item_type,
            title,
            aliases=_model_content_aliases(model),
            poster=_model_poster(model),
            in_library=True,
        )
        candidate["removed"] = False
        candidate["temp"] = False

    for state in local.movie_history:
        if not state.is_seen:
            continue
        content_id = _model_imdb_id(state.movie)
        if not content_id:
            continue
        candidate, remote_state = candidate_for(
            content_id,
            "movie",
            state.movie.title,
            aliases=_model_content_aliases(state.movie),
            poster=_model_poster(state.movie),
            in_library=False,
        )
        remote_state["timesWatched"] = max(1, _as_int(remote_state.get("timesWatched")))
        remote_last_watched = _parse_stremio_timestamp(remote_state.get("lastWatched"))
        if state.seen_at is not None and (
            remote_last_watched is None or state.seen_at > remote_last_watched
        ):
            remote_state["lastWatched"] = _serialize_timestamp(state.seen_at)
        candidate["state"] = remote_state

    dropped_content_ids = {
        content_id
        for state in local.show_dropped
        for content_id in [_model_imdb_id(state.show)]
        if content_id
    }
    episode_groups: dict[str, list] = {}
    episode_group_aliases: dict[str, list[str]] = {}
    for state in local.episode_history:
        show_id = _model_imdb_id(state.episode.show)
        if show_id:
            episode_groups.setdefault(show_id, []).append(state)
            episode_group_aliases.setdefault(
                show_id,
                _model_content_aliases(state.episode.show),
            )
    for show_id, episode_states in episode_groups.items():
        show = episode_states[0].episode.show
        candidate, remote_state = candidate_for(
            show_id,
            "series",
            show.name,
            aliases=episode_group_aliases.get(show_id, []),
            poster=_model_poster(show),
            in_library=False,
        )
        metadata = _safe_metadata(cinemeta_getter, show_id)
        videos = _sorted_videos(metadata or {})
        video_ids = [str(video["id"]) for video in videos]
        if not video_ids:
            add_warning(f"Cinemeta metadata unavailable for {show_id}")
            if candidate.get("temp"):
                discard_candidate(candidate)
            continue
        serialized_watched = remote_state.get("watched")
        if serialized_watched not in (None, "") and not _valid_watched_bitfield(
            serialized_watched,
            video_ids,
        ):
            add_warning(f"Remote episode state is invalid for {show_id}")
            if candidate.get("temp"):
                discard_candidate(candidate)
            continue
        watched_ids = decode_watched_bitfield(serialized_watched, video_ids)
        latest_seen_at = None
        for state in episode_states:
            matching = next(
                (
                    video
                    for video in videos
                    if _video_parts(video)
                    == (state.episode.season_number, state.episode.episode_number)
                ),
                None,
            )
            if matching:
                watched_ids.add(str(matching["id"]))
            if state.seen_at and (latest_seen_at is None or state.seen_at > latest_seen_at):
                latest_seen_at = state.seen_at
        remote_state["watched"] = encode_watched_bitfield(watched_ids, video_ids)
        remote_last_watched = _parse_stremio_timestamp(remote_state.get("lastWatched"))
        if latest_seen_at is not None and (not initial or remote_last_watched is not None) and (
            remote_last_watched is None or latest_seen_at > remote_last_watched
        ):
            remote_state["lastWatched"] = _serialize_timestamp(latest_seen_at)
        candidate["state"] = remote_state
        if show_id in dropped_content_ids:
            candidate["temp"] = True

    for dropped_state in local.show_dropped:
        content_id = _model_imdb_id(dropped_state.show)
        if not content_id:
            continue
        aliases = _model_content_aliases(dropped_state.show)
        remote_item = next(
            (
                remote_by_id[("series", alias)]
                for alias in [content_id, *aliases]
                if ("series", alias) in remote_by_id
            ),
            None,
        )
        if remote_item is None:
            continue
        candidate, _ = candidate_for(
            content_id,
            "series",
            dropped_state.show.name,
            aliases=aliases,
            poster=_model_poster(dropped_state.show),
            in_library=False,
        )
        if not candidate.get("temp") and not candidate.get("removed"):
            candidate["removed"] = True
            candidate["temp"] = False

    for intent in intents:
        kind = str(intent.kind)
        payload = intent.payload if isinstance(intent.payload, dict) else {}
        content_id = _payload_content_id(payload, kind)
        if not content_id:
            continue
        item_type = "movie" if kind.startswith("movie_") else "series"
        title = _payload_title(payload, content_id)
        candidate, state = candidate_for(
            content_id,
            item_type,
            title,
            aliases=_payload_content_aliases(payload, kind),
            in_library=bool(intent.desired and kind.endswith("watchlist")),
        )
        if kind.endswith("watchlist"):
            candidate["removed"] = not bool(intent.desired)
            candidate["temp"] = False
        elif kind == StremioSyncIntent.Kind.MOVIE_HISTORY:
            state["timesWatched"] = 1 if intent.desired else 0
            if not intent.desired:
                state["lastWatched"] = None
        elif kind == StremioSyncIntent.Kind.EPISODE_HISTORY:
            applied = _apply_episode_intent(
                state,
                content_id,
                payload,
                bool(intent.desired),
                cinemeta_getter,
            )
            if not applied:
                add_warning(f"Cinemeta metadata unavailable for {content_id}")
                if candidate.get("temp"):
                    discard_candidate(candidate)
                continue
        candidate["state"] = state

    changes = []
    for content_id, candidate in candidates.items():
        candidate["_mtime"] = now
        existing = candidate_sources.get(content_id)
        if existing is None:
            existing = remote_by_id.get(
                (candidate.get("type"), _content_id(candidate.get("_id")))
            )
        if existing is None or not _same_item(existing, candidate):
            changes.append(candidate)
    return changes


def sync_account(account_id: int, *, client_factory=None) -> SyncReport:
    account = StremioAccount.objects.select_related("user").get(id=account_id)
    client = (
        client_factory(account)
        if client_factory is not None
        else StremioClient(account.auth_key)
    )
    started_at = django_timezone.now()
    initial = not account.initial_sync_complete or account.library_synced_at is None
    if initial:
        remote_items = client.datastore_get(all_items=True)
    else:
        changed_ids = _changed_ids(client.datastore_meta(), account.library_synced_at)
        remote_items = client.datastore_get(ids=changed_ids) if changed_ids else []

    getter = client.get_cinemeta_series
    report = SyncReport()
    remote = normalize_items(remote_items, cinemeta_getter=getter, user=account.user)
    report.warnings.extend(
        f"Cinemeta metadata unavailable for {content_id}"
        for content_id in sorted(remote.metadata_failures)
    )
    with cachalot_disabled():
        local_before = _collect_local_snapshot(account.user)
        intents = list(
            StremioSyncIntent.objects.filter(user=account.user).order_by("updated_at", "id")
        )
        with suppress_local_intents():
            _apply_remote(account.user, remote, local_before, intents, report, initial=initial, getter=getter)
        local_after = _collect_local_snapshot(account.user)
        if initial:
            all_remote_items = remote_items
        else:
            known_ids = {
                content_id
                for item in remote_items
                for content_id in [_content_id(item.get("_id"))]
                if content_id
            }
            missing_ids = set()
            for aliases in _outbound_content_id_groups(local_after, intents):
                if not aliases & known_ids:
                    missing_ids.update(aliases)
            missing_ids = sorted(missing_ids)
            fetched_items = client.datastore_get(ids=missing_ids) if missing_ids else []
            all_remote_items = _merge_remote_items(remote_items, fetched_items)
        changes = build_outbound_items(
            local_after,
            all_remote_items,
            intents,
            cinemeta_getter=getter,
            initial=initial,
            warnings=report.warnings,
        )
        if changes:
            client.datastore_put(changes)
        report.items_pushed = len(changes)
        final_remote = _merge_remote_items(all_remote_items, changes)
        _acknowledge_intents(intents, final_remote, getter, user=account.user)

    if report.warnings:
        account.last_synced_at = started_at
        account.sync_status = StremioAccount.SyncStatus.ERROR
        account.last_error = "; ".join(report.warnings)
        account.save(update_fields=["last_synced_at", "sync_status", "last_error", "updated_at"])
        report.initial_sync_complete = account.initial_sync_complete
        return report

    account.initial_sync_complete = True
    account.library_synced_at = started_at
    account.last_synced_at = started_at
    account.sync_status = StremioAccount.SyncStatus.OK
    account.last_error = ""
    account.save(
        update_fields=[
            "initial_sync_complete",
            "library_synced_at",
            "last_synced_at",
            "sync_status",
            "last_error",
            "updated_at",
        ]
    )
    report.initial_sync_complete = True
    return report


def _apply_remote(user, remote, local, intents, report, *, initial, getter):
    movie_cache: dict[str, Movie] = {}
    show_cache: dict[str, Show] = {}

    def ensure_movie(content_id, title):
        if content_id not in movie_cache:
            try:
                movie_cache[content_id], created = _ensure_movie(content_id, title, user=user)
                report.movies_imported += int(created)
            except (ProviderError, ValueError) as exc:
                report.warnings.append(f"Movie import failed for {content_id}: {exc}")
                return None
        return movie_cache[content_id]

    def ensure_show(content_id, title):
        if content_id not in show_cache:
            try:
                show_cache[content_id], created = _ensure_show(content_id, title, user=user)
                report.shows_imported += int(created)
            except (ProviderError, ValueError) as exc:
                report.warnings.append(f"Show import failed for {content_id}: {exc}")
                return None
        return show_cache[content_id]

    for item in remote.items:
        content_id = _content_id(item.get("_id"))
        if not content_id or item.get("type") not in {"movie", "series"}:
            continue
        if item.get("temp") and not item.get("removed") and content_id not in remote.library_ids:
            has_watched_state = (
                content_id in remote.watched_movies
                if item.get("type") == "movie"
                else bool(remote.series_watched.get(content_id))
            )
            if not has_watched_state:
                continue
        title = str(item.get("name") or content_id)
        if item.get("type") == "movie":
            tombstone = bool(item.get("removed"))
            if tombstone:
                movie = _find_movie(content_id, user=user)
                if movie is None:
                    continue
                state = UserMovie.objects.filter(user=user, movie=movie).first()
                if state is None:
                    continue
            else:
                movie = ensure_movie(content_id, title)
                if movie is None:
                    continue
                state, _ = UserMovie.objects.get_or_create(user=user, movie=movie)
            if content_id in remote.library_ids and not _pending(
                intents, StremioSyncIntent.Kind.MOVIE_WATCHLIST, content_id
            ) is False:
                state.on_watchlist = True
                state.watchlist_added_at = state.watchlist_added_at or django_timezone.now()
            elif not initial and content_id not in remote.library_ids and _pending(
                intents, StremioSyncIntent.Kind.MOVIE_WATCHLIST, content_id
            ) is not True:
                state.on_watchlist = False
                state.watchlist_added_at = None
            watched = None if tombstone else remote.watched_movies.get(content_id)
            if watched is not None and _pending(
                intents, StremioSyncIntent.Kind.MOVIE_HISTORY, content_id
            ) is not False:
                state.is_seen = True
                state.on_watchlist = False
                state.watchlist_added_at = None
                if watched.watched_at and (state.seen_at is None or watched.watched_at > state.seen_at):
                    state.seen_at = watched.watched_at
            elif (
                not initial
                and not (item.get("removed") or item.get("temp"))
                and _movie_state_is_zero(item)
                and content_id in {key for key in _remote_ids(remote.items, "movie")}
                and _pending(
                    intents, StremioSyncIntent.Kind.MOVIE_HISTORY, content_id
                ) is not True
            ):
                state.is_seen = False
                state.seen_at = None
            state.save(update_fields=["on_watchlist", "watchlist_added_at", "is_seen", "seen_at", "updated_at"])
            continue

        if item.get("removed"):
            show = _find_show(content_id, user=user)
            if show is None:
                continue
            user_show = UserShow.objects.filter(user=user, show=show).first()
            if user_show is None:
                continue
        else:
            show = ensure_show(content_id, title)
            if show is None:
                continue
            user_show, _ = UserShow.objects.get_or_create(user=user, show=show)
        pending_watchlist = _pending(
            intents, StremioSyncIntent.Kind.SHOW_WATCHLIST, content_id
        )
        if pending_watchlist is True:
            user_show.on_watchlist = True
        elif pending_watchlist is False or user_show.status == UserShow.Status.DROPPED:
            user_show.on_watchlist = False
        elif content_id in remote.library_ids:
            user_show.on_watchlist = True
        elif not initial and content_id not in remote.library_ids:
            user_show.on_watchlist = False
        user_show.save(update_fields=["on_watchlist", "updated_at"])

        if item.get("removed"):
            continue

        watched_pairs = remote.series_watched.get(content_id, set())
        for season_number, episode_number in watched_pairs:
            if _pending_episode(intents, content_id, season_number, episode_number) is False:
                continue
            episode = _ensure_episode(show, season_number, episode_number, getter, content_id)
            if episode is None:
                continue
            watched = remote.watched_episodes[(content_id, season_number, episode_number)]
            state, created = UserEpisode.objects.get_or_create(
                user=user,
                episode=episode,
                defaults={"seen_at": watched.watched_at or django_timezone.now()},
            )
            if not created and watched.watched_at and (state.seen_at is None or watched.watched_at > state.seen_at):
                state.seen_at = watched.watched_at
                state.save(update_fields=["seen_at"])
            report.episodes_marked += int(created)

        if not initial and content_id in remote.series_state_valid:
            keep = {
                (season_number, episode_number)
                for show_id, season_number, episode_number in remote.watched_episodes
                if show_id == content_id
            }
            for state in UserEpisode.objects.filter(user=user, episode__show=show):
                pair = (state.episode.season_number, state.episode.episode_number)
                if pair not in keep and _pending_episode(intents, content_id, *pair) is not True:
                    state.delete()


def _movie_lookup(content_id):
    if content_id.startswith("tt"):
        return {"imdb_id": content_id}
    if content_id.startswith("tvdb:"):
        return {"tvdb_id": content_id[5:]}
    return {"tmdb_id": content_id[5:]}


def _show_lookup(content_id):
    if content_id.startswith("tt"):
        return {"imdb_id": content_id}
    if content_id.startswith("tvdb:"):
        return {"tvdb_id": content_id[5:]}
    return {"tmdb_id": content_id[5:]}


def _first_catalog_for_user(queryset, user=None):
    if user is not None:
        user_record = queryset.filter(user_states__user=user).first()
        if user_record is not None:
            return user_record
    return queryset.first()


def _find_movie(content_id, *, user=None):
    return _first_catalog_for_user(Movie.objects.filter(**_movie_lookup(content_id)), user)


def _find_movie_by_ids(*, imdb_id=None, tmdb_id=None, tvdb_id=None, user=None):
    identity_query = Q()
    for field_name, value in {
        "imdb_id": imdb_id,
        "tmdb_id": tmdb_id,
        "tvdb_id": tvdb_id,
    }.items():
        if isinstance(value, (str, int)) and value:
            identity_query |= Q(**{field_name: str(value)})
    return _first_catalog_for_user(Movie.objects.filter(identity_query), user) if identity_query else None


def _merge_movie_ids(movie, *, imdb_id=None, tmdb_id=None, tvdb_id=None):
    update_fields = []
    for field_name, value in {
        "imdb_id": imdb_id,
        "tmdb_id": tmdb_id,
        "tvdb_id": tvdb_id,
    }.items():
        if value and not getattr(movie, field_name):
            setattr(movie, field_name, str(value))
            update_fields.append(field_name)
    if update_fields:
        movie.save(update_fields=update_fields)


def _find_show_by_ids(*, imdb_id=None, tmdb_id=None, tvdb_id=None, user=None):
    identity_query = Q()
    for field_name, value in {
        "imdb_id": imdb_id,
        "tmdb_id": tmdb_id,
        "tvdb_id": tvdb_id,
    }.items():
        if isinstance(value, (str, int)) and value:
            identity_query |= Q(**{field_name: str(value)})
    return _first_catalog_for_user(Show.objects.filter(identity_query), user) if identity_query else None


def _merge_show_ids(show, *, imdb_id=None, tmdb_id=None, tvdb_id=None):
    update_fields = []
    for field_name, value in {
        "imdb_id": imdb_id,
        "tmdb_id": tmdb_id,
        "tvdb_id": tvdb_id,
    }.items():
        if value and not getattr(show, field_name):
            setattr(show, field_name, str(value))
            update_fields.append(field_name)
    if update_fields:
        show.save(update_fields=update_fields)


def _find_show(content_id, *, user=None):
    return _first_catalog_for_user(Show.objects.filter(**_show_lookup(content_id)), user)


def _ensure_movie(content_id: str, title: str, *, user=None) -> tuple[Movie, bool]:
    movie = _find_movie(content_id, user=user)
    if movie is not None:
        return movie, False
    if content_id.startswith("tvdb:"):
        movie = movie_services.import_movie(
            "tvdb",
            content_id[5:],
            language=PROVIDER_DEFAULT_LANGUAGES["tvdb"],
        )
    else:
        tmdb_provider = TMDBProvider()
        tmdb_id = (
            content_id[5:]
            if content_id.startswith("tmdb:")
            else tmdb_provider.find_by_imdb_id(content_id, "movie")
        )
        if not tmdb_id:
            raise ValueError(f"Stremio movie {content_id} has no TMDB match")
        detail = tmdb_provider.fetch_detail(
            tmdb_id,
            language=PROVIDER_DEFAULT_LANGUAGES["tmdb"],
            media_type="movie",
        )
        movie = _find_movie_by_ids(
            imdb_id=content_id if content_id.startswith("tt") else detail.imdb_id,
            tmdb_id=detail.tmdb_id or tmdb_id,
            tvdb_id=detail.tvdb_id,
            user=user,
        )
        if movie is not None:
            _merge_movie_ids(
                movie,
                imdb_id=content_id if content_id.startswith("tt") else detail.imdb_id,
                tmdb_id=detail.tmdb_id or tmdb_id,
                tvdb_id=detail.tvdb_id,
            )
            return movie, False
        movie = movie_services.import_movie(
            "tmdb",
            tmdb_id,
            language=PROVIDER_DEFAULT_LANGUAGES["tmdb"],
            base_detail=detail,
        )
    if content_id.startswith("tt") and not movie.imdb_id:
        movie.imdb_id = content_id
        movie.save(update_fields=["imdb_id"])
    return movie, True


def _ensure_show(content_id: str, title: str, *, user=None) -> tuple[Show, bool]:
    show = _find_show(content_id, user=user)
    if show is not None:
        return show, False
    if content_id.startswith("tvdb:"):
        show = tv_services.import_show(
            content_id[5:],
            provider="tvdb",
            language=PROVIDER_DEFAULT_LANGUAGES["tvdb"],
        )
    else:
        tmdb_provider = TMDBProvider()
        tmdb_id = (
            content_id[5:]
            if content_id.startswith("tmdb:")
            else tmdb_provider.find_by_imdb_id(content_id, "tv")
        )
        if not tmdb_id:
            raise ValueError(f"Stremio series {content_id} has no TMDB match")
        detail = tmdb_provider.fetch_detail(
            tmdb_id,
            language=PROVIDER_DEFAULT_LANGUAGES["tmdb"],
            media_type="tv",
        )
        show = _find_show_by_ids(
            imdb_id=content_id if content_id.startswith("tt") else detail.imdb_id,
            tmdb_id=detail.tmdb_id or tmdb_id,
            tvdb_id=detail.tvdb_id,
            user=user,
        )
        if show is not None:
            _merge_show_ids(
                show,
                imdb_id=content_id if content_id.startswith("tt") else detail.imdb_id,
                tmdb_id=detail.tmdb_id or tmdb_id,
                tvdb_id=detail.tvdb_id,
            )
            return show, False
        show = tv_services.import_show(
            tmdb_id,
            provider="tmdb",
            language=PROVIDER_DEFAULT_LANGUAGES["tmdb"],
            base_detail=detail,
        )
    if content_id.startswith("tt") and not show.imdb_id:
        show.imdb_id = content_id
        show.save(update_fields=["imdb_id"])
    return show, True


def _ensure_episode(show, season_number, episode_number, getter, content_id):
    season, _ = Season.objects.get_or_create(show=show, season_number=season_number)
    episode, _ = Episode.objects.get_or_create(
        show=show,
        season=season,
        season_number=season_number,
        episode_number=episode_number,
        defaults={"name": f"Episode {episode_number}"},
    )
    return episode


def _apply_episode_intent(state, content_id, payload, desired, getter):
    season, episode = _payload_episode_parts(payload)
    if season < 0 or episode <= 0:
        return False
    metadata = _safe_metadata(getter, content_id)
    videos = _sorted_videos(metadata or {})
    video_ids = [str(video["id"]) for video in videos]
    if not video_ids:
        return False
    serialized_watched = state.get("watched")
    if serialized_watched not in (None, "") and not _valid_watched_bitfield(
        serialized_watched,
        video_ids,
    ):
        return False
    watched_ids = (
        decode_watched_bitfield(serialized_watched, video_ids)
        if serialized_watched not in (None, "")
        else set()
    )
    matching = next((video for video in videos if _video_parts(video) == (season, episode)), None)
    if matching is None:
        return False
    if desired:
        watched_ids.add(str(matching["id"]))
    else:
        watched_ids.discard(str(matching["id"]))
    state["watched"] = encode_watched_bitfield(watched_ids, video_ids)
    return True


def _delete_intent_if_unchanged(intent):
    StremioSyncIntent.objects.filter(
        id=intent.id,
        updated_at=intent.updated_at,
    ).delete()


def _acknowledge_intents(intents, remote_items, getter, *, user=None):
    snapshot = normalize_items(remote_items, cinemeta_getter=getter, user=user)
    for index, intent in enumerate(intents):
        if any(
            _same_intent_target(intent, newer)
            for newer in intents[index + 1 :]
        ):
            _delete_intent_if_unchanged(intent)
            continue
        content_id = _payload_content_id(intent.payload, str(intent.kind))
        if not content_id:
            continue
        item_type = "movie" if str(intent.kind).startswith("movie_") else "series"
        snapshot_id = _snapshot_content_id(
            snapshot.items,
            intent.payload,
            str(intent.kind),
        )
        resolved_content_id = snapshot_id or content_id
        if intent.kind == StremioSyncIntent.Kind.MOVIE_WATCHLIST:
            actual = resolved_content_id in _remote_ids(snapshot.items, item_type)
        elif intent.kind == StremioSyncIntent.Kind.SHOW_WATCHLIST:
            actual = resolved_content_id in _remote_ids(snapshot.items, item_type)
        elif intent.kind == StremioSyncIntent.Kind.MOVIE_HISTORY:
            actual = resolved_content_id in snapshot.watched_movies
        elif intent.kind == StremioSyncIntent.Kind.EPISODE_HISTORY:
            if snapshot_id is not None and (
                resolved_content_id in snapshot.metadata_failures
                or resolved_content_id not in snapshot.series_state_valid
            ):
                continue
            season, episode = _payload_episode_parts(intent.payload)
            actual = (resolved_content_id, season, episode) in snapshot.watched_episodes
        else:
            continue
        if actual == bool(intent.desired):
            _delete_intent_if_unchanged(intent)


def _same_intent_target(left, right):
    if left.kind != right.kind:
        return False
    kind = str(left.kind)
    if kind == StremioSyncIntent.Kind.EPISODE_HISTORY and (
        _payload_episode_parts(left.payload) != _payload_episode_parts(right.payload)
    ):
        return False
    return bool(
        set(_payload_content_aliases(left.payload, kind))
        & set(_payload_content_aliases(right.payload, kind))
    )


def _pending(intents, kind, content_id):
    for intent in reversed(intents):
        if intent.kind == kind and content_id in _payload_content_aliases(
            intent.payload,
            str(kind),
        ):
            return bool(intent.desired)
    return None


def _pending_episode(intents, content_id, season, episode):
    for intent in reversed(intents):
        if intent.kind != StremioSyncIntent.Kind.EPISODE_HISTORY:
            continue
        if content_id not in _payload_content_aliases(
            intent.payload,
            str(intent.kind),
        ):
            continue
        if _payload_episode_parts(intent.payload) == (season, episode):
            return bool(intent.desired)
    return None


def _changed_ids(metadata, synced_at):
    cutoff = synced_at - timedelta(minutes=5)
    result = []
    for row in metadata:
        if not isinstance(row, (list, tuple)) or not row:
            continue
        content_id = _content_id(row[0])
        if not content_id:
            continue
        updated_at = _parse_stremio_timestamp(row[1] if len(row) > 1 else None)
        if updated_at is None or updated_at >= cutoff:
            result.append(content_id)
    return list(dict.fromkeys(result))


def _outbound_content_id_groups(local, intents):
    content_id_groups = []
    for state in [
        *local.movie_watchlist,
        *local.movie_history,
        *local.show_watchlist,
        *local.show_dropped,
        *local.episode_history,
    ]:
        model = state.movie if hasattr(state, "movie") else state.show
        aliases = _model_content_aliases(model)
        if aliases:
            content_id_groups.append(set(aliases))
    for intent in intents:
        kind = str(intent.kind)
        payload = intent.payload if isinstance(intent.payload, dict) else {}
        aliases = _payload_content_aliases(payload, kind)
        if aliases:
            content_id_groups.append(set(aliases))
    return content_id_groups


def _merge_remote_items(items, changes):
    by_id = {
        (item.get("type"), item.get("_id")): dict(item)
        for item in items
        if item.get("_id")
    }
    for item in changes:
        by_id[(item.get("type"), item["_id"])] = item
    return list(by_id.values())


def _new_item(content_id, item_type, title, now, *, in_library, poster=None):
    item = {
        "_id": content_id,
        "name": title or content_id,
        "type": item_type,
        "removed": False,
        "temp": not in_library,
        "_ctime": now,
        "_mtime": now,
        "state": {
            "lastWatched": None,
            "timeWatched": 0,
            "timesWatched": 0,
            "timeOffset": 0,
            "overallTimeWatched": 0,
            "flaggedWatched": 0,
            "duration": 0,
            "watched": None,
            "noNotif": False,
        },
        "behaviorHints": {},
    }
    if poster:
        item["poster"] = poster
    return item


def _same_item(left, right):
    return {key: value for key, value in left.items() if key != "_mtime"} == {
        key: value for key, value in right.items() if key != "_mtime"
    }


def _content_id(value):
    if value is None:
        return None
    value = str(value).strip()
    if value.lower().startswith("imdb:"):
        value = value[5:]
    if re.fullmatch(r"tt\d+", value, flags=re.IGNORECASE):
        return value
    if value.casefold().startswith("tmdb:") and value[5:].isdigit():
        return f"tmdb:{value[5:]}"
    if value.casefold().startswith("tvdb:") and value[5:].isdigit():
        return f"tvdb:{value[5:]}"
    return None


def _model_imdb_id(model):
    imdb_id = getattr(model, "imdb_id", None)
    if imdb_id:
        normalized = _content_id(imdb_id)
        if normalized:
            return normalized
    tmdb_id = getattr(model, "tmdb_id", None)
    if tmdb_id:
        return f"tmdb:{tmdb_id}"
    tvdb_id = getattr(model, "tvdb_id", None)
    return f"tvdb:{tvdb_id}" if tvdb_id else None


def _model_poster(model):
    poster = getattr(model, "poster_url", None)
    return str(poster) if poster else None


def _model_content_aliases(model):
    aliases = []
    imdb_id = _content_id(getattr(model, "imdb_id", None))
    if imdb_id:
        aliases.append(imdb_id)
    tmdb_id = getattr(model, "tmdb_id", None)
    if tmdb_id:
        aliases.append(f"tmdb:{tmdb_id}")
    tvdb_id = getattr(model, "tvdb_id", None)
    if tvdb_id:
        aliases.append(f"tvdb:{tvdb_id}")
    return list(dict.fromkeys(aliases))


def _payload_content_id(payload, kind):
    ids = payload.get("ids") if isinstance(payload, dict) else {}
    if kind == StremioSyncIntent.Kind.EPISODE_HISTORY:
        ids = (payload.get("show") or {}).get("ids") or {}
    if not isinstance(ids, dict):
        return None
    if ids.get("imdb"):
        return _content_id(ids["imdb"])
    if ids.get("tmdb"):
        return f"tmdb:{ids['tmdb']}"
    if ids.get("tvdb"):
        return f"tvdb:{ids['tvdb']}"
    return None


def _payload_content_aliases(payload, kind):
    ids = payload.get("ids") if isinstance(payload, dict) else {}
    if kind == StremioSyncIntent.Kind.EPISODE_HISTORY:
        ids = (payload.get("show") or {}).get("ids") or {}
    if not isinstance(ids, dict):
        return []
    aliases = []
    if ids.get("imdb"):
        content_id = _content_id(ids["imdb"])
        if content_id:
            aliases.append(content_id)
    if ids.get("tmdb"):
        aliases.append(f"tmdb:{ids['tmdb']}")
    if ids.get("tvdb"):
        aliases.append(f"tvdb:{ids['tvdb']}")
    return list(dict.fromkeys(aliases))


def _snapshot_content_id(items, payload, kind):
    aliases = set(_payload_content_aliases(payload, kind))
    if not aliases:
        return None
    expected_type = "movie" if kind.startswith("movie_") else "series"
    for item in items:
        if item.get("type") != expected_type:
            continue
        content_id = _content_id(item.get("_id"))
        if content_id in aliases:
            return content_id
    return None


def _payload_title(payload, content_id):
    return str(payload.get("title") or payload.get("name") or content_id)


def _payload_episode_parts(payload):
    seasons = payload.get("seasons") or []
    season = seasons[0] if seasons else {}
    episodes = season.get("episodes") or []
    episode = episodes[0] if episodes else {}
    return _as_int(season.get("number")), _as_int(episode.get("number"))


def _remote_ids(items, item_type):
    return {
        content_id
        for item in items
        if item.get("type") == item_type
        and not item.get("removed")
        and not item.get("temp")
        for content_id in [_content_id(item.get("_id"))]
        if content_id
    }


def _series_metadata_id(item, content_id, *, user=None):
    if content_id.startswith("tt"):
        return content_id
    state = item.get("state") if isinstance(item.get("state"), dict) else {}
    for candidate in (state.get("video_id"), state.get("watched")):
        value = str(candidate or "")
        candidate_id = value.split(":", 1)[0]
        if re.fullmatch(r"tt\d+", candidate_id, flags=re.IGNORECASE):
            return candidate_id
    show = _first_catalog_for_user(
        Show.objects.filter(**_show_lookup(content_id))
        .exclude(imdb_id__isnull=True)
        .exclude(imdb_id=""),
        user,
    )
    if show is not None:
        imdb_id = _content_id(show.imdb_id)
        if imdb_id:
            return imdb_id
    return content_id


def _sorted_videos(metadata):
    return sorted(
        [
            video
            for video in (metadata.get("videos") or [])
            if isinstance(video, dict) and video.get("id") and _video_parts(video) is not None
        ],
        key=lambda video: (*_video_parts(video), str(video.get("released") or "")),
    )


def _video_parts(video):
    try:
        season = video.get("season")
        episode = video.get("episode")
        if season is not None and episode is not None:
            return int(season), int(episode)
        parts = str(video.get("id") or "").rsplit(":", 2)
        return int(parts[-2]), int(parts[-1])
    except (TypeError, ValueError):
        return None


def _safe_metadata(getter, content_id):
    try:
        return getter(content_id)
    except Exception:
        return None


def _parse_stremio_timestamp(value):
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value) / 1000, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed


def _serialize_timestamp(value):
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )


def _as_int(value):
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _movie_state_is_zero(item):
    state = item.get("state") if isinstance(item, dict) else None
    if not isinstance(state, dict) or not any(
        key in state for key in ("timesWatched", "flaggedWatched")
    ):
        return False
    for key in ("timesWatched", "flaggedWatched"):
        value = state.get(key)
        if isinstance(value, bool):
            return False
        try:
            if float(value or 0) != 0:
                return False
        except (TypeError, ValueError):
            return False
    return True


def _valid_watched_bitfield(serialized, video_ids):
    if not isinstance(serialized, str) or not serialized or not video_ids:
        return False
    try:
        anchor_video_id, anchor_length_raw, packed = serialized.rsplit(":", 2)
        anchor_length = int(anchor_length_raw)
        decoded = zlib.decompress(base64.b64decode(packed, validate=True))
    except (ValueError, TypeError, zlib.error, binascii.Error):
        return False
    return (
        anchor_video_id in video_ids
        and video_ids.index(anchor_video_id) < anchor_length
        and 0 < anchor_length <= len(video_ids)
        and len(decoded) >= (anchor_length + 7) // 8
    )


_MISSING = object()
