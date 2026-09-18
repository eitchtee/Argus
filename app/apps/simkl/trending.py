"""Discovery data from SIMKL's public CDN files: trending charts and calendars.

The files are edge-cached JSON that cost nothing against the API quota, so
they are fetched with the server's client id alone and kept in the Django
cache for about as long as SIMKL takes to regenerate them. SIMKL's rules ask
that any trending list is titled with "Simkl" and deep-links to the item.
"""

from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta

from django.core.cache import cache
from django.urls import reverse
from django.utils import timezone

from apps.simkl.client import SimklError
from apps.simkl.config import build_client, catalog_configured
from apps.simkl.enrichment import poster_url
from apps.simkl.identities import normalize_ids, parse_timestamp
from apps.simkl.models import SIMKL_WEB_URL, build_simkl_url


CATEGORIES = {"movies": "movie", "tv": "show", "anime": "anime"}
TIMEFRAMES = ("today", "week", "month")
CACHE_TTL = {"today": 60 * 60, "week": 6 * 60 * 60, "month": 6 * 60 * 60}
CALENDAR_TTL = 6 * 60 * 60
FAILURE_TTL = 10 * 60
MOST_WATCHED_URLS = {
    "movies": f"{SIMKL_WEB_URL}/movies/best-movies/most-watched/",
    "tv": f"{SIMKL_WEB_URL}/tv/best-shows/most-watched/",
    "anime": f"{SIMKL_WEB_URL}/anime/best-anime/most-watched/",
}
CALENDAR_KINDS = ("tv", "anime", "movie_release")


@dataclass(frozen=True)
class TrendingEntry:
    title: str
    year: int | None
    media_type: str
    simkl_id: int | None
    simkl_url: str | None
    poster_url: str | None
    argus_url: str | None
    ids: dict
    watchers: int | None
    plan_to_watch: int | None
    simkl_rating: float | None
    imdb_rating: float | None
    status: str
    genres: list
    overview: str
    network: str | None
    runtime: str | None
    user_state: str | None = None

    @property
    def href(self) -> str | None:
        return self.argus_url or self.simkl_url


@dataclass(frozen=True)
class CalendarEntry:
    title: str
    media_type: str
    simkl_id: int | None
    simkl_url: str | None
    poster_url: str | None
    argus_url: str | None
    ids: dict
    date: datetime
    episode_label: str | None
    episode_title: str | None
    network: str | None
    simkl_rating: float | None
    finale: bool = False
    user_state: str | None = None

    @property
    def href(self) -> str | None:
        return self.argus_url or self.simkl_url


# -- Trending ----------------------------------------------------------------


def get_trending(category: str, timeframe: str = "today", *, limit: int = 24, client=None) -> list[TrendingEntry]:
    if category not in CATEGORIES or timeframe not in TIMEFRAMES or not catalog_configured():
        return []
    raw = _cached_trending(category, timeframe, client=client)
    media_type = CATEGORIES[category]
    entries = []
    for item in raw:
        entry = _trending_entry(item, media_type)
        if entry is not None:
            entries.append(entry)
        if len(entries) >= limit:
            break
    return entries


def trending_section(
    user,
    category: str,
    timeframe: str = "today",
    *,
    limit: int = 20,
    hide_seen: bool = True,
) -> list[TrendingEntry]:
    """Trending titles annotated with the viewer's state, optionally dropping
    what they have already watched or track."""
    entries = annotate_user_state(user, get_trending(category, timeframe, limit=limit * 3))
    if hide_seen:
        entries = [entry for entry in entries if entry.user_state is None]
    return entries[:limit]


def _cached_trending(category: str, timeframe: str, *, client=None) -> list:
    key = f"simkl:trending:{category}:{timeframe}"
    cached = cache.get(key)
    if cached is not None:
        return cached
    client = client or build_client()
    try:
        payload = client.get_trending(category, timeframe, size=100)
    except SimklError:
        cache.set(key, [], FAILURE_TTL)
        return []
    items = payload if isinstance(payload, list) else []
    cache.set(key, items, CACHE_TTL[timeframe])
    return items


def _trending_entry(item, media_type: str) -> TrendingEntry | None:
    if not isinstance(item, dict) or not item.get("title"):
        return None
    ids = normalize_ids(item.get("ids"))
    simkl_id = _as_int(ids.get("simkl"))
    ratings = item.get("ratings") if isinstance(item.get("ratings"), dict) else {}
    return TrendingEntry(
        title=str(item["title"]),
        year=_year_from_release(item.get("release_date")),
        media_type=media_type,
        simkl_id=simkl_id,
        simkl_url=_simkl_url(item.get("url"), media_type, simkl_id, ids.get("slug")),
        poster_url=poster_url(item.get("poster")),
        argus_url=argus_url_for(media_type, ids),
        ids=ids,
        watchers=_as_int(item.get("watched")),
        plan_to_watch=_as_int(item.get("plan_to_watch")),
        simkl_rating=_rating(ratings.get("simkl")),
        imdb_rating=_rating(ratings.get("imdb")) or _rating(ratings.get("mal")),
        status=str(item.get("status") or ""),
        genres=[str(genre) for genre in item.get("genres") or [] if genre][:4],
        overview=str(item.get("overview") or ""),
        network=item.get("network") or None,
        runtime=item.get("runtime") or None,
    )


# -- Calendar ----------------------------------------------------------------


def get_calendar(kind: str, *, client=None) -> dict:
    if kind not in CALENDAR_KINDS or not catalog_configured():
        return {}
    key = f"simkl:calendar:{kind}"
    cached = cache.get(key)
    if cached is not None:
        return cached
    client = client or build_client()
    try:
        payload = client.get_calendar(kind)
    except SimklError:
        cache.set(key, {}, FAILURE_TTL)
        return {}
    payload = payload if isinstance(payload, dict) else {}
    cache.set(key, payload, CALENDAR_TTL)
    return payload


def premieres(*, days: int = 30, limit: int = 24, client=None) -> list[CalendarEntry]:
    """Shows whose first episode airs within the next ``days``."""
    now = timezone.now()
    horizon = now + timedelta(days=days)
    seen: set[int] = set()
    results = []
    for entry in _calendar_entries("tv", "show", client=client):
        if entry.simkl_id in seen or entry.episode_label != "S01E01":
            continue
        if entry.date < now - timedelta(days=1) or entry.date > horizon:
            continue
        seen.add(entry.simkl_id)
        results.append(entry)
        if len(results) >= limit:
            break
    return results


def airing_on(day: date, *, limit: int = 40, client=None) -> list[CalendarEntry]:
    """Episodes airing on ``day`` in the active timezone, one entry per show."""
    seen: set[int] = set()
    results = []
    for entry in _calendar_entries("tv", "show", client=client):
        if timezone.localtime(entry.date).date() != day:
            continue
        if entry.simkl_id in seen:
            continue
        seen.add(entry.simkl_id)
        results.append(entry)
        if len(results) >= limit:
            break
    return results


def movie_releases(*, days: int = 30, limit: int = 24, client=None) -> list[CalendarEntry]:
    now = timezone.now()
    horizon = now + timedelta(days=days)
    seen: set[int] = set()
    results = []
    for entry in _calendar_entries("movie_release", "movie", client=client):
        if entry.date < now - timedelta(days=1) or entry.date > horizon:
            continue
        if entry.simkl_id in seen:
            continue
        seen.add(entry.simkl_id)
        results.append(entry)
        if len(results) >= limit:
            break
    return results


def _calendar_entries(kind: str, media_type: str, *, client=None) -> list[CalendarEntry]:
    payload = get_calendar(kind, client=client)
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    results = []
    for raw in payload.get("calendar") or []:
        if not isinstance(raw, dict):
            continue
        simkl_id = _as_int(raw.get("simkl_id"))
        when = parse_timestamp(raw.get("date"))
        if simkl_id is None or when is None:
            continue
        show = metadata.get(str(simkl_id)) if isinstance(metadata.get(str(simkl_id)), dict) else {}
        ids = normalize_ids(show.get("ids"))
        episode = raw.get("episode") if isinstance(raw.get("episode"), dict) else None
        label = None
        if episode is not None:
            season = _as_int(episode.get("season"))
            number = _as_int(episode.get("episode"))
            if number is not None:
                label = f"S{season:02d}E{number:02d}" if season is not None else f"E{number}"
        ratings = show.get("ratings") if isinstance(show.get("ratings"), dict) else {}
        results.append(
            CalendarEntry(
                title=str(show.get("title") or raw.get("title") or ""),
                media_type=media_type,
                simkl_id=simkl_id,
                simkl_url=_simkl_url(show.get("url"), media_type, simkl_id, ids.get("slug")),
                poster_url=poster_url(show.get("poster")),
                argus_url=argus_url_for(media_type, ids),
                ids=ids,
                date=when,
                episode_label=label,
                episode_title=(episode or {}).get("title") if episode else None,
                network=show.get("network") or None,
                simkl_rating=_rating(ratings.get("simkl")),
                finale=bool(raw.get("finale_type")),
            )
        )
    return results


# -- Viewer state ------------------------------------------------------------


def annotate_user_state(user, entries):
    """Stamp ``user_state`` (watched / watchlist / tracked) using the ids the
    entries carry, in one query per media class."""
    if not entries or user is None or getattr(user, "pk", None) is None:
        return list(entries)
    from apps.movies.models import UserMovie
    from apps.tv.models import UserShow

    movie_states: dict[str, str] = {}
    show_states: dict[str, str] = {}
    movie_ids = [entry.ids for entry in entries if entry.media_type == "movie"]
    show_ids = [entry.ids for entry in entries if entry.media_type != "movie"]

    if movie_ids:
        tmdb = {ids["tmdb"] for ids in movie_ids if ids.get("tmdb")}
        imdb = {ids["imdb"] for ids in movie_ids if ids.get("imdb")}
        for state in UserMovie.objects.filter(user=user).filter(
            _id_filter("movie", tmdb=tmdb, imdb=imdb)
        ).select_related("movie"):
            label = "watched" if state.is_seen else ("watchlist" if state.on_watchlist else None)
            if label is None:
                continue
            for key in _record_tokens(state.movie):
                movie_states[key] = label
    if show_ids:
        tmdb = {ids["tmdb"] for ids in show_ids if ids.get("tmdb")}
        tvdb = {ids["tvdb"] for ids in show_ids if ids.get("tvdb")}
        imdb = {ids["imdb"] for ids in show_ids if ids.get("imdb")}
        for state in UserShow.objects.filter(user=user).filter(
            _id_filter("show", tmdb=tmdb, tvdb=tvdb, imdb=imdb)
        ).select_related("show"):
            label = "watchlist" if state.on_watchlist else "tracked"
            for key in _record_tokens(state.show):
                show_states[key] = label

    annotated = []
    for entry in entries:
        states = movie_states if entry.media_type == "movie" else show_states
        label = None
        for provider in ("tmdb", "tvdb", "imdb"):
            value = entry.ids.get(provider)
            if value and f"{provider}:{value}" in states:
                label = states[f"{provider}:{value}"]
                break
        annotated.append(replace(entry, user_state=label))
    return annotated


def _id_filter(relation: str, *, tmdb=(), tvdb=(), imdb=()):
    from django.db.models import Q

    query = Q(pk__in=[])
    if tmdb:
        query |= Q(**{f"{relation}__tmdb_id__in": list(tmdb)})
    if tvdb:
        query |= Q(**{f"{relation}__tvdb_id__in": list(tvdb)})
    if imdb:
        query |= Q(**{f"{relation}__imdb_id__in": list(imdb)})
    return query


def _record_tokens(record) -> set[str]:
    tokens = set()
    for provider in ("tmdb", "tvdb", "imdb"):
        value = getattr(record, f"{provider}_id", None)
        if value:
            tokens.add(f"{provider}:{value}")
    return tokens


# -- Helpers -----------------------------------------------------------------


def argus_url_for(media_type: str, ids: dict) -> str | None:
    """The Argus detail page for a SIMKL item, when its TMDB/TVDB id is known."""
    if media_type == "movie":
        if ids.get("tmdb"):
            return reverse("movie-detail", kwargs={"external_id": ids["tmdb"]})
        return None
    if ids.get("tvdb"):
        return reverse("tv-detail", kwargs={"external_id": ids["tvdb"]})
    if ids.get("tmdb"):
        url = reverse("tv-detail", kwargs={"external_id": ids["tmdb"]})
        return f"{url}?provider=tmdb"
    return None


def _simkl_url(path, media_type: str, simkl_id, slug) -> str | None:
    if path:
        text = str(path)
        if text.startswith("http"):
            return text
        return f"{SIMKL_WEB_URL}{text if text.startswith('/') else '/' + text}"
    return build_simkl_url(media_type, simkl_id, slug)


def _year_from_release(value) -> int | None:
    if not value:
        return None
    text = str(value)
    for fmt in ("%m/%d/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(text[:10], fmt).year
        except ValueError:
            continue
    parsed = parse_timestamp(text)
    return parsed.year if parsed else None


def _rating(block) -> float | None:
    if not isinstance(block, dict):
        return None
    try:
        value = float(block.get("rating"))
    except (TypeError, ValueError):
        return None
    return value or None


def _as_int(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
