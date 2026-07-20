import json
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from django.conf import settings

from apps.catalog.providers.base import (
    BaseProvider,
    CastMemberDTO,
    DetailDTO,
    EpisodeDTO,
    GenreDTO,
    LanguageOptionDTO,
    SearchResultDTO,
    SeasonDTO,
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
        self._tv_summary_cache: dict[tuple[str, str], dict] = {}

    def search(
        self,
        query: str,
        *,
        language: str,
        page: int = 1,
        media_type: str = "movie",
    ) -> list[SearchResultDTO]:
        if media_type not in {"movie", "tv"}:
            raise ValueError(f"Unsupported media type: {media_type}")

        title_field = "title" if media_type == "movie" else "name"
        date_field = "release_date" if media_type == "movie" else "first_air_date"
        payload = self._get_json(
            f"/search/{media_type}",
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
                title=item.get(title_field) or "",
                year=self._year_from_date(item.get(date_field)),
                poster_url=self._poster_url(item.get("poster_path")),
                overview=item.get("overview") or "",
            )
            for item in payload.get("results", [])
        ]

    def fetch_detail(
        self,
        external_id: str,
        *,
        language: str,
        media_type: str = "movie",
    ) -> DetailDTO:
        if media_type not in {"movie", "tv"}:
            raise ValueError(f"Unsupported media type: {media_type}")

        payload = self._get_json(
            f"/{media_type}/{external_id}",
            {
                "language": language,
                "append_to_response": "credits,external_ids,videos,translations",
            },
        )

        is_tv = media_type == "tv"
        external_ids = payload.get("external_ids") or {}
        episode_run_times = payload.get("episode_run_time") or []
        average_runtime = episode_run_times[0] if episode_run_times else None
        networks = payload.get("networks") or []
        next_episode = payload.get("next_episode_to_air") or {}
        last_episode = payload.get("last_episode_to_air") or {}
        translations = self._translations_from_payload(payload)
        localized_values = {
            "title": payload.get("title") or payload.get("name"),
            "overview": payload.get("overview"),
            "tagline": payload.get("tagline"),
        }
        localized_values = {
            field: value for field, value in localized_values.items() if value
        }
        if localized_values:
            requested_translation = translations.setdefault(language, {})
            for field, value in localized_values.items():
                requested_translation.setdefault(field, value)

        return DetailDTO(
            provider=self.name,
            external_id=str(payload["id"]),
            title=payload.get("title") or payload.get("name") or "",
            original_title=(
                payload.get("original_title")
                or payload.get("original_name")
                or ""
            ),
            overview=payload.get("overview") or "",
            tagline=payload.get("tagline") or "",
            poster_path=payload.get("poster_path"),
            backdrop_path=payload.get("backdrop_path"),
            release_date=(
                payload.get("release_date")
                or payload.get("first_air_date")
                or None
            ),
            runtime=payload.get("runtime") or average_runtime,
            status=payload.get("status") or "",
            vote_average=payload.get("vote_average"),
            vote_count=payload.get("vote_count"),
            imdb_id=payload.get("imdb_id") or external_ids.get("imdb_id"),
            tmdb_id=str(payload["id"]),
            tvdb_id=(
                str(external_ids["tvdb_id"])
                if external_ids.get("tvdb_id")
                else None
            ),
            network=networks[0].get("name") if networks else None,
            director=None if is_tv else self._director_from_credits(payload),
            trailer_url=self._trailer_from_videos(payload),
            average_runtime=average_runtime,
            next_air_date=next_episode.get("air_date") or None,
            last_air_date=last_episode.get("air_date") or None,
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
            translations=translations,
        )

    def fetch_seasons(self, external_id: str, *, language: str) -> list[SeasonDTO]:
        payload = self._get_tv_summary(external_id, language)
        return [
            SeasonDTO(
                season_number=season.get("season_number") or 0,
                name="",
                overview=season.get("overview") or "",
                poster_path=season.get("poster_path"),
                translations={
                    language: {
                        field: value
                        for field, value in {"overview": season.get("overview")}.items()
                        if value
                    }
                },
            )
            for season in payload.get("seasons", [])
        ]

    def fetch_episodes(self, external_id: str, *, language: str) -> list[EpisodeDTO]:
        summary = self._get_tv_summary(external_id, language)
        episodes = []
        for season in summary.get("seasons", []):
            season_number = season.get("season_number")
            payload = self._get_json(
                f"/tv/{external_id}/season/{season_number}",
                {"language": language},
            )
            for episode in payload.get("episodes", []):
                episodes.append(
                    EpisodeDTO(
                        season_number=episode.get("season_number") or season_number or 0,
                        episode_number=episode.get("episode_number") or 0,
                        name=episode.get("name") or "",
                        overview=episode.get("overview") or "",
                        still_path=self._image_url(episode.get("still_path"), "w300"),
                        air_date=episode.get("air_date") or None,
                        runtime=episode.get("runtime"),
                        translations={
                            language: {
                                field: value
                                for field, value in {
                                    "name": episode.get("name"),
                                    "overview": episode.get("overview"),
                                }.items()
                                if value
                            }
                        },
                    )
                )
        return episodes

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
                "title": data.get("title") or data.get("name"),
                "overview": data.get("overview"),
                "tagline": data.get("tagline"),
            }
            values = {field: value for field, value in values.items() if value}
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
        return self._image_url(poster_path, self.poster_size)

    def _image_url(self, path: str | None, size: str) -> str | None:
        if not path:
            return None
        if path.startswith(("http://", "https://")):
            return path

        return f"{self.image_base_url.rstrip('/')}/{size}/{path.lstrip('/')}"

    def _get_tv_summary(self, external_id: str, language: str) -> dict:
        cache_key = (external_id, language)
        if cache_key not in self._tv_summary_cache:
            self._tv_summary_cache[cache_key] = self._get_json(
                f"/tv/{external_id}",
                {"language": language},
            )
        return self._tv_summary_cache[cache_key]

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
    if poster_path.startswith(("http://", "https://")):
        return poster_path

    base_url = settings.TMDB_IMAGE_BASE_URL
    return f"{base_url.rstrip('/')}/{TMDBProvider.poster_size}/{poster_path.lstrip('/')}"


def build_profile_url(profile_path: str | None) -> str | None:
    if not profile_path:
        return None
    if profile_path.startswith(("http://", "https://")):
        return profile_path

    base_url = settings.TMDB_IMAGE_BASE_URL
    return f"{base_url.rstrip('/')}/w185/{profile_path.lstrip('/')}"


def build_backdrop_url(backdrop_path: str | None) -> str | None:
    if not backdrop_path:
        return None
    if backdrop_path.startswith(("http://", "https://")):
        return backdrop_path

    base_url = settings.TMDB_IMAGE_BASE_URL
    return f"{base_url.rstrip('/')}/w1280/{backdrop_path.lstrip('/')}"
