"""Community data from SIMKL for the detail pages.

TMDB and TVDB remain the metadata source of record; SIMKL only adds what
they lack -- IMDb and SIMKL community ratings, drop rate, rank,
certification, a trailer fallback and "viewers also watched" picks. The
catalog endpoints need no user token, only the server's client id, so the
data is shared by every user like the rest of the catalog.

The detail page loads the card over HTMX: the request fetches from SIMKL on
the spot when nothing fresh is on hand, persists it for tracked titles and
caches it for untracked ones. A nightly job keeps tracked titles fresh.
"""

from dataclasses import dataclass
from datetime import timedelta
from itertools import chain

from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from django.core.cache import cache
from django.db.models import Exists, OuterRef, Q
from django.utils import timezone

from apps.simkl.client import SimklError, SimklNotFound
from apps.simkl.config import build_client, catalog_configured
from apps.simkl.models import SimklMediaInfo, build_simkl_url


POSTER_BASE_URL = "https://simkl.in/posters"
POSTER_PROXY_URL = "https://wsrv.nl/?url="
YOUTUBE_URL = "https://www.youtube.com/watch?v="
RECOMMENDATION_LIMIT = 12
NOT_FOUND_MULTIPLIER = 4
INFO_FIELDS = (
    "media_type",
    "simkl_id",
    "slug",
    "simkl_rating",
    "simkl_votes",
    "imdb_rating",
    "imdb_votes",
    "rank",
    "drop_rate",
    "certification",
    "trailer_url",
    "recommendations",
)


@dataclass(frozen=True)
class Recommendation:
    title: str
    year: int | None
    media_type: str
    simkl_id: int | None
    simkl_url: str | None
    poster_url: str | None
    argus_url: str | None = None

    @property
    def href(self) -> str | None:
        return self.argus_url or self.simkl_url


def poster_url(path: str | None, size: str = "_ca") -> str | None:
    """Compose a SIMKL poster URL through the image proxy SIMKL recommends."""
    if not path:
        return None
    return f"{POSTER_PROXY_URL}{POSTER_BASE_URL}/{path}{size}.webp&q=90"


def media_type_for(media) -> str:
    from apps.movies.models import Movie

    return "movie" if isinstance(media, Movie) else "show"


# -- Fetching ------------------------------------------------------------------


def fetch_info(
    client,
    media_type: str,
    *,
    imdb=None,
    tmdb=None,
    tvdb=None,
    simkl_id: int | None = None,
    target_type: str | None = None,
) -> dict | None:
    """Fetch the SIMKL record for a title as a plain dict, or ``None`` when
    SIMKL does not know it. Raises :class:`SimklError` on API trouble."""
    resolved_type = target_type or media_type
    if not simkl_id:
        target = client.resolve(
            media_type="movie" if media_type == "movie" else "tv",
            imdb=imdb,
            tmdb=tmdb,
            tvdb=tvdb,
        )
        if target is None:
            return None
        simkl_id = target.simkl_id
        resolved_type = target.media_type
    try:
        detail = client.get_detail(resolved_type, simkl_id)
    except SimklNotFound:
        return None
    if not detail:
        return None

    ids = detail.get("ids") if isinstance(detail.get("ids"), dict) else {}
    ratings = detail.get("ratings") if isinstance(detail.get("ratings"), dict) else {}
    simkl_rating = ratings.get("simkl") if isinstance(ratings.get("simkl"), dict) else {}
    imdb_rating = ratings.get("imdb") if isinstance(ratings.get("imdb"), dict) else {}
    return {
        "media_type": resolved_type,
        "simkl_id": int(ids.get("simkl") or ids.get("simkl_id") or simkl_id),
        "slug": str(ids.get("slug") or "")[:255],
        "simkl_rating": _as_float(simkl_rating.get("rating")),
        "simkl_votes": _as_int(simkl_rating.get("votes")),
        "imdb_rating": _as_float(imdb_rating.get("rating")),
        "imdb_votes": _as_int(imdb_rating.get("votes")),
        "rank": _as_int(detail.get("rank")),
        "drop_rate": str(detail.get("droprate") or detail.get("drop_rate") or "")[:16],
        "certification": str(detail.get("certification") or "")[:32],
        "trailer_url": _trailer_url(detail.get("trailers")),
        "recommendations": _recommendations(detail.get("users_recommendations")),
    }


# -- Tracked titles: persisted -----------------------------------------------


def get_media_info(media) -> SimklMediaInfo | None:
    if media is None or media.pk is None:
        return None
    return SimklMediaInfo.objects.filter(
        content_type=ContentType.objects.get_for_model(type(media)),
        object_id=media.pk,
    ).first()


def is_stale(info: SimklMediaInfo | None) -> bool:
    if info is None or info.fetched_at is None:
        return True
    max_age = timedelta(days=settings.SIMKL_METADATA_REFRESH_DAYS)
    if info.status == SimklMediaInfo.Status.NOT_FOUND:
        # Unmatched titles are retried far less often; SIMKL's catalog grows
        # but rarely overnight.
        max_age = max_age * NOT_FOUND_MULTIPLIER
    return info.fetched_at < timezone.now() - max_age


def stale_media_queryset(*, limit: int):
    """Tracked movies and shows whose SIMKL data is missing or old."""
    from apps.movies.models import Movie
    from apps.tv.models import Show

    now = timezone.now()
    cutoff = now - timedelta(days=settings.SIMKL_METADATA_REFRESH_DAYS)
    not_found_cutoff = now - timedelta(
        days=settings.SIMKL_METADATA_REFRESH_DAYS * NOT_FOUND_MULTIPLIER
    )
    results = []
    for model in (Movie, Show):
        content_type = ContentType.objects.get_for_model(model)
        info = SimklMediaInfo.objects.filter(
            content_type=content_type,
            object_id=OuterRef("pk"),
        )
        fresh = info.filter(
            Q(status=SimklMediaInfo.Status.NOT_FOUND, fetched_at__gte=not_found_cutoff)
            | Q(fetched_at__gte=cutoff)
        )
        queryset = (
            model.objects.filter(user_states__isnull=False)
            .annotate(has_fresh_info=Exists(fresh))
            .filter(has_fresh_info=False, last_synced_at__isnull=False)
            .distinct()
            .order_by("id")[: max(1, limit)]
        )
        results.append(queryset)
    return list(chain.from_iterable(results))[: max(1, limit)]


def refresh_media_info(media, *, client, ids: dict | None = None) -> SimklMediaInfo:
    """Fetch (or re-fetch) the SIMKL record for one movie or show and store it.

    ``ids`` overrides the identifiers read from the record, for rows the
    catalog import has not filled in yet.
    """
    media_type = media_type_for(media)
    content_type = ContentType.objects.get_for_model(type(media))
    info, _created = SimklMediaInfo.objects.get_or_create(
        content_type=content_type,
        object_id=media.pk,
        defaults={"media_type": media_type},
    )
    now = timezone.now()
    ids = ids or {
        "imdb": media.imdb_id,
        "tmdb": media.tmdb_id,
        "tvdb": media.tvdb_id,
    }
    try:
        data = fetch_info(
            client,
            media_type,
            imdb=ids.get("imdb"),
            tmdb=ids.get("tmdb"),
            tvdb=ids.get("tvdb"),
            simkl_id=info.simkl_id,
            target_type=info.media_type or None,
        )
    except SimklError as exc:
        info.status = SimklMediaInfo.Status.ERROR
        info.last_error = str(exc)[:500]
        info.fetched_at = now
        info.save(update_fields=["status", "last_error", "fetched_at", "updated_at"])
        raise

    if data is None:
        info.status = SimklMediaInfo.Status.NOT_FOUND
        info.last_error = ""
        info.fetched_at = now
        info.save(update_fields=["status", "last_error", "fetched_at", "updated_at"])
        return info

    for field_name in INFO_FIELDS:
        setattr(info, field_name, data[field_name])
    info.status = SimklMediaInfo.Status.OK
    info.last_error = ""
    info.fetched_at = now
    info.save()
    return info


# -- Detail card context -----------------------------------------------------


def load_card(media_type: str, provider: str, external_id: str, *, language: str, client=None) -> dict:
    """Context for the SIMKL card of a detail page, fetching on the spot.

    Tracked titles (and rated stubs) persist the result in
    :class:`SimklMediaInfo`; untracked previews keep it in the cache so a
    title someone is only browsing does not grow the catalog.
    """
    if not catalog_configured():
        return {"state": "unavailable"}
    media_type = "movie" if media_type == "movie" else "show"
    media = _find_media(media_type, provider, external_id)
    client = client or build_client()

    if media is not None:
        info = get_media_info(media)
        if is_stale(info):
            ids = None
            if not (media.imdb_id or media.tmdb_id or media.tvdb_id):
                ids = _ids_from_provider(media_type, provider, external_id, language)
            try:
                info = refresh_media_info(media, client=client, ids=ids)
            except SimklError:
                return {"state": "error"}
        if info is None or info.status != SimklMediaInfo.Status.OK:
            return {"state": "not_found"}
        return _card_context({name: getattr(info, name) for name in INFO_FIELDS})

    key = f"simkl:info:{media_type}:{provider}:{external_id}"
    cached = cache.get(key)
    if cached is None:
        ids = _ids_from_provider(media_type, provider, external_id, language)
        try:
            data = fetch_info(client, media_type, **ids) if ids else None
        except SimklError:
            return {"state": "error"}
        cached = data if data is not None else {"not_found": True}
        ttl = timedelta(
            days=settings.SIMKL_METADATA_REFRESH_DAYS
            * (NOT_FOUND_MULTIPLIER if data is None else 1)
        )
        cache.set(key, cached, int(ttl.total_seconds()))
    if cached.get("not_found"):
        return {"state": "not_found"}
    return _card_context(cached)


def _card_context(data: dict) -> dict:
    return {
        "state": "ok",
        "url": build_simkl_url(data.get("media_type"), data.get("simkl_id"), data.get("slug")),
        "simkl_rating": data.get("simkl_rating"),
        "simkl_votes": data.get("simkl_votes"),
        "imdb_rating": data.get("imdb_rating"),
        "imdb_votes": data.get("imdb_votes"),
        "rank": data.get("rank"),
        "drop_rate": data.get("drop_rate") or None,
        "certification": data.get("certification") or None,
        "trailer_url": data.get("trailer_url") or None,
        "recommendations": _recommendations_for(data.get("recommendations"), data.get("media_type")),
    }


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


# -- Parsing helpers -----------------------------------------------------------


def _trailer_url(trailers) -> str:
    if not isinstance(trailers, list):
        return ""
    # Prefer the highest-resolution clip that calls itself a trailer.
    candidates = [item for item in trailers if isinstance(item, dict) and item.get("youtube")]
    if not candidates:
        return ""
    candidates.sort(
        key=lambda item: (
            "trailer" in str(item.get("name") or "").lower(),
            _as_int(item.get("size")) or 0,
        ),
        reverse=True,
    )
    return f"{YOUTUBE_URL}{candidates[0]['youtube']}"[:255]


def _recommendations(items) -> list[dict]:
    if not isinstance(items, list):
        return []
    results = []
    for item in items:
        if not isinstance(item, dict) or not item.get("title"):
            continue
        ids = item.get("ids") if isinstance(item.get("ids"), dict) else {}
        results.append(
            {
                "title": str(item["title"])[:255],
                "year": _as_int(item.get("year")),
                "type": {"tv": "show", "shows": "show", "movies": "movie"}.get(
                    str(item.get("type") or ""), str(item.get("type") or "")
                )[:16],
                "simkl_id": _as_int(ids.get("simkl") or ids.get("simkl_id")),
                "slug": str(ids.get("slug") or "")[:255],
                "poster": str(item.get("poster") or "")[:255],
            }
        )
        if len(results) >= RECOMMENDATION_LIMIT:
            break
    return results


def _recommendations_for(items, default_type) -> list[Recommendation]:
    from django.urls import reverse

    results = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        media_type = item.get("type") or default_type or "show"
        simkl_id = item.get("simkl_id")
        results.append(
            Recommendation(
                title=item.get("title") or "",
                year=item.get("year"),
                media_type=media_type,
                simkl_id=simkl_id,
                simkl_url=build_simkl_url(media_type, simkl_id, item.get("slug")),
                poster_url=poster_url(item.get("poster")),
                # SIMKL only hands out its own id here; the Argus page is found
                # on click, so the card does not cost a dozen lookups per view.
                argus_url=(
                    reverse(
                        "simkl-open",
                        kwargs={"media_type": media_type, "simkl_id": simkl_id},
                    )
                    if simkl_id
                    else None
                ),
            )
        )
    return results


def resolve_argus_url(media_type: str, simkl_id: int, *, client=None) -> str | None:
    """The Argus detail page for a SIMKL id, via the cached detail endpoint."""
    from apps.simkl.identities import normalize_ids
    from apps.simkl.trending import argus_url_for

    if not catalog_configured():
        return None
    key = f"simkl:open:{media_type}:{simkl_id}"
    cached = cache.get(key)
    if cached is None:
        client = client or build_client()
        try:
            detail = client.get_detail(media_type, simkl_id) or {}
        except SimklError:
            return None
        ids = normalize_ids(detail.get("ids"))
        cached = argus_url_for("movie" if media_type == "movie" else "show", ids) or ""
        cache.set(
            key,
            cached,
            int(timedelta(days=settings.SIMKL_METADATA_REFRESH_DAYS).total_seconds()),
        )
    return cached or None


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
