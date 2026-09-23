"""Apply a Trakt data export onto a user's library."""

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation

from django.contrib.contenttypes.models import ContentType
from django.utils import timezone
from cachalot.api import cachalot_disabled

from apps.catalog.localization import PROVIDER_DEFAULT_LANGUAGES
from apps.catalog.models import MediaRating
from apps.catalog.ratings import HALF_STEP, MAX_SCORE, MIN_SCORE, SCORE_QUANTUM
from apps.catalog.providers.exceptions import ProviderError
from apps.movies import services as movie_services
from apps.movies.models import Movie, UserMovie
from apps.sync.changes import suppress_local_intents
from apps.sync.identities import (
    episode_identity_key,
    ids_from_media,
    media_identity_key,
    parse_timestamp,
    unwrap_media,
)
from apps.sync.library import (
    WatchedEpisode,
    _ensure_episode,
    _ensure_episodes_batch,
    _find_by_ids,
    _import_with_provider_fallback,
    _normalize_movie_title,
    _normalize_show_title,
    _save_media_ids,
)
from apps.tv import services as tv_services
from apps.tv.models import Episode, Show, UserEpisode, UserShow


@dataclass(frozen=True)
class TraktSnapshot:
    watchlist_movies: list[dict]
    watchlist_shows: list[dict]
    watched_movies: list[dict]
    watched_shows: list[dict]
    dropped_shows: list[dict]
    watched_episodes: list[dict] = field(default_factory=list)
    rated_movies: list[dict] = field(default_factory=list)
    rated_shows: list[dict] = field(default_factory=list)
    rated_episodes: list[dict] = field(default_factory=list)


@dataclass(frozen=True)
class WatchedMovie:
    media: dict
    watched_at: datetime


@dataclass(frozen=True)
class RatedMedia:
    media: dict
    score: Decimal


@dataclass(frozen=True)
class RatedEpisode:
    show: dict
    episode: dict
    season_number: int
    episode_number: int
    score: Decimal


@dataclass
class RemoteSnapshot:
    watchlist_movies: dict[str, dict] = field(default_factory=dict)
    watchlist_shows: dict[str, dict] = field(default_factory=dict)
    watched_shows: dict[str, dict] = field(default_factory=dict)
    watched_movies: dict[str, WatchedMovie] = field(default_factory=dict)
    watched_episodes: dict[str, WatchedEpisode] = field(default_factory=dict)
    dropped_shows: dict[str, dict] = field(default_factory=dict)
    rated_movies: dict[str, RatedMedia] = field(default_factory=dict)
    rated_shows: dict[str, RatedMedia] = field(default_factory=dict)
    rated_episodes: dict[str, RatedEpisode] = field(default_factory=dict)


@dataclass
class ImportReport:
    movies_imported: int = 0
    shows_imported: int = 0
    episodes_marked: int = 0
    ratings_applied: int = 0
    warnings: list[str] = field(default_factory=list)


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

    normalized.rated_movies = _merge_rated_media(snapshot.rated_movies, "movie")
    normalized.rated_shows = _merge_rated_media(snapshot.rated_shows, "show")
    normalized.rated_episodes = _merge_rated_episodes(snapshot.rated_episodes)

    normalized.watched_movies = _merge_watched_movies(snapshot.watched_movies)
    normalized.watched_episodes = _merge_watched_episodes(
        snapshot.watched_episodes or snapshot.watched_shows
    )
    return normalized


def apply_remote_snapshot(user, snapshot: TraktSnapshot) -> ImportReport:
    """Merge an exported Trakt library into the user's library.

    The import only adds or updates state; nothing already in Argus is removed.
    """
    remote = normalize_snapshot(snapshot)
    report = ImportReport()
    with cachalot_disabled():
        with suppress_local_intents():
            _apply_remote_movies(user, remote, report)
            _apply_remote_shows(user, remote, report)
            _apply_remote_ratings(user, remote, report)
    return report


def _local_score(raw) -> Decimal | None:
    """Convert a Trakt 1-10 rating into the local 0.5-5 half-star scale."""
    if raw in (None, ""):
        return None
    try:
        score = (Decimal(str(raw)) / 2).quantize(SCORE_QUANTUM)
    except (InvalidOperation, ArithmeticError, ValueError):
        return None
    # Trakt only ever sends whole numbers, but round anything else onto the
    # nearest half star so an odd payload does not fail model validation.
    score = (score / HALF_STEP).quantize(Decimal("1")) * HALF_STEP
    if score < MIN_SCORE or score > MAX_SCORE:
        return None
    return score.quantize(SCORE_QUANTUM)


def _merge_rated_media(records: list[dict], media_type: str) -> dict[str, RatedMedia]:
    result: dict[str, RatedMedia] = {}
    for record in records:
        if not isinstance(record, dict):
            continue
        score = _local_score(record.get("rating"))
        if score is None:
            continue
        media = unwrap_media(record, media_type)
        if not media:
            continue
        result[media_identity_key(media)] = RatedMedia(media=media, score=score)
    return result


def _merge_rated_episodes(records: list[dict]) -> dict[str, RatedEpisode]:
    result: dict[str, RatedEpisode] = {}
    for record in records:
        if not isinstance(record, dict):
            continue
        score = _local_score(record.get("rating"))
        if score is None:
            continue
        show = record.get("show")
        episode = record.get("episode")
        if not isinstance(show, dict) or not isinstance(episode, dict):
            continue
        season_number = _as_int(episode.get("season"), default=-1)
        episode_number = _as_int(episode.get("number"), default=0)
        if season_number < 0 or episode_number <= 0:
            continue
        key = episode_identity_key(
            {"show": show},
            season_number=season_number,
            episode_number=episode_number,
        )
        result[key] = RatedEpisode(
            show=show,
            episode=episode,
            season_number=season_number,
            episode_number=episode_number,
            score=score,
        )
    return result


def _apply_remote_ratings(user, remote, report) -> None:
    """Apply exported Trakt ratings onto media already in either library.

    This pass never adds anything to the catalog: it runs after the watched
    and watchlist passes, so anything in the Trakt library has a row by now,
    and anything tracked only in Argus already had one. A rating for media in neither library is dropped rather
    than dragged in on its own -- a bare score is not a reason to track
    something.
    """
    resolved: list[tuple[str, object, Decimal]] = []

    for rated in remote.rated_movies.values():
        movie = _find_by_ids(
            Movie,
            rated.media,
            user=user,
            user_state_relation="user_states",
        )
        if movie is not None:
            resolved.append((MediaRating.MediaType.MOVIE, movie, rated.score))

    show_cache: dict[str, Show | None] = {}

    def find_show(media: dict) -> Show | None:
        cache_key = media_identity_key(media)
        if cache_key not in show_cache:
            show_cache[cache_key] = _find_by_ids(
                Show,
                media,
                user=user,
                user_state_relation="user_states",
            )
        return show_cache[cache_key]

    for rated in remote.rated_shows.values():
        show = find_show(rated.media)
        if show is not None:
            resolved.append((MediaRating.MediaType.SHOW, show, rated.score))

    episode_requests: list[tuple[Show, RatedEpisode]] = []
    for rated in remote.rated_episodes.values():
        show = find_show(rated.show)
        if show is not None:
            episode_requests.append((show, rated))

    if episode_requests:
        wanted = {
            (show.id, rated.season_number, rated.episode_number)
            for show, rated in episode_requests
        }
        episodes = {
            (episode.show_id, episode.season_number, episode.episode_number): episode
            for episode in Episode.objects.filter(
                show_id__in={show.id for show, _rated in episode_requests}
            ).only("id", "show_id", "season_number", "episode_number")
            if (episode.show_id, episode.season_number, episode.episode_number) in wanted
        }
        for show, rated in episode_requests:
            episode = episodes.get((show.id, rated.season_number, rated.episode_number))
            if episode is None:
                # The show is tracked, so the episode belongs here even if the
                # metadata provider never listed it -- same fallback the
                # watched-episode pass uses.
                episode = _ensure_episode(
                    show,
                    rated.season_number,
                    rated.episode_number,
                    rated.episode,
                )
                episodes[(show.id, rated.season_number, rated.episode_number)] = episode
            resolved.append((MediaRating.MediaType.EPISODE, episode, rated.score))

    if not resolved:
        return

    content_types = {
        media_type: ContentType.objects.get_for_model(type(media))
        for media_type, media, _score in resolved
    }
    existing_ratings = {
        (rating.content_type_id, rating.object_id): rating
        for rating in MediaRating.objects.filter(
            user=user,
            content_type__in=set(content_types.values()),
        )
    }

    to_create = []
    to_update = []
    for media_type, media, score in resolved:
        content_type = content_types[media_type]
        current = existing_ratings.get((content_type.id, media.pk))
        if current is None:
            to_create.append(
                MediaRating(
                    user=user,
                    media_type=media_type,
                    content_type=content_type,
                    object_id=media.pk,
                    score=score,
                )
            )
        elif current.score != score:
            current.score = score
            current.media_type = media_type
            # bulk_update skips auto_now, so stamp the change by hand.
            current.updated_at = timezone.now()
            to_update.append(current)

    if to_create:
        MediaRating.objects.bulk_create(
            to_create,
            batch_size=500,
            ignore_conflicts=True,
        )
    if to_update:
        MediaRating.objects.bulk_update(
            to_update,
            ["media_type", "score", "updated_at"],
            batch_size=500,
        )
    report.ratings_applied += len(to_create) + len(to_update)


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


def _apply_remote_movies(user, remote, report):
    movie_cache: dict[str, Movie | None] = {}
    watched_movies_index = _media_index(remote.watched_movies.values())

    def ensure(media):
        cache_key = media_identity_key(media)
        if cache_key not in movie_cache:
            try:
                movie, created = _ensure_movie(user, media)
            except (ProviderError, ValueError) as exc:
                report.warnings.append(f"Movie import failed: {exc}")
                movie_cache[cache_key] = None
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
        changed_fields = []
        if not state.is_seen:
            state.is_seen = True
            changed_fields.append("is_seen")
        if state.seen_at is None or watched.watched_at > state.seen_at:
            state.seen_at = watched.watched_at
            changed_fields.append("seen_at")
        if state.on_watchlist:
            state.on_watchlist = False
            changed_fields.append("on_watchlist")
        if state.watchlist_added_at is not None:
            state.watchlist_added_at = None
            changed_fields.append("watchlist_added_at")
        if changed_fields:
            state.save(update_fields=[*changed_fields, "updated_at"])

    for media in remote.watchlist_movies.values():
        if _matching_remote_media(_media_tokens(media), watched_movies_index) is not None:
            continue
        movie = ensure(media)
        if movie is None:
            continue
        state, _created = UserMovie.objects.get_or_create(user=user, movie=movie)
        if state.is_seen:
            continue
        changed_fields = []
        if not state.on_watchlist:
            state.on_watchlist = True
            changed_fields.append("on_watchlist")
        if state.watchlist_added_at is None:
            state.watchlist_added_at = timezone.now()
            changed_fields.append("watchlist_added_at")
        if changed_fields:
            state.save(update_fields=[*changed_fields, "updated_at"])


def _apply_remote_shows(user, remote, report):
    show_cache: dict[str, Show | None] = {}
    dropped_shows_index = _media_index(remote.dropped_shows.values())

    def ensure(media):
        cache_key = media_identity_key(media)
        if cache_key not in show_cache:
            try:
                show, created = _ensure_show(user, media)
            except (ProviderError, ValueError) as exc:
                report.warnings.append(f"Show import failed: {exc}")
                show_cache[cache_key] = None
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
        ensure_user_show_cached(show, status=UserShow.Status.TRACKED)
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
        ensure_user_show_cached(show, status=UserShow.Status.TRACKED)

    for media in remote.watchlist_shows.values():
        show = ensure(media)
        if show is None:
            continue
        if _matching_remote_media(_media_tokens(media), dropped_shows_index) is not None:
            continue
        user_show = ensure_user_show_cached(show, status=UserShow.Status.TRACKED)
        if not user_show.on_watchlist:
            user_show.on_watchlist = True
            user_show.save(update_fields=["on_watchlist", "updated_at"])

    for media in remote.dropped_shows.values():
        show = ensure(media)
        if show is None:
            continue
        user_show = ensure_user_show_cached(show, status=UserShow.Status.DROPPED)
        changed_fields = []
        if user_show.status not in {UserShow.Status.PAUSED, UserShow.Status.DROPPED}:
            user_show.status = UserShow.Status.DROPPED
            changed_fields.append("status")
        if user_show.on_watchlist:
            user_show.on_watchlist = False
            changed_fields.append("on_watchlist")
        if changed_fields:
            user_show.save(update_fields=[*changed_fields, "updated_at"])


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


def _media_tokens(media: dict) -> set[str]:
    ids = ids_from_media(media)
    return {
        f"{provider}:{value}"
        for provider, value in ids.items()
        if value not in (None, "")
    }


def _media_index(values) -> dict[str, object]:
    """Map every identifier token of ``values`` to the first entry carrying it."""
    index: dict[str, object] = {}
    for value in values:
        media = value.media if isinstance(value, WatchedMovie) else value
        for token in _media_tokens(media):
            index.setdefault(token, value)
    return index


def _matching_remote_media(tokens: set[str], values) -> object | None:
    if isinstance(values, dict):
        for token in tokens:
            match = values.get(token)
            if match is not None:
                return match
        return None
    for value in values:
        media = value.media if isinstance(value, WatchedMovie) else value
        if tokens.intersection(_media_tokens(media)):
            return value
    return None


def _as_int(value, *, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
