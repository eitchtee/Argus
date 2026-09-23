"""Payload and identity helpers for the SIMKL sync.

Intents are stored with the shared payloads :mod:`apps.sync.identities`
produces (``{"title", "ids": {trakt, imdb, tmdb, tvdb}}`` and the
nested ``{"show", "seasons"}`` episode form). The helpers here convert those
into the flat items SIMKL's sync endpoints accept.
"""

from datetime import datetime

from apps.sync.identities import parse_timestamp, serialize_timestamp

__all__ = [
    "episode_key",
    "episode_payload",
    "history_item",
    "identity_key_for_payload",
    "media_identity_key",
    "media_tokens",
    "movie_payload",
    "normalize_ids",
    "parse_episode_key",
    "parse_timestamp",
    "serialize_timestamp",
    "show_payload",
    "simkl_ids",
]

ID_PRIORITY = ("simkl", "imdb", "tmdb", "tvdb")
# Keys SIMKL resolves on writes; anything else (``trakt``, ``slug``) is dropped.
WRITE_ID_KEYS = ("simkl", "imdb", "tmdb", "tvdb")


def normalize_ids(raw) -> dict[str, str]:
    """Lower-case id keys, fold ``simkl_id`` into ``simkl`` and drop blanks."""
    if not isinstance(raw, dict):
        return {}
    ids: dict[str, str] = {}
    for key, value in raw.items():
        if value in (None, ""):
            continue
        name = str(key).lower()
        if name == "simkl_id":
            name = "simkl"
        ids[name] = str(value)
    return ids


def simkl_ids(raw) -> dict:
    """The ``ids`` object for a SIMKL write: known keys only, ints where SIMKL
    documents ints."""
    ids = normalize_ids(raw)
    payload: dict = {}
    for key in WRITE_ID_KEYS:
        value = ids.get(key)
        if value is None:
            continue
        if key == "simkl":
            try:
                payload[key] = int(value)
                continue
            except ValueError:
                pass
        payload[key] = value
    return payload


def _ids_for_object(obj) -> dict[str, str]:
    return normalize_ids(
        {
            "imdb": getattr(obj, "imdb_id", None),
            "tmdb": getattr(obj, "tmdb_id", None),
            "tvdb": getattr(obj, "tvdb_id", None),
        }
    )


def movie_payload(movie, *, watched_at: datetime | None = None) -> dict:
    payload = {"title": movie.title, "ids": _ids_for_object(movie)}
    release_date = getattr(movie, "release_date", None)
    if release_date is not None:
        payload["year"] = release_date.year
    if watched_at is not None:
        payload["watched_at"] = serialize_timestamp(watched_at)
    return payload


def show_payload(show) -> dict:
    payload = {
        "title": show.name,
        "ids": _ids_for_object(show),
        # A no-op for regular shows; for anime it lets SIMKL route TVDB
        # season/episode coordinates onto its per-cour records.
        "use_tvdb_anime_seasons": True,
    }
    first_aired = getattr(show, "first_aired", None)
    if first_aired is not None:
        payload["year"] = first_aired.year
    return payload


def episode_payload(episode, *, watched_at: datetime | None) -> dict:
    item = {"number": episode.episode_number}
    if watched_at is not None:
        item["watched_at"] = serialize_timestamp(watched_at)
    return {
        **show_payload(episode.show),
        "seasons": [{"number": episode.season_number, "episodes": [item]}],
    }


def history_item(payload: dict, media_type: str) -> dict:
    """Turn a stored intent payload into a SIMKL history item."""
    if media_type == "episode":
        show = payload.get("show") if isinstance(payload.get("show"), dict) else payload
        item = {
            "ids": simkl_ids(show.get("ids")),
            "use_tvdb_anime_seasons": True,
            "seasons": [
                {
                    "number": int(season.get("number") or 0),
                    "episodes": [
                        {
                            key: value
                            for key, value in {
                                "number": int(episode.get("number") or 0),
                                "watched_at": episode.get("watched_at"),
                            }.items()
                            if value not in (None, "")
                        }
                        for episode in season.get("episodes") or []
                    ],
                }
                for season in payload.get("seasons") or []
            ],
        }
        for key in ("title", "year"):
            if show.get(key):
                item[key] = show[key]
        return item

    media = payload.get(media_type) if isinstance(payload.get(media_type), dict) else payload
    item = {"ids": simkl_ids(media.get("ids"))}
    for key in ("title", "year", "watched_at", "rating"):
        if media.get(key) not in (None, ""):
            item[key] = media[key]
    if media_type == "show":
        item["use_tvdb_anime_seasons"] = True
    return item


def media_identity_key(raw_ids, *, title: str = "", year=None) -> str:
    ids = normalize_ids(raw_ids)
    for provider in ID_PRIORITY:
        value = ids.get(provider)
        if value:
            return f"{provider}:{value}"
    return f"title:{str(title or '').strip().casefold()}:{year or ''}"


def media_tokens(raw_ids) -> set[str]:
    return {f"{key}:{value}" for key, value in normalize_ids(raw_ids).items() if key in ID_PRIORITY}


def identity_key_for_payload(kind: str, payload: dict) -> str:
    if kind == "episode_history":
        show = payload.get("show") if isinstance(payload.get("show"), dict) else payload
        seasons = payload.get("seasons") or []
        season = seasons[0] if seasons else {}
        episodes = season.get("episodes") or []
        episode = episodes[0] if episodes else {}
        show_key = media_identity_key(show.get("ids"), title=show.get("title", ""))
        return f"episode:{show_key}:s{season.get('number', 0)}:e{episode.get('number', 0)}"
    media_type = "movie" if kind.startswith("movie_") else "show"
    media = payload.get(media_type) if isinstance(payload.get(media_type), dict) else payload
    return media_identity_key(
        media.get("ids"),
        title=media.get("title", ""),
        year=media.get("year"),
    )


def episode_key(season_number: int, episode_number: int) -> str:
    return f"{int(season_number)}:{int(episode_number)}"


def parse_episode_key(key: str) -> tuple[int, int]:
    season, _sep, episode = str(key).partition(":")
    return int(season), int(episode)
