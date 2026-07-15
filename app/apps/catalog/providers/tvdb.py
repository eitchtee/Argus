import json
from urllib.error import HTTPError
from urllib.parse import urlencode, urljoin
from urllib.request import Request, urlopen

from django.conf import settings
from django.core.cache import cache

from apps.catalog.providers.base import (
    BaseProvider,
    CastMemberDTO,
    DetailDTO,
    EpisodeDTO,
    GenreDTO,
    LanguageOptionDTO,
    SeasonDTO,
    SearchResultDTO,
)
from apps.catalog.providers.exceptions import AuthError, NotFound, ProviderError, RateLimited


class TVDBProvider(BaseProvider):
    name = "tvdb"
    api_base_url = "https://api4.thetvdb.com/v4"
    artwork_base_url = "https://artworks.thetvdb.com/"
    token_cache_key = "catalog:tvdb:token"
    token_cache_timeout = 60 * 60 * 24

    def __init__(self, *, api_key: str | None = None, opener=urlopen, timeout: int = 10):
        self.api_key = settings.TVDB_API_KEY if api_key is None else api_key
        self.opener = opener
        self.timeout = timeout

    def search(
        self,
        query: str,
        *,
        language: str,
        page: int = 1,
    ) -> list[SearchResultDTO]:
        payload = self._get_json(
            "/search",
            params={
                "query": query,
                "type": "series",
                "language": language,
                # TVDB's /search endpoint is 0-indexed, unlike our 1-indexed UI pages.
                "page": page - 1,
            },
        )

        return [
            SearchResultDTO(
                provider=self.name,
                external_id=str(item.get("tvdb_id") or item.get("id")),
                title=item.get("name") or "",
                year=self._int_or_none(item.get("year")),
                poster_url=item.get("image_url") or item.get("image"),
                overview=item.get("overview") or "",
            )
            for item in payload.get("data", [])
        ]

    def fetch_detail(self, external_id: str, *, language: str) -> DetailDTO:
        payload = self._get_json(f"/series/{external_id}/extended")
        data = payload.get("data", {})
        status = data.get("status") or {}
        network = data.get("originalNetwork") or data.get("network") or {}
        translations = {
            "eng": self._non_empty_values(
                title=data.get("name"),
                overview=data.get("overview"),
            )
        }
        if language != "eng":
            try:
                translated_payload = self._get_json(
                    f"/series/{external_id}/translations/{language}"
                )
            except NotFound:
                pass
            else:
                translated = translated_payload.get("data") or {}
                values = self._non_empty_values(
                    title=translated.get("name"),
                    overview=translated.get("overview"),
                )
                if values:
                    translations[language] = values

        return DetailDTO(
            provider=self.name,
            external_id=str(data["id"]),
            title=data.get("name") or "",
            original_title=data.get("name") or "",
            overview=data.get("overview") or "",
            poster_path=data.get("image"),
            backdrop_path=self._backdrop_from_artworks(data),
            release_date=data.get("firstAired") or None,
            status=status.get("name") or "",
            network=network.get("name"),
            imdb_id=self._imdb_id_from_remote_ids(data),
            tmdb_id=self._tmdb_id_from_remote_ids(data),
            trailer_url=self._trailer_from_data(data),
            cast=self._cast_from_characters(data),
            average_runtime=data.get("averageRuntime"),
            next_air_date=data.get("nextAired") or None,
            last_air_date=data.get("lastAired") or None,
            airs_time=data.get("airsTime") or None,
            genres=[
                GenreDTO(
                    provider=self.name,
                    external_id=str(genre["id"]),
                    name=genre.get("name") or "",
                    translations=(
                        {"eng": {"name": genre["name"]}}
                        if genre.get("name")
                        else {}
                    ),
                )
                for genre in data.get("genres", [])
            ],
            translations={code: values for code, values in translations.items() if values},
        )

    def _cast_from_characters(self, data: dict) -> list[CastMemberDTO]:
        characters = [c for c in data.get("characters", []) if c.get("peopleType") == "Actor"]
        sorted_characters = sorted(characters, key=lambda c: c.get("sort", 0))
        return [
            CastMemberDTO(
                name=character.get("personName") or "",
                character=character.get("name") or "",
                photo_url=character.get("personImgURL"),
            )
            for character in sorted_characters[:10]
        ]

    def _backdrop_from_artworks(self, data: dict) -> str | None:
        backgrounds = [a for a in data.get("artworks", []) if a.get("type") == 3]
        if not backgrounds:
            return None
        best = max(backgrounds, key=lambda a: a.get("score", 0))
        return best.get("image")

    def _imdb_id_from_remote_ids(self, data: dict) -> str | None:
        for remote_id in data.get("remoteIds", []):
            if remote_id.get("sourceName") == "IMDB":
                return remote_id.get("id")
        return None

    def _tmdb_id_from_remote_ids(self, data: dict) -> str | None:
        for remote_id in data.get("remoteIds", []):
            if remote_id.get("sourceName") == "TheMovieDB.com":
                return remote_id.get("id")
        return None

    def _trailer_from_data(self, data: dict) -> str | None:
        trailers = data.get("trailers") or []
        if not trailers:
            return None
        return trailers[0].get("url")

    def fetch_episodes(self, external_id: str, *, language: str) -> list[EpisodeDTO]:
        payload = self._get_json(
            f"/series/{external_id}/episodes/default/{language}"
        )
        data = payload.get("data", {})
        episodes = data.get("episodes", data if isinstance(data, list) else [])

        return [
            EpisodeDTO(
                season_number=episode.get("seasonNumber") or 0,
                episode_number=episode.get("number") or 0,
                absolute_number=episode.get("absoluteNumber"),
                name=episode.get("name") or "",
                overview=episode.get("overview") or "",
                still_path=self._artwork_url(episode.get("image")),
                air_date=episode.get("aired") or None,
                runtime=episode.get("runtime"),
                finale_type=episode.get("finaleType"),
                translations={
                    language: self._non_empty_values(
                        name=episode.get("name"),
                        overview=episode.get("overview"),
                    )
                },
            )
            for episode in episodes
        ]

    def fetch_seasons(self, external_id: str, *, language: str) -> list[SeasonDTO]:
        payload = self._get_json(f"/series/{external_id}/extended")
        seasons = []
        for season in (payload.get("data") or {}).get("seasons", []):
            translations = {
                "eng": self._non_empty_values(
                    name=season.get("name"),
                    overview=season.get("overview"),
                )
            }
            if language != "eng":
                try:
                    translated_payload = self._get_json(
                        f"/seasons/{season['id']}/translations/{language}"
                    )
                except NotFound:
                    pass
                else:
                    translated = translated_payload.get("data") or {}
                    values = self._non_empty_values(
                        name=translated.get("name"),
                        overview=translated.get("overview"),
                    )
                    if values:
                        translations[language] = values
            seasons.append(
                SeasonDTO(
                    season_number=season.get("number") or 0,
                    name=season.get("name") or "",
                    overview=season.get("overview") or "",
                    poster_path=season.get("image"),
                    translations={
                        code: values
                        for code, values in translations.items()
                        if values
                    },
                )
            )
        return seasons

    def list_languages(self) -> list[LanguageOptionDTO]:
        payload = self._get_json("/languages")
        return [
            LanguageOptionDTO(
                code=str(item.get("id") or item.get("shortCode")),
                name=item.get("nativeName") or item.get("name") or str(item.get("id")),
            )
            for item in payload.get("data", [])
            if item.get("id") or item.get("shortCode")
        ]

    def _non_empty_values(self, **values) -> dict[str, str]:
        return {key: value for key, value in values.items() if value}

    def _artwork_url(self, path: str | None) -> str | None:
        if not path:
            return None
        return urljoin(self.artwork_base_url, path)

    def _get_json(
        self,
        path: str,
        *,
        params: dict[str, object] | None = None,
        retry_auth: bool = True,
    ) -> dict:
        token = self._get_token()
        query = f"?{urlencode(params)}" if params else ""
        request = Request(
            f"{self.api_base_url}{path}{query}",
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {token}",
            },
        )

        try:
            with self.opener(request, timeout=self.timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            if exc.code == 401 and retry_auth:
                cache.delete(self.token_cache_key)
                return self._get_json(path, params=params, retry_auth=False)
            self._raise_provider_error(exc)
        except OSError as exc:
            raise ProviderError(f"TVDB request failed: {exc}") from exc

        raise ProviderError("TVDB request failed without a response.")

    def _get_token(self) -> str:
        token = cache.get(self.token_cache_key)
        if token:
            return token

        if not self.api_key:
            raise AuthError("TVDB_API_KEY is required.")

        payload = json.dumps({"apikey": self.api_key}).encode("utf-8")
        request = Request(
            f"{self.api_base_url}/login",
            data=payload,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
            method="POST",
        )

        try:
            with self.opener(request, timeout=self.timeout) as response:
                data = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            self._raise_provider_error(exc)
        except OSError as exc:
            raise ProviderError(f"TVDB login failed: {exc}") from exc

        token = data.get("data", {}).get("token")
        if not token:
            raise AuthError("TVDB login did not return a bearer token.")

        cache.set(self.token_cache_key, token, self.token_cache_timeout)
        return token

    def _raise_provider_error(self, exc: HTTPError):
        if exc.code == 404:
            raise NotFound("TVDB item was not found.") from exc
        if exc.code in {401, 403}:
            raise AuthError("TVDB authentication failed.") from exc
        if exc.code == 429:
            retry_after = exc.headers.get("Retry-After")
            if retry_after:
                raise RateLimited(f"TVDB rate limit exceeded. Retry after {retry_after} seconds.") from exc
            raise RateLimited("TVDB rate limit exceeded.") from exc

        raise ProviderError(f"TVDB request failed with HTTP {exc.code}.") from exc

    def _int_or_none(self, value) -> int | None:
        if value in (None, ""):
            return None

        try:
            return int(value)
        except (TypeError, ValueError):
            return None
