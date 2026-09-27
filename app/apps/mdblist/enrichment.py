"""Ratings from MDBList for the detail pages.

TMDB and TVDB remain the metadata source of record; MDBList adds the scores
they lack -- IMDb, Rotten Tomatoes (critics and audience), Metacritic,
Letterboxd and more in one call -- plus certification, a trailer, streaming
services and recommendations. The data is shared by every user like the rest
of the catalog.

The detail page loads the card over HTMX: the request fetches from MDBList on
the spot when nothing fresh is on hand, persists it for tracked titles and
caches it for untracked ones. A nightly job keeps tracked titles fresh with
batched lookups, which matters because every key has a daily request quota.
"""

from dataclasses import dataclass
from datetime import timedelta
from itertools import chain

from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from django.core.cache import cache
from django.db.models import Exists, OuterRef, Q
from django.utils import timezone
from django.utils.text import slugify

from apps.mdblist.client import MdblistError
from apps.mdblist.config import catalog_client_for
from apps.mdblist.identities import normalize_ids
from apps.mdblist.models import MdblistMediaInfo, build_mdblist_url


RECOMMENDATION_LIMIT = 12
NOT_FOUND_MULTIPLIER = 4
BATCH_SIZE = 100
INFO_FIELDS = (
    "media_type",
    "mdblist_id",
    "slug",
    "imdb_id",
    "score",
    "ratings",
    "certification",
    "trailer_url",
    "streams",
    "recommendations",
)
# Fields a batch lookup refreshes; recommendations only come with a single
# lookup and are kept as they are.
BATCH_FIELDS = tuple(name for name in INFO_FIELDS if name != "recommendations")

# source -> (label, scale, suffix); the display order of the card.
RATING_SOURCES = {
    "imdb": ("IMDb", 10, ""),
    "tomatoes": ("Rotten Tomatoes", 100, "%"),
    "popcorn": ("RT Audience", 100, "%"),
    "metacritic": ("Metacritic", 100, ""),
    "metacriticuser": ("Metacritic users", 10, ""),
    "letterboxd": ("Letterboxd", 5, ""),
    "tmdb": ("TMDB", 100, "%"),
    "trakt": ("Trakt", 100, "%"),
    "rogerebert": ("Roger Ebert", 4, ""),
    "myanimelist": ("MyAnimeList", 10, ""),
}


@dataclass(frozen=True)
class RatingBadge:
    source: str
    label: str
    value: float
    scale: int
    suffix: str
    votes: int | None
    url: str | None

    @property
    def display(self) -> str:
        if self.suffix == "%" or self.scale == 100:
            return f"{self.value:.0f}"
        return f"{self.value:.1f}"


@dataclass(frozen=True)
class Recommendation:
    title: str
    year: int | None
    media_type: str
    ids: dict
    poster_url: str | None
    argus_url: str | None
    mdblist_url: str | None

    @property
    def href(self) -> str | None:
        return self.argus_url or self.mdblist_url


def media_type_for(media) -> str:
    from apps.movies.models import Movie

    return "movie" if isinstance(media, Movie) else "show"


# -- Parsing -----------------------------------------------------------------


def parse_media(detail: dict) -> dict:
    """The stored fields of an MDBList media record."""
    ids = normalize_ids(detail.get("ids"))
    media_type = "movie" if detail.get("type") == "movie" else "show"
    ratings = []
    for rating in detail.get("ratings") or []:
        if not isinstance(rating, dict) or rating.get("source") not in RATING_SOURCES:
            continue
        value = _as_float(rating.get("value"))
        if value is None:
            continue
        ratings.append(
            {
                "source": rating["source"],
                "value": value,
                "votes": _as_int(rating.get("votes")),
                "url": rating.get("url") if isinstance(rating.get("url"), str) else None,
            }
        )
    return {
        "media_type": media_type,
        "mdblist_id": str(ids.get("mdblist") or "")[:32],
        "slug": slugify(str(detail.get("title") or ""))[:255],
        "imdb_id": str(ids.get("imdb") or "")[:32],
        "score": _as_int(detail.get("score")) or None,
        "ratings": ratings,
        "certification": str(detail.get("certification") or "")[:32],
        "trailer_url": str(detail.get("trailer") or "")[:255],
        "streams": [
            str(stream["name"])[:64]
            for stream in detail.get("streams") or []
            if isinstance(stream, dict) and stream.get("name")
        ][:12],
        "recommendations": _recommendations(detail.get("recommendations")),
    }


def _recommendations(items) -> list[dict]:
    if not isinstance(items, list):
        return []
    results = []
    for item in items:
        if not isinstance(item, dict) or not item.get("title"):
            continue
        results.append(
            {
                "title": str(item["title"])[:255],
                "year": _as_int(item.get("release_year") or item.get("year")),
                "type": "movie" if item.get("mediatype") == "movie" else "show",
                "ids": {
                    key: value
                    for key, value in normalize_ids(item.get("ids")).items()
                    if key in {"tmdb", "tvdb", "imdb", "mdblist"}
                },
                "poster": str(item.get("poster") or "")[:255],
            }
        )
        if len(results) >= RECOMMENDATION_LIMIT:
            break
    return results


# -- Tracked titles: persisted -----------------------------------------------


def get_media_info(media) -> MdblistMediaInfo | None:
    if media is None or media.pk is None:
        return None
    return MdblistMediaInfo.objects.filter(
        content_type=ContentType.objects.get_for_model(type(media)),
        object_id=media.pk,
    ).first()


def is_stale(info: MdblistMediaInfo | None) -> bool:
    if info is None or info.fetched_at is None:
        return True
    max_age = timedelta(days=settings.MDBLIST_METADATA_REFRESH_DAYS)
    if info.status == MdblistMediaInfo.Status.NOT_FOUND:
        max_age = max_age * NOT_FOUND_MULTIPLIER
    return info.fetched_at < timezone.now() - max_age


def stale_media_queryset(*, limit: int):
    """Tracked movies and shows whose MDBList data is missing or old."""
    from apps.movies.models import Movie
    from apps.tv.models import Show

    now = timezone.now()
    cutoff = now - timedelta(days=settings.MDBLIST_METADATA_REFRESH_DAYS)
    not_found_cutoff = now - timedelta(
        days=settings.MDBLIST_METADATA_REFRESH_DAYS * NOT_FOUND_MULTIPLIER
    )
    results = []
    for model in (Movie, Show):
        content_type = ContentType.objects.get_for_model(model)
        info = MdblistMediaInfo.objects.filter(content_type=content_type, object_id=OuterRef("pk"))
        fresh = info.filter(
            Q(status=MdblistMediaInfo.Status.NOT_FOUND, fetched_at__gte=not_found_cutoff)
            | Q(fetched_at__gte=cutoff)
        )
        queryset = (
            model.objects.filter(user_states__isnull=False, tmdb_id__isnull=False)
            .annotate(has_fresh_info=Exists(fresh))
            .filter(has_fresh_info=False, last_synced_at__isnull=False)
            .distinct()
            .order_by("id")[: max(1, limit)]
        )
        results.append(queryset)
    return list(chain.from_iterable(results))[: max(1, limit)]


def _lookup_target(media) -> tuple[str, str] | None:
    """The MDBList ``(provider, id)`` for a catalog record."""
    for provider in ("tmdb", "imdb", "tvdb"):
        value = getattr(media, f"{provider}_id", None)
        if value:
            return provider, str(value)
    return None


def _info_for(media) -> MdblistMediaInfo:
    info, _created = MdblistMediaInfo.objects.get_or_create(
        content_type=ContentType.objects.get_for_model(type(media)),
        object_id=media.pk,
        defaults={"media_type": media_type_for(media)},
    )
    return info


def _store(info: MdblistMediaInfo, data: dict | None, fields=INFO_FIELDS) -> MdblistMediaInfo:
    info.fetched_at = timezone.now()
    info.last_error = ""
    if data is None:
        info.status = MdblistMediaInfo.Status.NOT_FOUND
        info.save(update_fields=["status", "last_error", "fetched_at", "updated_at"])
        return info
    for name in fields:
        setattr(info, name, data[name])
    info.status = MdblistMediaInfo.Status.OK
    info.save()
    return info


def refresh_media_info(media, *, client, target: tuple[str, str] | None = None) -> MdblistMediaInfo:
    """Fetch (or re-fetch) the MDBList record for one movie or show and store it."""
    info = _info_for(media)
    target = target or _lookup_target(media)
    if target is None:
        return _store(info, None)
    try:
        detail = client.get_media(target[0], media_type_for(media), target[1], append=("recommendations",))
    except MdblistError as exc:
        info.status = MdblistMediaInfo.Status.ERROR
        info.last_error = str(exc)[:500]
        info.fetched_at = timezone.now()
        info.save(update_fields=["status", "last_error", "fetched_at", "updated_at"])
        raise
    return _store(info, parse_media(detail) if detail else None)


def refresh_media_batch(media_items, *, client) -> int:
    """Refresh many titles with batched lookups by TMDB id; returns how many
    were stored."""
    refreshed = 0
    groups: dict[str, list] = {}
    for media in media_items:
        if getattr(media, "tmdb_id", None):
            groups.setdefault(media_type_for(media), []).append(media)
    for media_type, items in groups.items():
        for start in range(0, len(items), BATCH_SIZE):
            chunk = items[start:start + BATCH_SIZE]
            try:
                details = client.get_media_batch("tmdb", media_type, [_as_int(item.tmdb_id) or item.tmdb_id for item in chunk])
            except MdblistError:
                if refreshed == 0:
                    raise
                return refreshed
            by_tmdb = {
                str(normalize_ids(detail.get("ids")).get("tmdb")): detail for detail in details
            }
            for media in chunk:
                detail = by_tmdb.get(str(media.tmdb_id))
                _store(_info_for(media), parse_media(detail) if detail else None, BATCH_FIELDS)
                refreshed += 1
    return refreshed


# -- Detail card context -----------------------------------------------------


def load_card(media_type: str, provider: str, external_id: str, *, language: str, user=None, client=None) -> dict:
    """Context for the MDBList card of a detail page, fetching on the spot.

    Tracked titles persist the result in :class:`MdblistMediaInfo`; untracked
    previews keep it in the cache so browsing does not grow the catalog.
    """
    client = client or catalog_client_for(user)
    if client is None:
        return {"state": "unavailable"}
    media_type = "movie" if media_type == "movie" else "show"
    media = _find_media(media_type, provider, external_id)

    if media is not None:
        info = get_media_info(media)
        if is_stale(info):
            target = _lookup_target(media)
            if target is None:
                ids = _ids_from_provider(media_type, provider, external_id, language)
                target = next(((key, ids[key]) for key in ("tmdb", "imdb", "tvdb") if ids.get(key)), None)
            try:
                info = refresh_media_info(media, client=client, target=target)
            except MdblistError:
                return {"state": "error"}
        if info is None or info.status != MdblistMediaInfo.Status.OK:
            return {"state": "not_found"}
        return _card_context({name: getattr(info, name) for name in INFO_FIELDS}, media_type)

    key = f"mdblist:info:{media_type}:{provider}:{external_id}"
    cached = cache.get(key)
    if cached is None:
        target = (provider, str(external_id)) if provider in {"tmdb", "tvdb"} else None
        try:
            detail = (
                client.get_media(target[0], media_type, target[1], append=("recommendations",))
                if target
                else None
            )
        except MdblistError:
            return {"state": "error"}
        data = parse_media(detail) if detail else None
        cached = data if data is not None else {"not_found": True}
        ttl = timedelta(
            days=settings.MDBLIST_METADATA_REFRESH_DAYS * (NOT_FOUND_MULTIPLIER if data is None else 1)
        )
        cache.set(key, cached, int(ttl.total_seconds()))
    if cached.get("not_found"):
        return {"state": "not_found"}
    return _card_context(cached, media_type)


def _card_context(data: dict, media_type: str) -> dict:
    ratings = {rating["source"]: rating for rating in data.get("ratings") or []}
    badges = []
    for source, (label, scale, suffix) in RATING_SOURCES.items():
        rating = ratings.get(source)
        if rating is None:
            continue
        badges.append(
            RatingBadge(
                source=source,
                label=label,
                value=rating["value"],
                scale=scale,
                suffix=suffix,
                votes=rating.get("votes"),
                url=rating_url(source, rating.get("url"), media_type=media_type, imdb_id=data.get("imdb_id")),
            )
        )
    return {
        "state": "ok",
        "url": build_mdblist_url(media_type, data.get("mdblist_id"), data.get("slug")),
        "score": data.get("score"),
        "ratings": badges,
        "certification": data.get("certification") or None,
        "trailer_url": data.get("trailer_url") or None,
        "streams": data.get("streams") or [],
        "recommendations": _recommendations_for(data.get("recommendations")),
    }


def rating_url(source: str, path: str | None, *, media_type: str, imdb_id: str | None) -> str | None:
    """Deep link to a rating's source site; MDBList sends most as a path."""
    if source == "imdb":
        return f"https://www.imdb.com/title/{imdb_id}/" if imdb_id else None
    if not path:
        return None
    if path.startswith("http"):
        return path
    section = "movie" if media_type == "movie" else "tv"
    prefix = {
        "tomatoes": "https://www.rottentomatoes.com",
        "popcorn": "https://www.rottentomatoes.com",
        "metacritic": f"https://www.metacritic.com/{section}",
        "letterboxd": "https://letterboxd.com",
        "rogerebert": "https://www.rogerebert.com/reviews",
    }.get(source)
    if prefix is None:
        return None
    return f"{prefix}/{path.lstrip('/')}"


def _recommendations_for(items) -> list[Recommendation]:
    from apps.simkl.trending import argus_url_for

    results = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        media_type = item.get("type") or "show"
        ids = item.get("ids") or {}
        results.append(
            Recommendation(
                title=item.get("title") or "",
                year=item.get("year"),
                media_type=media_type,
                ids=ids,
                poster_url=item.get("poster") or None,
                argus_url=argus_url_for(media_type, ids),
                mdblist_url=build_mdblist_url(media_type, ids.get("mdblist"), slugify(item.get("title") or "")),
            )
        )
    return results


def _find_media(media_type: str, provider: str, external_id: str):
    from apps.movies.models import Movie
    from apps.tv.models import Show

    model = Movie if media_type == "movie" else Show
    return model.objects.filter(provider=provider, external_id=str(external_id)).first()


def _ids_from_provider(media_type: str, provider: str, external_id: str, language: str) -> dict:
    from apps.catalog.providers.exceptions import ProviderError
    from apps.catalog.services import get_movie_detail, get_show_detail

    getter = get_movie_detail if media_type == "movie" else get_show_detail
    try:
        detail = getter(str(external_id), provider=provider, language=language)
    except (ProviderError, ValueError):
        return {}
    ids = {"imdb": detail.imdb_id, "tmdb": detail.tmdb_id, "tvdb": detail.tvdb_id}
    if not ids["tmdb"] and provider == "tmdb":
        ids["tmdb"] = str(external_id)
    if not ids["tvdb"] and provider == "tvdb":
        ids["tvdb"] = str(external_id)
    return {key: value for key, value in ids.items() if value}


def _as_int(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _as_float(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
