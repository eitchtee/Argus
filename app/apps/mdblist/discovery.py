"""Discovery rails from MDBList: streaming charts, official lists and the
viewer's own recommendations.

Everything but the recommendations is the same for every viewer and is kept
in the Django cache for hours, so a busy Home page costs a few requests a day
against the key's quota.
"""

from dataclasses import dataclass

from django.core.cache import cache
from django.utils.text import slugify

from apps.mdblist.client import MdblistError
from apps.mdblist.config import catalog_client_for, user_account
from apps.mdblist.identities import normalize_ids
from apps.mdblist.models import MDBLIST_WEB_URL, build_mdblist_url  # noqa: F401 - views link to the site


CACHE_TTL = 6 * 60 * 60
FAILURE_TTL = 10 * 60
# Argus timeframe -> MDBList chart period.
PERIODS = {"today": "1d", "week": "7d", "month": "30d"}
OFFICIAL_LISTS = ("trending", "popular", "anticipated")
# Personalised sections in order of preference; ``rising`` is the one every
# account gets.
RECOMMENDATION_SECTIONS = ("recommended", "because-watched", "similar", "trending", "rising")



@dataclass(frozen=True)
class DiscoveryEntry:
    title: str
    year: int | None
    media_type: str
    ids: dict
    poster_url: str | None
    argus_url: str | None
    mdblist_url: str | None
    score: int | None
    rank: int | None = None
    delta: int | None = None
    user_state: str | None = None

    @property
    def href(self) -> str | None:
        return self.argus_url or self.mdblist_url


def streaming_chart(user, media_type: str, timeframe: str = "today", *, limit: int = 20, hide_seen: bool = False) -> list[DiscoveryEntry]:
    """JustWatch's streaming chart for movies or shows, as MDBList serves it."""
    if media_type not in {"movie", "show"}:
        return []
    period = PERIODS.get(timeframe, "1d")
    raw = _cached(
        user,
        f"mdblist:chart:{media_type}:{period}",
        lambda client: client.get_streaming_chart(media_type, period=period, size=20),
    )
    entries = [_entry(item, media_type) for item in raw or []]
    return _finish(user, entries, limit=limit, hide_seen=hide_seen)


def official_list(user, slug: str, *, limit: int = 24, hide_seen: bool = False) -> list[DiscoveryEntry]:
    if slug not in OFFICIAL_LISTS:
        return []
    raw = _cached(
        user,
        f"mdblist:official:{slug}",
        lambda client: client.get_official_list_items(slug, limit=60),
    )
    return _finish(user, _list_entries(raw), limit=limit, hide_seen=hide_seen)


def recommendations(user, *, limit: int = 24) -> tuple[dict | None, list[DiscoveryEntry]]:
    """The viewer's best available recommendation section and its items.

    Needs the viewer's own MDBList account; most sections are for MDBList
    supporters, and everyone gets ``rising``.
    """
    account = user_account(user)
    if account is None:
        return None, []
    key = f"mdblist:recommended:{account.id}"
    cached = cache.get(key)
    if cached is None:
        from apps.mdblist.config import build_client

        try:
            client = build_client(account)
            available = {section.get("slug"): section for section in client.get_recommendation_sections()}
            slug = next((slug for slug in RECOMMENDATION_SECTIONS if slug in available), None)
            items = client.get_recommendation_items(slug, limit=60) if slug else {}
            cached = {"section": available.get(slug), "items": items}
            cache.set(key, cached, CACHE_TTL)
        except MdblistError:
            cache.set(key, {"section": None, "items": {}}, FAILURE_TTL)
            return None, []
    entries = _finish(user, _list_entries(cached.get("items")), limit=limit, hide_seen=True)
    return cached.get("section"), entries


def list_url(slug: str, media_type: str = "movies") -> str:
    return f"{MDBLIST_WEB_URL}/lists/official/{media_type}/{slug}"


# -- Helpers -----------------------------------------------------------------


def _cached(user, key: str, fetch):
    cached = cache.get(key)
    if cached is not None:
        return cached
    client = catalog_client_for(user)
    if client is None:
        return None
    try:
        payload = fetch(client)
    except MdblistError:
        cache.set(key, [], FAILURE_TTL)
        return []
    cache.set(key, payload, CACHE_TTL)
    return payload


def _list_entries(payload) -> list[DiscoveryEntry]:
    if not isinstance(payload, dict):
        return []
    entries = []
    for media_type, key in (("movie", "movies"), ("show", "shows")):
        for item in payload.get(key) or []:
            entries.append(_entry(item, media_type))
    # Official lists hold movies and shows side by side; keep MDBList's order.
    entries = [entry for entry in entries if entry is not None]
    entries.sort(key=lambda entry: entry.rank if entry.rank is not None else 10**6)
    return entries


def _entry(item, media_type: str) -> DiscoveryEntry | None:
    from apps.simkl.trending import argus_url_for

    if not isinstance(item, dict) or not item.get("title"):
        return None
    media_type = "movie" if (item.get("mediatype") or media_type) == "movie" else "show"
    ids = {
        key: value
        for key, value in normalize_ids(item.get("ids")).items()
        if key in {"tmdb", "tvdb", "imdb", "mdblist"}
    }
    if "tmdb" not in ids and item.get("id"):
        ids["tmdb"] = str(item["id"])
    return DiscoveryEntry(
        title=str(item["title"]),
        year=_as_int(item.get("year") or item.get("release_year")),
        media_type=media_type,
        ids=ids,
        poster_url=item.get("poster") or None,
        argus_url=argus_url_for(media_type, ids),
        mdblist_url=build_mdblist_url(media_type, ids.get("mdblist"), slugify(str(item["title"]))),
        score=_score(item),
        rank=_as_int(item.get("rank")),
        delta=_as_int(item.get("delta")),
    )


def _score(item: dict) -> int | None:
    score = _as_int(item.get("score"))
    if score:
        return score
    for rating in item.get("ratings") or []:
        if isinstance(rating, dict) and rating.get("source") == "mdblist":
            return _as_int(rating.get("score")) or None
    return None


def _finish(user, entries, *, limit: int, hide_seen: bool) -> list[DiscoveryEntry]:
    from apps.simkl.trending import annotate_user_state

    entries = annotate_user_state(user, [entry for entry in entries if entry is not None])
    if hide_seen:
        entries = [entry for entry in entries if entry.user_state is None]
    return entries[:limit]


def _as_int(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
