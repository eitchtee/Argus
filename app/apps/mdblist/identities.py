"""Payload and identity helpers for the MDBList sync.

Intents are stored with the shared payloads :mod:`apps.sync.identities`
produces (``{"title", "ids": {trakt, imdb, tmdb, tvdb}}`` and the nested
``{"show", "seasons"}`` episode form); the helpers here turn those into the
Trakt-shaped items MDBList's sync endpoints accept.
"""

from apps.sync.identities import parse_timestamp, serialize_timestamp

__all__ = [
    "episode_key",
    "identity_key_for_payload",
    "media_identity_key",
    "media_tokens",
    "mdblist_ids",
    "normalize_ids",
    "parse_episode_key",
    "parse_timestamp",
    "serialize_timestamp",
]

ID_PRIORITY = ("tmdb", "imdb", "tvdb", "mdblist")
# Keys MDBList resolves on writes; anything else (``trakt``, ``mal``) is dropped.
WRITE_ID_KEYS = ("tmdb", "imdb", "tvdb")


def normalize_ids(raw) -> dict[str, str]:
    """Lower-case id keys and drop blanks; values become strings."""
    if not isinstance(raw, dict):
        return {}
    return {
        str(key).lower(): str(value)
        for key, value in raw.items()
        if value not in (None, "")
    }


def mdblist_ids(raw) -> dict:
    """The ``ids`` object for an MDBList write: known keys only, TMDB and TVDB
    as integers."""
    ids = normalize_ids(raw)
    payload: dict = {}
    for key in WRITE_ID_KEYS:
        value = ids.get(key)
        if value is None:
            continue
        if key in {"tmdb", "tvdb"}:
            try:
                payload[key] = int(value)
                continue
            except ValueError:
                pass
        payload[key] = value
    return payload


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
    return media_identity_key(media.get("ids"), title=media.get("title", ""), year=media.get("year"))


def episode_key(season_number: int, episode_number: int) -> str:
    return f"{int(season_number)}:{int(episode_number)}"


def parse_episode_key(key: str) -> tuple[int, int]:
    season, _sep, episode = str(key).partition(":")
    return int(season), int(episode)
