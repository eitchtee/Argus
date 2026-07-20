from dataclasses import dataclass, field
from datetime import datetime, timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from cachalot.api import cachalot_disabled

from apps.catalog.localization import (
    PROVIDER_DEFAULT_LANGUAGES,
)
from apps.catalog.providers.exceptions import ProviderError
from apps.movies import services as movie_services
from apps.movies.models import Movie, UserMovie
from apps.trakt.changes import suppress_local_intents
from apps.trakt.client import TraktClient, TraktSnapshot
from apps.trakt.identities import (
    episode_identity_key,
    episode_payload,
    ids_from_media,
    media_identity_key,
    movie_payload,
    parse_timestamp,
    show_payload,
    unwrap_media,
)
from apps.trakt.models import (
    TraktAccount,
    TraktSyncIntent,
    TraktWatchedEpisode,
)
from apps.tv import services as tv_services
from apps.tv.models import Episode, Season, Show, UserEpisode, UserShow


@dataclass(frozen=True)
class WatchedMovie:
    media: dict
    watched_at: datetime


@dataclass(frozen=True)
class WatchedEpisode:
    show: dict
    episode: dict
    season_number: int
    episode_number: int
    watched_at: datetime


@dataclass
class RemoteSnapshot:
    watchlist_movies: dict[str, dict] = field(default_factory=dict)
    watchlist_shows: dict[str, dict] = field(default_factory=dict)
    watched_shows: dict[str, dict] = field(default_factory=dict)
    watched_movies: dict[str, WatchedMovie] = field(default_factory=dict)
    watched_episodes: dict[str, WatchedEpisode] = field(default_factory=dict)
    dropped_shows: dict[str, dict] = field(default_factory=dict)


@dataclass
class LocalSnapshot:
    movie_watchlist: list[UserMovie]
    movie_history: list[UserMovie]
    show_watchlist: list[UserShow]
    show_dropped: list[UserShow]
    episode_history: list[UserEpisode]


@dataclass
class SyncReport:
    movies_imported: int = 0
    shows_imported: int = 0
    episodes_marked: int = 0
    intents_sent: int = 0
    warnings: list[str] = field(default_factory=list)


def sync_account(account_id: int, *, client_factory=None) -> SyncReport:
    account = TraktAccount.objects.select_related("user").get(id=account_id)
    client = _build_client(account, client_factory=client_factory)
    episode_history_start_at = _episode_history_start_at(account)
    remote = normalize_snapshot(
        client.get_snapshot(episode_history_start_at=episode_history_start_at)
    )
    report = SyncReport()
    initial = not account.initial_sync_complete

    with cachalot_disabled():
        remote = _merge_cached_watched_episodes(account, remote)
        local = _collect_local_snapshot(account.user)
        intents = list(
            TraktSyncIntent.objects.filter(user=account.user).order_by(
                "updated_at", "id"
            )
        )
        with suppress_local_intents():
            _apply_remote_movies(account.user, remote, intents, local, report, initial=initial)
            _apply_remote_shows(account.user, remote, intents, local, report, initial=initial)

        outbound = _build_outbound(
            account.user,
            remote,
            local,
            intents,
            initial=initial,
        )
        report.intents_sent = _send_outbound(client, outbound)
        _acknowledge_intents(intents, remote)
    return report


def normalize_snapshot(snapshot: TraktSnapshot) -> RemoteSnapshot:
    normalized = RemoteSnapshot()
    for raw_item in snapshot.watchlist_movies:
        media = unwrap_media(raw_item, "movie")
        if media:
            normalized.watchlist_movies[media_identity_key(media)] = media
    for raw_item in snapshot.watchlist_shows:
        media = unwrap_media(raw_item, "show")
        if media:
            normalized.watchlist_shows[media_identity_key(media)] = media
    for raw_item in snapshot.dropped_shows:
        media = unwrap_media(raw_item, "show")
        if media:
            normalized.dropped_shows[media_identity_key(media)] = media

    for raw_item in snapshot.watched_shows:
        media = unwrap_media(raw_item, "show")
        if media:
            normalized.watched_shows[media_identity_key(media)] = media

    normalized.watched_movies = _merge_watched_movies(snapshot.watched_movies)
    normalized.watched_episodes = _merge_watched_episodes(
        snapshot.watched_episodes or snapshot.watched_shows
    )
    return normalized


def _merge_latest_watches(records: list[dict], media_type: str = "movie") -> dict[str, datetime]:
    result: dict[str, datetime] = {}
    for record in records:
        media = unwrap_media(record, media_type)
        if not media:
            continue
        key = media_identity_key(media)
        watched_at = _record_timestamp(record)
        if key not in result or watched_at > result[key]:
            result[key] = watched_at
    return result


def _merge_watched_movies(records: list[dict]) -> dict[str, WatchedMovie]:
    result: dict[str, WatchedMovie] = {}
    for record in records:
        media = unwrap_media(record, "movie")
        if not media:
            continue
        key = media_identity_key(media)
        watched_at = _record_timestamp(record)
        current = result.get(key)
        if current is None or watched_at > current.watched_at:
            result[key] = WatchedMovie(media=media, watched_at=watched_at)
    return result


def _merge_watched_episodes(records: list[dict]) -> dict[str, WatchedEpisode]:
    result: dict[str, WatchedEpisode] = {}
    for record in records:
        show = record.get("show") or {}
        if not isinstance(show, dict):
            continue

        direct_episode = record.get("episode")
        if isinstance(direct_episode, dict):
            _merge_watched_episode(
                result,
                show,
                direct_episode,
                season_number=_as_int(direct_episode.get("season"), default=0),
                episode_number=_as_int(direct_episode.get("number"), default=0),
                watched_at=_record_timestamp(record),
            )

        for season in record.get("seasons") or []:
            season_number = _as_int(season.get("number"), default=0)
            for episode in season.get("episodes") or []:
                episode_number = _as_int(episode.get("number"), default=0)
                if season_number < 0 or episode_number <= 0:
                    continue
                _merge_watched_episode(
                    result,
                    show,
                    episode.get("episode") or episode,
                    season_number=season_number,
                    episode_number=episode_number,
                    watched_at=_record_timestamp(episode, fallback=record),
                )
    return result


def _merge_watched_episode(
    result: dict[str, WatchedEpisode],
    show: dict,
    episode: dict,
    *,
    season_number: int,
    episode_number: int,
    watched_at: datetime,
) -> None:
    if season_number < 0 or episode_number <= 0:
        return
    key = episode_identity_key(
        {"show": show},
        season_number=season_number,
        episode_number=episode_number,
    )
    current = result.get(key)
    if current is None or watched_at > current.watched_at:
        result[key] = WatchedEpisode(
            show=show,
            episode=episode,
            season_number=season_number,
            episode_number=episode_number,
            watched_at=watched_at,
        )


def _episode_history_start_at(account: TraktAccount) -> datetime | None:
    cursor = account.episode_history_synced_at or account.last_synced_at
    if cursor is None:
        return None
    return cursor - timedelta(minutes=10)


def _merge_cached_watched_episodes(
    account: TraktAccount,
    remote: RemoteSnapshot,
) -> RemoteSnapshot:
    rows = list(
        TraktWatchedEpisode.objects.filter(account=account).order_by("identity_key")
    )
    cached = {
        row.identity_key: WatchedEpisode(
            show=row.show_data,
            episode=row.episode_data,
            season_number=row.season_number,
            episode_number=row.episode_number,
            watched_at=row.watched_at,
        )
        for row in rows
    }
    by_key = {row.identity_key: row for row in rows}
    to_create = []
    to_update = []
    for key, watched in remote.watched_episodes.items():
        current = cached.get(key)
        if current is not None and current.watched_at >= watched.watched_at:
            continue
        cached[key] = watched
        row = by_key.get(key)
        if row is None:
            to_create.append(
                TraktWatchedEpisode(
                    account=account,
                    identity_key=key,
                    show_data=watched.show,
                    episode_data=watched.episode,
                    season_number=watched.season_number,
                    episode_number=watched.episode_number,
                    watched_at=watched.watched_at,
                )
            )
        else:
            row.show_data = watched.show
            row.episode_data = watched.episode
            row.season_number = watched.season_number
            row.episode_number = watched.episode_number
            row.watched_at = watched.watched_at
            to_update.append(row)
    if to_create:
        TraktWatchedEpisode.objects.bulk_create(to_create, batch_size=500)
    if to_update:
        updated_at = timezone.now()
        for row in to_update:
            row.updated_at = updated_at
        TraktWatchedEpisode.objects.bulk_update(
            to_update,
            [
                "show_data",
                "episode_data",
                "season_number",
                "episode_number",
                "watched_at",
                "updated_at",
            ],
            batch_size=500,
        )
    remote.watched_episodes = cached
    account.episode_history_synced_at = timezone.now()
    account.save(update_fields=["episode_history_synced_at", "updated_at"])
    return remote


def _record_timestamp(record: dict, *, fallback: dict | None = None) -> datetime:
    for candidate in (record, fallback or {}):
        for field_name in (
            "last_watched_at",
            "watched_at",
            "last_updated_at",
            "updated_at",
        ):
            timestamp = parse_timestamp(candidate.get(field_name))
            if timestamp is not None:
                return timestamp
    return timezone.now()


def _apply_remote_movies(user, remote, intents, local, report, *, initial: bool):
    movie_cache: dict[str, Movie] = {}

    def ensure(media):
        cache_key = media_identity_key(media)
        if cache_key not in movie_cache:
            try:
                movie, created = _ensure_movie(user, media)
            except (ProviderError, ValueError) as exc:
                report.warnings.append(f"Movie import failed: {exc}")
                return None
            movie_cache[cache_key] = movie
            if created:
                report.movies_imported += 1
        return movie_cache[cache_key]

    for watched in remote.watched_movies.values():
        movie = ensure(watched.media)
        if movie is None:
            continue
        state, _created = UserMovie.objects.get_or_create(user=user, movie=movie)
        state.is_seen = True
        if state.seen_at is None or watched.watched_at > state.seen_at:
            state.seen_at = watched.watched_at
        state.on_watchlist = False
        state.watchlist_added_at = None
        state.save(
            update_fields=[
                "is_seen",
                "seen_at",
                "on_watchlist",
                "watchlist_added_at",
                "updated_at",
            ]
        )

    for media in remote.watchlist_movies.values():
        movie = ensure(media)
        if movie is None:
            continue
        tokens = _media_tokens(media)
        if _pending_desired(intents, TraktSyncIntent.Kind.MOVIE_WATCHLIST, tokens) is False:
            continue
        if _matching_remote_media(tokens, remote.watched_movies.values()) is not None:
            continue
        state, _created = UserMovie.objects.get_or_create(user=user, movie=movie)
        if not state.is_seen:
            state.on_watchlist = True
            state.watchlist_added_at = timezone.now()
            state.save(update_fields=["on_watchlist", "watchlist_added_at", "updated_at"])

    if not initial:
        for old_state in local.movie_watchlist:
            media = movie_payload(old_state.movie)
            tokens = _media_tokens(media)
            if _matching_remote_media(tokens, remote.watchlist_movies.values()) is not None:
                continue
            if _pending_desired(intents, TraktSyncIntent.Kind.MOVIE_WATCHLIST, tokens) is True:
                continue
            if not old_state.is_seen:
                UserMovie.objects.filter(id=old_state.id).update(
                    on_watchlist=False,
                    watchlist_added_at=None,
                )


def _apply_remote_shows(user, remote, intents, local, report, *, initial: bool):
    show_cache: dict[str, Show] = {}

    def ensure(media):
        cache_key = media_identity_key(media)
        if cache_key not in show_cache:
            try:
                show, created = _ensure_show(user, media)
            except (ProviderError, ValueError) as exc:
                report.warnings.append(f"Show import failed: {exc}")
                return None
            show_cache[cache_key] = show
            if created:
                report.shows_imported += 1
        return show_cache[cache_key]

    all_remote_media = [
        *remote.watched_shows.values(),
        *remote.watchlist_shows.values(),
        *remote.dropped_shows.values(),
        *(watched.show for watched in remote.watched_episodes.values()),
    ]
    for media in all_remote_media:
        ensure(media)

    user_show_cache = {
        user_show.show_id: user_show
        for user_show in UserShow.objects.filter(
            user=user,
            show_id__in=[show.id for show in show_cache.values() if show is not None],
        )
    }

    def ensure_user_show_cached(show, *, status):
        user_show = user_show_cache.get(show.id)
        if user_show is None:
            user_show = UserShow.objects.create(
                user=user,
                show=show,
                status=status,
            )
            user_show_cache[show.id] = user_show
            return user_show
        if user_show.status == UserShow.Status.PAUSED:
            return user_show
        if user_show.status != UserShow.Status.DROPPED or status == UserShow.Status.DROPPED:
            if user_show.status != status:
                user_show.status = status
                user_show.save(update_fields=["status", "updated_at"])
        return user_show

    episode_pairs = []
    for watched in remote.watched_episodes.values():
        show = ensure(watched.show)
        if show is None:
            continue
        status = (
            UserShow.Status.DROPPED
            if _pending_desired(
                intents,
                TraktSyncIntent.Kind.SHOW_DROPPED,
                _media_tokens(watched.show),
            ) is True
            else UserShow.Status.TRACKED
        )
        ensure_user_show_cached(show, status=status)
        episode_pairs.append((watched, show))

    episodes_by_key = _ensure_episodes_batch(episode_pairs)
    if episode_pairs:
        episode_ids = [
            episodes_by_key[(show.id, watched.season_number, watched.episode_number)].id
            for watched, show in episode_pairs
        ]
        user_episodes = {
            state.episode_id: state
            for state in UserEpisode.objects.filter(
                user=user,
                episode_id__in=episode_ids,
            )
        }
        to_create = []
        to_update = []
        for watched, show in episode_pairs:
            episode = episodes_by_key[(show.id, watched.season_number, watched.episode_number)]
            state = user_episodes.get(episode.id)
            if state is None:
                to_create.append(
                    UserEpisode(
                        user=user,
                        episode=episode,
                        seen_at=watched.watched_at,
                    )
                )
                continue
            if state.seen_at is None or watched.watched_at > state.seen_at:
                state.seen_at = watched.watched_at
                to_update.append(state)
        if to_create:
            UserEpisode.objects.bulk_create(
                to_create,
                batch_size=500,
                ignore_conflicts=True,
            )
            report.episodes_marked += len(to_create)
        if to_update:
            UserEpisode.objects.bulk_update(to_update, ["seen_at"], batch_size=500)

    for media in remote.watched_shows.values():
        show = ensure(media)
        if show is None:
            continue
        tokens = _media_tokens(media)
        status = (
            UserShow.Status.DROPPED
            if _pending_desired(intents, TraktSyncIntent.Kind.SHOW_DROPPED, tokens)
            is True
            else UserShow.Status.TRACKED
        )
        ensure_user_show_cached(show, status=status)

    for media in remote.watchlist_shows.values():
        show = ensure(media)
        if show is None:
            continue
        tokens = _media_tokens(media)
        if _pending_desired(intents, TraktSyncIntent.Kind.SHOW_WATCHLIST, tokens) is False:
            continue
        if _pending_desired(intents, TraktSyncIntent.Kind.SHOW_DROPPED, tokens) is True:
            continue
        if _matching_remote_media(tokens, remote.dropped_shows.values()) is not None:
            continue
        user_show = ensure_user_show_cached(show, status=UserShow.Status.TRACKED)
        user_show.on_watchlist = True
        user_show.save(update_fields=["on_watchlist", "updated_at"])

    for watched in remote.watched_episodes.values():
        show = ensure(watched.show)
        if show is None:
            continue
        tokens = _media_tokens(watched.show)
        if _pending_desired(intents, TraktSyncIntent.Kind.SHOW_DROPPED, tokens) is True:
            continue
        ensure_user_show_cached(show, status=UserShow.Status.TRACKED)

    for media in remote.dropped_shows.values():
        show = ensure(media)
        if show is None:
            continue
        tokens = _media_tokens(media)
        if _pending_desired(intents, TraktSyncIntent.Kind.SHOW_DROPPED, tokens) is False:
            continue
        user_show = ensure_user_show_cached(show, status=UserShow.Status.DROPPED)
        if user_show.status != UserShow.Status.PAUSED:
            user_show.status = UserShow.Status.DROPPED
        user_show.on_watchlist = False
        user_show.save(update_fields=["status", "on_watchlist", "updated_at"])

    for old_state in local.show_watchlist:
        media = show_payload(old_state.show)
        tokens = _media_tokens(media)
        if _matching_remote_media(tokens, remote.watchlist_shows.values()) is not None:
            continue
        if _pending_desired(intents, TraktSyncIntent.Kind.SHOW_WATCHLIST, tokens) is True:
            continue
        if not initial:
            UserShow.objects.filter(id=old_state.id).update(on_watchlist=False)

    for old_state in local.show_dropped:
        media = show_payload(old_state.show)
        tokens = _media_tokens(media)
        if _matching_remote_media(tokens, remote.dropped_shows.values()) is not None:
            continue
        if _pending_desired(intents, TraktSyncIntent.Kind.SHOW_DROPPED, tokens) is True:
            continue
        if not initial:
            state = UserShow.objects.filter(id=old_state.id).first()
            if state is None:
                continue
            state.status = UserShow.Status.TRACKED
            state.on_watchlist = (
                _matching_remote_media(tokens, remote.watchlist_shows.values()) is not None
            )
            state.save(update_fields=["status", "on_watchlist", "updated_at"])

def _collect_local_snapshot(user) -> LocalSnapshot:
    return LocalSnapshot(
        movie_watchlist=list(
            UserMovie.objects.filter(user=user, on_watchlist=True, is_seen=False)
            .select_related("movie")
        ),
        movie_history=list(
            UserMovie.objects.filter(user=user, is_seen=True).select_related("movie")
        ),
        show_watchlist=list(
            UserShow.objects.filter(
                user=user,
                on_watchlist=True,
            ).select_related("show")
        ),
        show_dropped=list(
            UserShow.objects.filter(
                user=user,
                status=UserShow.Status.DROPPED,
            ).select_related("show")
        ),
        episode_history=list(
            UserEpisode.objects.filter(user=user)
            .select_related("episode", "episode__show")
        ),
    )


def _ensure_movie(user, media: dict) -> tuple[Movie, bool]:
    movie = _find_by_ids(Movie, media, user=user, user_state_relation="user_states")
    created = movie is None
    if movie is None:
        ids = ids_from_media(media)
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
            raise ValueError("Trakt movie has no TMDB or TVDB identifier")
    _normalize_movie_title(movie, media)
    _save_media_ids(movie, media)
    return movie, created


def _ensure_show(user, media: dict) -> tuple[Show, bool]:
    show = _find_by_ids(Show, media, user=user, user_state_relation="user_states")
    created = show is None
    if show is None:
        ids = ids_from_media(media)
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
            raise ValueError("Trakt show has no TMDB or TVDB identifier")
    _normalize_show_title(show, media)
    _save_media_ids(show, media)
    return show, created


def _normalize_movie_title(movie: Movie, media: dict) -> None:
    default_language = PROVIDER_DEFAULT_LANGUAGES.get(movie.provider)
    translations = dict(movie.translations or {})
    default_values = dict(translations.get(default_language, {})) if default_language else {}
    default_title = (
        default_values.get("title")
        if default_language
        else None
    )
    title = default_title or movie.original_title or media.get("title")
    if not title:
        return
    changed_fields = []
    if movie.title != title:
        movie.title = title
        changed_fields.append("title")
    if default_language and default_values.get("title") != title:
        default_values["title"] = title
        translations[default_language] = default_values
        movie.translations = translations
        changed_fields.append("translations")
    if changed_fields:
        movie.save(update_fields=[*changed_fields, "updated_at"])


def _normalize_show_title(show: Show, media: dict) -> None:
    default_language = PROVIDER_DEFAULT_LANGUAGES.get(show.provider)
    translations = dict(show.translations or {})
    default_values = dict(translations.get(default_language, {})) if default_language else {}
    default_name = (
        default_values.get("name")
        if default_language
        else None
    )
    name = default_name or media.get("title")
    if not name:
        return
    changed_fields = []
    if show.name != name:
        show.name = name
        changed_fields.append("name")
    if default_language and default_values.get("name") != name:
        default_values["name"] = name
        translations[default_language] = default_values
        show.translations = translations
        changed_fields.append("translations")
    if changed_fields:
        show.save(update_fields=[*changed_fields, "updated_at"])


def _find_by_ids(model, media, *, user=None, user_state_relation: str | None = None):
    ids = ids_from_media(media)
    fields = [
        ("trakt_id", "trakt"),
        ("imdb_id", "imdb"),
        ("tmdb_id", "tmdb"),
    ]
    if ids.get("tmdb") in (None, ""):
        fields.append(("tvdb_id", "tvdb"))

    def find(fields_to_search):
        for field_name, provider_name in fields_to_search:
            value = ids.get(provider_name)
            if value in (None, ""):
                continue
            if user is not None and user_state_relation:
                record = model.objects.filter(
                    **{
                        field_name: str(value),
                        f"{user_state_relation}__user": user,
                    }
                ).first()
                if record is not None:
                    return record
            record = model.objects.filter(**{field_name: str(value)}).first()
            if record is not None:
                return record
        return None

    return find(fields)


def _import_with_provider_fallback(ids: dict, provider_order, importer):
    candidates = [
        (provider, str(ids[provider]))
        for provider in provider_order
        if ids.get(provider) not in (None, "")
    ]
    if not candidates:
        return None
    last_error = None
    for provider, external_id in candidates:
        try:
            return importer(provider, external_id)
        except ProviderError as exc:
            last_error = exc
    if last_error is not None:
        raise last_error
    return None


def _save_media_ids(obj, media: dict):
    ids = ids_from_media(media)
    field_by_provider = {
        "trakt": "trakt_id",
        "tmdb": "tmdb_id",
        "tvdb": "tvdb_id",
        "imdb": "imdb_id",
    }
    update_fields = []
    for provider, field_name in field_by_provider.items():
        value = ids.get(provider)
        if value in (None, "") or getattr(obj, field_name) == str(value):
            continue
        setattr(obj, field_name, str(value))
        update_fields.append(field_name)
    if update_fields:
        obj.save(update_fields=[*update_fields, "updated_at"])


def _ensure_episodes_batch(
    episode_pairs: list[tuple[WatchedEpisode, Show]],
) -> dict[tuple[int, int, int], Episode]:
    if not episode_pairs:
        return {}

    show_ids = {show.id for _watched, show in episode_pairs}
    trakt_ids = {
        str(watched.episode.get("ids", {}).get("trakt"))
        for watched, _show in episode_pairs
        if watched.episode.get("ids", {}).get("trakt") not in (None, "")
    }
    existing_episodes = Episode.objects.filter(
        Q(show_id__in=show_ids) | Q(trakt_id__in=trakt_ids)
    )
    episodes_by_trakt = {
        str(episode.trakt_id): episode
        for episode in existing_episodes
        if episode.trakt_id
    }
    episodes_by_position = {
        (episode.show_id, episode.season_number, episode.episode_number): episode
        for episode in existing_episodes
        if episode.show_id in show_ids
    }

    seasons = {
        (season.show_id, season.season_number): season
        for season in Season.objects.filter(show_id__in=show_ids)
    }
    missing_season_keys = {
        (show.id, watched.season_number)
        for watched, show in episode_pairs
        if (show.id, watched.season_number) not in seasons
    }
    if missing_season_keys:
        created_seasons = Season.objects.bulk_create(
            [
                Season(
                    show_id=show_id,
                    season_number=season_number,
                    name=f"Season {season_number}",
                )
                for show_id, season_number in sorted(missing_season_keys)
            ],
            batch_size=500,
        )
        seasons.update(
            {(season.show_id, season.season_number): season for season in created_seasons}
        )

    new_episodes = []
    updates_by_id = {}
    conflicting_ids_to_clear = set()
    result = {}
    for watched, show in episode_pairs:
        ids = watched.episode.get("ids") or {}
        trakt_id = ids.get("trakt")
        position_key = (show.id, watched.season_number, watched.episode_number)
        episode = episodes_by_position.get(position_key)
        conflicting_episode = (
            episodes_by_trakt.get(str(trakt_id))
            if trakt_id not in (None, "")
            else None
        )
        if episode is None:
            episode = conflicting_episode
        elif (
            conflicting_episode is not None
            and conflicting_episode.id != episode.id
            and conflicting_episode.show_id != show.id
        ):
            conflicting_episode.trakt_id = None
            conflicting_ids_to_clear.add(conflicting_episode.id)
            updates_by_id[conflicting_episode.id] = conflicting_episode
        season = seasons[(show.id, watched.season_number)]
        if episode is None:
            episode = Episode(
                show=show,
                season=season,
                season_number=watched.season_number,
                episode_number=watched.episode_number,
                trakt_id=str(trakt_id) if trakt_id not in (None, "") else None,
                name=str(
                    watched.episode.get("title")
                    or watched.episode.get("name")
                    or ""
                ),
            )
            new_episodes.append(episode)
        else:
            changed = False
            if episode.show_id != show.id:
                episode.show = show
                changed = True
            if episode.season_id != season.id:
                episode.season = season
                changed = True
            if trakt_id not in (None, "") and episode.trakt_id != str(trakt_id):
                episode.trakt_id = str(trakt_id)
                changed = True
            if changed:
                updates_by_id[episode.id] = episode
        result[position_key] = episode
        if episode.trakt_id:
            episodes_by_trakt[str(episode.trakt_id)] = episode
        episodes_by_position[position_key] = episode

    if new_episodes:
        Episode.objects.bulk_create(new_episodes, batch_size=500)
    if conflicting_ids_to_clear:
        Episode.objects.filter(id__in=conflicting_ids_to_clear).update(trakt_id=None)
    updates = [
        episode
        for episode in updates_by_id.values()
        if episode.id not in conflicting_ids_to_clear or episode.trakt_id
    ]
    if updates:
        Episode.objects.bulk_update(updates, ["show", "season", "trakt_id"], batch_size=500)
    return result


def _ensure_episode(show: Show, season_number: int, episode_number: int, media: dict) -> Episode:
    episode = None
    ids = ids_from_media(media)
    if ids.get("trakt") not in (None, ""):
        episode = Episode.objects.filter(trakt_id=str(ids["trakt"])).first()
    if episode is None:
        episode = Episode.objects.filter(
            show=show,
            season_number=season_number,
            episode_number=episode_number,
        ).first()
    season, _created = Season.objects.get_or_create(
        show=show,
        season_number=season_number,
        defaults={"name": f"Season {season_number}"},
    )
    if episode is None:
        episode = Episode.objects.create(
            show=show,
            season=season,
            season_number=season_number,
            episode_number=episode_number,
            trakt_id=(str(ids["trakt"]) if ids.get("trakt") not in (None, "") else None),
            name=str(media.get("title") or media.get("name") or ""),
        )
    else:
        fields = []
        if episode.show_id != show.id:
            episode.show = show
            fields.append("show")
        if episode.season_id != season.id:
            episode.season = season
            fields.append("season")
        if ids.get("trakt") not in (None, "") and episode.trakt_id != str(ids["trakt"]):
            episode.trakt_id = str(ids["trakt"])
            fields.append("trakt_id")
        if fields:
            episode.save(update_fields=[*fields])
    return episode


def _ensure_user_show(user, show: Show, *, status: str) -> UserShow:
    user_show, _created = UserShow.objects.get_or_create(
        user=user,
        show=show,
        defaults={"status": status},
    )
    if user_show.status != UserShow.Status.DROPPED or status == UserShow.Status.DROPPED:
        user_show.status = status
        user_show.save(update_fields=["status", "updated_at"])
    return user_show


def _build_outbound(user, remote, local, intents, *, initial: bool) -> dict:
    watch_add_movies = []
    watch_add_shows = []
    watch_remove_movies = []
    watch_remove_shows = []
    history_movies = []
    history_shows: dict[str, dict] = {}
    dropped_add = []
    dropped_remove = []
    remote_episode_index = _remote_episode_index(remote.watched_episodes.values())

    if initial:
        for state in local.movie_watchlist:
            payload = movie_payload(state.movie)
            if _matching_remote_media(_media_tokens(payload), remote.watchlist_movies.values()) is None:
                _append_unique(watch_add_movies, payload)
        for state in local.show_watchlist:
            payload = show_payload(state.show)
            if _matching_remote_media(_media_tokens(payload), remote.watchlist_shows.values()) is None:
                _append_unique(watch_add_shows, payload)
        for state in local.show_dropped:
            payload = show_payload(state.show)
            if _matching_remote_media(_media_tokens(payload), remote.dropped_shows.values()) is None:
                _append_unique(dropped_add, payload)

    for state in local.movie_history:
        payload = movie_payload(state.movie, watched_at=state.seen_at)
        remote_watch = _matching_remote_media(
            _media_tokens(payload), remote.watched_movies.values()
        )
        remote_time = remote_watch.watched_at if remote_watch else None
        if remote_time is None:
            _append_unique(history_movies, payload)
    for state in local.episode_history:
        payload = episode_payload(state.episode, watched_at=state.seen_at)
        remote_episode = _matching_remote_episode(
            payload,
            remote_episode_index,
        )
        if remote_episode is None:
            _add_episode_history(history_shows, payload)

    for intent in intents:
        kind = intent.kind
        payload = intent.payload
        if kind == TraktSyncIntent.Kind.MOVIE_WATCHLIST:
            target = watch_add_movies if intent.desired else watch_remove_movies
            remote_present = _matching_remote_media(
                _media_tokens(payload), remote.watchlist_movies.values()
            ) is not None
            if intent.desired and not remote_present:
                _append_unique(target, _payload_media(payload, "movie"))
            elif not intent.desired and remote_present:
                _append_unique(target, _payload_media(payload, "movie"))
        elif kind == TraktSyncIntent.Kind.SHOW_WATCHLIST:
            target = watch_add_shows if intent.desired else watch_remove_shows
            remote_present = _matching_remote_media(
                _media_tokens(payload), remote.watchlist_shows.values()
            ) is not None
            if intent.desired and not remote_present:
                _append_unique(target, _payload_media(payload, "show"))
            elif not intent.desired and remote_present:
                _append_unique(target, _payload_media(payload, "show"))
        elif kind == TraktSyncIntent.Kind.MOVIE_HISTORY:
            remote_watch = _matching_remote_media(
                _media_tokens(payload), remote.watched_movies.values()
            )
            if remote_watch is None:
                _append_unique(history_movies, _payload_media(payload, "movie"))
        elif kind == TraktSyncIntent.Kind.EPISODE_HISTORY:
            remote_episode = _matching_remote_episode(
                payload,
                remote_episode_index,
            )
            if remote_episode is None:
                _add_episode_history(history_shows, payload)
        elif kind == TraktSyncIntent.Kind.SHOW_DROPPED:
            target = dropped_add if intent.desired else dropped_remove
            remote_present = _matching_remote_media(
                _media_tokens(payload), remote.dropped_shows.values()
            ) is not None
            if intent.desired and not remote_present:
                _append_unique(target, _payload_media(payload, "show"))
            elif not intent.desired and remote_present:
                _append_unique(target, _payload_media(payload, "show"))

    return {
        "watch_add": {"movies": watch_add_movies, "shows": watch_add_shows},
        "watch_remove": {"movies": watch_remove_movies, "shows": watch_remove_shows},
        "history_movies": history_movies,
        "history_shows": list(history_shows.values()),
        "dropped_add": dropped_add,
        "dropped_remove": dropped_remove,
    }


def _send_outbound(client, outbound: dict) -> int:
    sent = 0
    if any(outbound["watch_add"].values()):
        client.post_watchlist(outbound["watch_add"])
        sent += sum(len(items) for items in outbound["watch_add"].values())
    if any(outbound["watch_remove"].values()):
        client.post_watchlist(outbound["watch_remove"], remove=True)
        sent += sum(len(items) for items in outbound["watch_remove"].values())
    if outbound["history_movies"] or outbound["history_shows"]:
        client.post_history(outbound["history_movies"], outbound["history_shows"])
        sent += len(outbound["history_movies"]) + len(outbound["history_shows"])
    if outbound["dropped_add"]:
        client.post_dropped(outbound["dropped_add"])
        sent += len(outbound["dropped_add"])
    if outbound["dropped_remove"]:
        client.post_dropped(outbound["dropped_remove"], remove=True)
        sent += len(outbound["dropped_remove"])
    return sent


def _acknowledge_intents(intents, remote):
    remote_episode_index = _remote_episode_index(remote.watched_episodes.values())
    for intent in intents:
        payload = intent.payload
        if intent.kind == TraktSyncIntent.Kind.MOVIE_WATCHLIST:
            present = _matching_remote_media(
                _media_tokens(payload), remote.watchlist_movies.values()
            ) is not None
            if present == intent.desired:
                _delete_intent_if_unchanged(intent)
        elif intent.kind == TraktSyncIntent.Kind.SHOW_WATCHLIST:
            present = _matching_remote_media(
                _media_tokens(payload), remote.watchlist_shows.values()
            ) is not None
            if present == intent.desired:
                _delete_intent_if_unchanged(intent)
        elif intent.kind == TraktSyncIntent.Kind.SHOW_DROPPED:
            present = _matching_remote_media(
                _media_tokens(payload), remote.dropped_shows.values()
            ) is not None
            if present == intent.desired:
                _delete_intent_if_unchanged(intent)
        elif intent.kind == TraktSyncIntent.Kind.MOVIE_HISTORY:
            remote_watch = _matching_remote_media(
                _media_tokens(payload), remote.watched_movies.values()
            )
            if remote_watch is not None:
                _delete_intent_if_unchanged(intent)
        elif intent.kind == TraktSyncIntent.Kind.EPISODE_HISTORY:
            if _matching_remote_episode(payload, remote_episode_index):
                _delete_intent_if_unchanged(intent)


def _delete_intent_if_unchanged(intent):
    TraktSyncIntent.objects.filter(
        id=intent.id,
        updated_at=intent.updated_at,
    ).delete()


def _add_episode_history(history_shows: dict[str, dict], payload: dict):
    show = payload.get("show") or {}
    if not show:
        return
    show_key = media_identity_key(show)
    entry = history_shows.setdefault(
        show_key,
        {"ids": dict(show.get("ids") or {}), "seasons": []},
    )
    for incoming_season in payload.get("seasons") or []:
        season_number = _as_int(incoming_season.get("number"), default=0)
        season = next(
            (
                item
                for item in entry["seasons"]
                if _as_int(item.get("number"), default=0) == season_number
            ),
            None,
        )
        if season is None:
            season = {"number": season_number, "episodes": []}
            entry["seasons"].append(season)
        for incoming_episode in incoming_season.get("episodes") or []:
            episode_number = _as_int(incoming_episode.get("number"), default=0)
            existing = next(
                (
                    item
                    for item in season["episodes"]
                    if _as_int(item.get("number"), default=0) == episode_number
                ),
                None,
            )
            if existing is None:
                season["episodes"].append(dict(incoming_episode))
                continue
            existing_time = parse_timestamp(existing.get("watched_at"))
            incoming_time = parse_timestamp(incoming_episode.get("watched_at"))
            if existing_time is None or (
                incoming_time is not None and incoming_time > existing_time
            ):
                existing.update(incoming_episode)


def _payload_media(payload: dict, media_type: str) -> dict:
    return unwrap_media(payload, media_type)


def _media_tokens(media: dict) -> set[str]:
    ids = ids_from_media(media)
    return {
        f"{provider}:{value}"
        for provider, value in ids.items()
        if value not in (None, "")
    }


def _matching_remote_media(tokens: set[str], values) -> object | None:
    for value in values:
        media = value.media if isinstance(value, WatchedMovie) else value
        if tokens.intersection(_media_tokens(media)):
            return value
    return None


def _matching_remote_episode(payload: dict, values) -> WatchedEpisode | None:
    show = payload.get("show") or {}
    seasons = payload.get("seasons") or []
    if not seasons or not seasons[0].get("episodes"):
        return None
    season_number = _as_int(seasons[0].get("number"), default=0)
    episode_number = _as_int(seasons[0]["episodes"][0].get("number"), default=0)
    tokens = _media_tokens(show)
    if isinstance(values, dict):
        for token in tokens:
            matched = values.get((token, season_number, episode_number))
            if matched is not None:
                return matched
        return None
    tokens.add(f"number:s{season_number}:e{episode_number}")
    for value in values:
        remote_tokens = _media_tokens(value.show)
        remote_tokens.add(f"number:s{value.season_number}:e{value.episode_number}")
        if tokens.intersection(remote_tokens) and (
            value.season_number == season_number
            and value.episode_number == episode_number
        ):
            return value
    return None


def _remote_episode_index(values) -> dict[tuple[str, int, int], WatchedEpisode]:
    return {
        (token, value.season_number, value.episode_number): value
        for value in values
        for token in _media_tokens(value.show)
    }


def _pending_desired(intents, kind: str, tokens: set[str]) -> bool | None:
    for intent in reversed(intents):
        if intent.kind != kind:
            continue
        if tokens.intersection(_media_tokens(_payload_media(intent.payload, "movie" if kind.startswith("movie_") else "show"))):
            return intent.desired
    return None


def _append_unique(items: list[dict], payload: dict):
    tokens = _media_tokens(payload)
    if any(tokens.intersection(_media_tokens(existing)) for existing in items):
        return
    items.append(payload)


def _build_client(account, *, client_factory=None):
    if client_factory is not None:
        return client_factory(account)
    return TraktClient(
        account.access_token,
        client_id=settings.TRAKT_CLIENT_ID,
        client_secret=settings.TRAKT_CLIENT_SECRET,
    )


def _as_int(value, *, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
