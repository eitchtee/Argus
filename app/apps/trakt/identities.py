from datetime import date, datetime

from django.utils import timezone


def movie_payload(movie, *, watched_at: datetime | None = None) -> dict:
    payload = {
        "title": movie.title,
        "ids": _ids_for_object(movie),
    }
    if watched_at is not None:
        payload["watched_at"] = serialize_timestamp(watched_at)
    return payload


def show_payload(show) -> dict:
    return {
        "title": show.name,
        "ids": _ids_for_object(show),
    }


def episode_payload(episode, *, watched_at: datetime) -> dict:
    return {
        "show": show_payload(episode.show),
        "seasons": [
            {
                "number": episode.season_number,
                "episodes": [
                    {
                        "number": episode.episode_number,
                        "ids": _ids_for_object(episode),
                        "watched_at": serialize_timestamp(watched_at),
                    }
                ],
            }
        ],
    }


def unwrap_media(item: dict, media_type: str) -> dict:
    value = item.get(media_type)
    return value if isinstance(value, dict) else item


def ids_from_media(item: dict, media_type: str | None = None) -> dict:
    media = unwrap_media(item, media_type) if media_type else item
    ids = media.get("ids") if isinstance(media, dict) else None
    return ids if isinstance(ids, dict) else {}


def media_identity_key(item: dict, media_type: str | None = None) -> str:
    ids = ids_from_media(item, media_type)
    for provider in ("trakt", "imdb", "tmdb", "tvdb"):
        value = ids.get(provider)
        if value not in (None, ""):
            return f"{provider}:{value}"
    media = unwrap_media(item, media_type) if media_type else item
    title = str(media.get("title") or media.get("name") or "").strip().casefold()
    year = media.get("year") or ""
    return f"title:{title}:{year}"


def episode_identity_key(
    item: dict,
    *,
    season_number: int | None = None,
    episode_number: int | None = None,
) -> str:
    if "show" in item:
        show_item = item["show"]
    else:
        show_item = item
    show_key = media_identity_key(show_item, "show")
    season_number = (
        season_number
        if season_number is not None
        else int(item.get("season_number") or item.get("season") or 0)
    )
    episode_number = (
        episode_number
        if episode_number is not None
        else int(item.get("episode_number") or item.get("episode") or 0)
    )
    return f"episode:{show_key}:s{season_number}:e{episode_number}"


def identity_key_for_payload(kind: str, payload: dict) -> str:
    if kind == "episode_history":
        show = payload.get("show") or {}
        seasons = payload.get("seasons") or []
        season = seasons[0] if seasons else {}
        episodes = season.get("episodes") or []
        episode = episodes[0] if episodes else {}
        return episode_identity_key(
            {"show": show},
            season_number=season.get("number", 0),
            episode_number=episode.get("number", 0),
        )
    media_type = "movie" if kind.startswith("movie_") else "show"
    return media_identity_key(payload, media_type)


def latest_timestamp_from_payload(payload: dict) -> datetime | None:
    if payload.get("watched_at"):
        return parse_timestamp(payload["watched_at"])
    for season in payload.get("seasons") or []:
        for episode in season.get("episodes") or []:
            if episode.get("watched_at"):
                return parse_timestamp(episode["watched_at"])
    return None


def serialize_timestamp(value: datetime | date | str) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, datetime) and timezone.is_naive(value):
        value = timezone.make_aware(value, timezone.get_current_timezone())
    return value.isoformat()


def parse_timestamp(value) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        parsed = datetime.combine(value, datetime.min.time())
    else:
        text = str(value).strip()
        if not text:
            return None
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
    if timezone.is_naive(parsed):
        return timezone.make_aware(parsed, timezone.get_current_timezone())
    return parsed


def _ids_for_object(obj) -> dict:
    values = {
        "trakt": getattr(obj, "trakt_id", None),
        "imdb": getattr(obj, "imdb_id", None),
        "tmdb": getattr(obj, "tmdb_id", None),
        "tvdb": getattr(obj, "tvdb_id", None),
    }
    return {
        key: _coerce_provider_id(value, key)
        for key, value in values.items()
        if value not in (None, "")
    }


def _coerce_provider_id(value, provider: str):
    if provider in {"trakt", "tmdb", "tvdb"}:
        try:
            return int(value)
        except (TypeError, ValueError):
            return str(value)
    return str(value)
