"""Library helpers shared by the sync providers and the Trakt export import.

They resolve external media identities to catalog rows and read a user's
local library state.
"""

from dataclasses import dataclass
from datetime import datetime

from django.db.models import Q

from apps.catalog.localization import PROVIDER_DEFAULT_LANGUAGES
from apps.catalog.providers.exceptions import ProviderError
from apps.movies.models import Movie, UserMovie
from apps.sync.identities import ids_from_media
from apps.tv.models import Episode, Season, Show, UserEpisode, UserShow


@dataclass(frozen=True)
class WatchedEpisode:
    show: dict
    episode: dict
    season_number: int
    episode_number: int
    watched_at: datetime


@dataclass
class LocalSnapshot:
    movie_watchlist: list[UserMovie]
    movie_history: list[UserMovie]
    show_watchlist: list[UserShow]
    show_dropped: list[UserShow]
    episode_history: list[UserEpisode]


def _collect_local_snapshot(user) -> LocalSnapshot:
    # Syncs only compare identities, titles and timestamps. select_related
    # builds a separate Show for every watched episode, so loading the JSON
    # blobs too costs hundreds of megabytes on a large library.
    heavy_media_fields = ("translations", "cast", "overview")
    return LocalSnapshot(
        movie_watchlist=list(
            UserMovie.objects.filter(user=user, on_watchlist=True, is_seen=False)
            .select_related("movie")
            .defer(*(f"movie__{name}" for name in heavy_media_fields))
        ),
        movie_history=list(
            UserMovie.objects.filter(user=user, is_seen=True)
            .select_related("movie")
            .defer(*(f"movie__{name}" for name in heavy_media_fields))
        ),
        show_watchlist=list(
            UserShow.objects.filter(
                user=user,
                on_watchlist=True,
            )
            .select_related("show")
            .defer(*(f"show__{name}" for name in heavy_media_fields))
        ),
        show_dropped=list(
            UserShow.objects.filter(
                user=user,
                status=UserShow.Status.DROPPED,
            )
            .select_related("show")
            .defer(*(f"show__{name}" for name in heavy_media_fields))
        ),
        episode_history=list(
            UserEpisode.objects.filter(user=user)
            .select_related("episode", "episode__show")
            .defer(
                "episode__translations",
                "episode__overview",
                *(f"episode__show__{name}" for name in heavy_media_fields),
            )
        ),
    )


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
    """Look a record up by external id, preferring the user's own copy.

    Identifiers are tried in ``trakt, imdb, tmdb, tvdb`` order; a single query
    fetches every candidate so a library sync does not issue one round trip per
    identifier per item.
    """
    ids = ids_from_media(media)
    fields = [
        ("trakt_id", "trakt"),
        ("imdb_id", "imdb"),
        ("tmdb_id", "tmdb"),
    ]
    if ids.get("tmdb") in (None, ""):
        fields.append(("tvdb_id", "tvdb"))

    lookups = [
        (field_name, str(ids[provider_name]))
        for field_name, provider_name in fields
        if ids.get(provider_name) not in (None, "")
    ]
    if not lookups:
        return None

    query = Q()
    for field_name, value in lookups:
        query |= Q(**{field_name: value})
    candidates = list(model.objects.filter(query))
    if not candidates:
        return None

    owned_ids: set[int] = set()
    if user is not None and user_state_relation and len(candidates) > 1:
        owned_ids = set(
            model.objects.filter(
                query,
                **{f"{user_state_relation}__user": user},
            ).values_list("id", flat=True)
        )

    for field_name, value in lookups:
        matches = [
            candidate
            for candidate in candidates
            if getattr(candidate, field_name) == value
        ]
        if not matches:
            continue
        for candidate in matches:
            if candidate.id in owned_ids:
                return candidate
        return matches[0]
    return None


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
        elif conflicting_episode is not None and conflicting_episode is not episode:
            # The trakt id moved to another episode row; release it first so the
            # reassignment below cannot trip the unique constraint.
            conflicting_episode.trakt_id = None
            if conflicting_episode.id is not None:
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
