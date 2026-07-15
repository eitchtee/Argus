import json
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from django.conf import settings

from apps.catalog.providers.base import (
    BaseProvider,
    CastMemberDTO,
    DetailDTO,
    GenreDTO,
    LanguageOptionDTO,
    SearchResultDTO,
)
from apps.catalog.providers.exceptions import AuthError, NotFound, ProviderError, RateLimited


class TMDBProvider(BaseProvider):
    name = "tmdb"
    api_base_url = "https://api.themoviedb.org/3"
    poster_size = "w342"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        image_base_url: str | None = None,
        opener=urlopen,
        timeout: int = 10,
    ):
        self.api_key = settings.TMDB_API_KEY if api_key is None else api_key
        self.image_base_url = (
            settings.TMDB_IMAGE_BASE_URL if image_base_url is None else image_base_url
        )
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
            "/search/movie",
            {
                "query": query,
                "page": page,
                "language": language,
            },
        )

        return [
            SearchResultDTO(
                provider=self.name,
                external_id=str(item["id"]),
                title=item.get("title") or "",
                year=self._year_from_date(item.get("release_date")),
                poster_url=self._poster_url(item.get("poster_path")),
                overview=item.get("overview") or "",
            )
            for item in payload.get("results", [])
        ]

    def fetch_detail(self, external_id: str, *, language: str) -> DetailDTO:
        payload = self._get_json(
            f"/movie/{external_id}",
            {
                "language": language,
                "append_to_response": "credits,external_ids,videos,translations",
            },
        )

        return DetailDTO(
            provider=self.name,
            external_id=str(payload["id"]),
            title=payload.get("title") or "",
            original_title=payload.get("original_title") or "",
            overview=payload.get("overview") or "",
            tagline=payload.get("tagline") or "",
            poster_path=payload.get("poster_path"),
            backdrop_path=payload.get("backdrop_path"),
            release_date=payload.get("release_date") or None,
            runtime=payload.get("runtime"),
            status=payload.get("status") or "",
            vote_average=payload.get("vote_average"),
            vote_count=payload.get("vote_count"),
            imdb_id=payload.get("imdb_id"),
            director=self._director_from_credits(payload),
            trailer_url=self._trailer_from_videos(payload),
            cast=self._cast_from_credits(payload),
            genres=[
                GenreDTO(
                    provider=self.name,
                    external_id=str(genre["id"]),
                    name=genre.get("name") or "",
                    translations=(
                        {language: {"name": genre["name"]}}
                        if genre.get("name")
                        else {}
                    ),
                )
                for genre in payload.get("genres", [])
            ],
            translations=self._translations_from_payload(payload),
        )

    def list_languages(self) -> list[LanguageOptionDTO]:
        primary_tags = self._get_json("/configuration/primary_translations", {})
        languages = self._get_json("/configuration/languages", {})
        names_by_code = {
            item.get("iso_639_1"): item.get("name") or item.get("english_name")
            for item in languages
            if item.get("iso_639_1")
        }
        options = []
        for tag in primary_tags:
            language_code, separator, region = tag.partition("-")
            name = names_by_code.get(language_code) or language_code
            if separator:
                name = f"{name} ({region})"
            options.append(LanguageOptionDTO(code=tag, name=name))
        return options

    def _translations_from_payload(self, payload: dict) -> dict[str, dict[str, str]]:
        translations = {}
        for item in payload.get("translations", {}).get("translations", []):
            language = item.get("iso_639_1")
            if not language:
                continue
            region = item.get("iso_3166_1")
            code = f"{language}-{region}" if region else language
            data = item.get("data") or {}
            values = {
                field: data.get(field)
                for field in ("title", "overview", "tagline")
                if data.get(field)
            }
            if values:
                translations[code] = values
        return translations

    def _cast_from_credits(self, payload: dict) -> list[CastMemberDTO]:
        cast_members = payload.get("credits", {}).get("cast", [])
        sorted_cast = sorted(cast_members, key=lambda member: member.get("order", 0))
        return [
            CastMemberDTO(
                name=member.get("name") or "",
                character=member.get("character") or "",
                photo_url=build_profile_url(member.get("profile_path")),
            )
            for member in sorted_cast[:10]
        ]

    def _director_from_credits(self, payload: dict) -> str | None:
        crew = payload.get("credits", {}).get("crew", [])
        for member in crew:
            if member.get("job") == "Director":
                return member.get("name") or None
        return None

    def _trailer_from_videos(self, payload: dict) -> str | None:
        videos = payload.get("videos", {}).get("results", [])
        for video in videos:
            if video.get("site") == "YouTube" and video.get("type") == "Trailer":
                return f"https://www.youtube.com/watch?v={video['key']}"
        return None

    def _get_json(self, path: str, params: dict[str, object]) -> dict:
        if not self.api_key:
            raise AuthError("TMDB_API_KEY is required.")

        query = urlencode({"api_key": self.api_key, **params})
        request = Request(
            f"{self.api_base_url}{path}?{query}",
            headers={"Accept": "application/json"},
        )

        try:
            with self.opener(request, timeout=self.timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            self._raise_provider_error(exc)
        except OSError as exc:
            raise ProviderError(f"TMDB request failed: {exc}") from exc

        raise ProviderError("TMDB request failed without a response.")

    def _raise_provider_error(self, exc: HTTPError):
        if exc.code == 404:
            raise NotFound("TMDB item was not found.") from exc
        if exc.code in {401, 403}:
            raise AuthError("TMDB authentication failed.") from exc
        if exc.code == 429:
            retry_after = exc.headers.get("Retry-After")
            if retry_after:
                raise RateLimited(f"TMDB rate limit exceeded. Retry after {retry_after} seconds.") from exc
            raise RateLimited("TMDB rate limit exceeded.") from exc

        raise ProviderError(f"TMDB request failed with HTTP {exc.code}.") from exc

    def _poster_url(self, poster_path: str | None) -> str | None:
        if not poster_path:
            return None

        return (
            f"{self.image_base_url.rstrip('/')}/{self.poster_size}/"
            f"{poster_path.lstrip('/')}"
        )

    def _year_from_date(self, value: str | None) -> int | None:
        if not value:
            return None

        try:
            return int(value[:4])
        except ValueError:
            return None


def build_poster_url(poster_path: str | None) -> str | None:
    if not poster_path:
        return None

    base_url = settings.TMDB_IMAGE_BASE_URL
    return f"{base_url.rstrip('/')}/{TMDBProvider.poster_size}/{poster_path.lstrip('/')}"


def build_profile_url(profile_path: str | None) -> str | None:
    if not profile_path:
        return None

    base_url = settings.TMDB_IMAGE_BASE_URL
    return f"{base_url.rstrip('/')}/w185/{profile_path.lstrip('/')}"


def build_backdrop_url(backdrop_path: str | None) -> str | None:
    if not backdrop_path:
        return None

    base_url = settings.TMDB_IMAGE_BASE_URL
    return f"{base_url.rstrip('/')}/w1280/{backdrop_path.lstrip('/')}"
