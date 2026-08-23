import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

_MULTILINE_WHITESPACE = re.compile(r"\s+")


@dataclass(frozen=True)
class SearchResultDTO:
    provider: str
    external_id: str
    title: str
    year: int | None
    poster_url: str | None
    overview: str

    def __post_init__(self) -> None:
        # Providers (notably TVDB) return overviews with raw line breaks, which
        # break -webkit-line-clamp truncation in search result cards.
        object.__setattr__(
            self,
            "title",
            _MULTILINE_WHITESPACE.sub(" ", self.title or "").strip(),
        )
        object.__setattr__(
            self,
            "overview",
            _MULTILINE_WHITESPACE.sub(" ", self.overview or "").strip(),
        )


@dataclass(frozen=True)
class LanguageOptionDTO:
    code: str
    name: str


@dataclass(frozen=True)
class GenreDTO:
    provider: str
    external_id: str
    name: str
    translations: dict[str, dict[str, str]] = field(default_factory=dict)


@dataclass(frozen=True)
class CastMemberDTO:
    name: str
    character: str
    photo_url: str | None = None


@dataclass(frozen=True)
class ArtworkDTO:
    kind: str
    image_url: str
    language: str | None = None
    width: int | None = None
    height: int | None = None
    score: float | None = None
    remote_id: str | None = None
    is_default: bool = False


@dataclass(frozen=True)
class DetailDTO:
    provider: str
    external_id: str
    title: str
    original_title: str = ""
    original_language: str = ""
    overview: str = ""
    tagline: str = ""
    poster_path: str | None = None
    backdrop_path: str | None = None
    artworks: list[ArtworkDTO] | None = None
    release_date: str | None = None
    runtime: int | None = None
    status: str = ""
    vote_average: float | None = None
    vote_count: int | None = None
    imdb_id: str | None = None
    tmdb_id: str | None = None
    tvdb_id: str | None = None
    trakt_id: str | None = None
    network: str | None = None
    genres: list[GenreDTO] = field(default_factory=list)
    cast: list[CastMemberDTO] = field(default_factory=list)
    director: str | None = None
    trailer_url: str | None = None
    average_runtime: int | None = None
    next_air_date: str | None = None
    last_air_date: str | None = None
    airs_time: str | None = None
    airs_timezone: str | None = None
    translations: dict[str, dict[str, str]] = field(default_factory=dict)


@dataclass(frozen=True)
class SeasonDTO:
    season_number: int
    name: str = ""
    overview: str = ""
    poster_path: str | None = None
    translations: dict[str, dict[str, str]] = field(default_factory=dict)


@dataclass(frozen=True)
class EpisodeDTO:
    season_number: int
    episode_number: int
    absolute_number: int | None = None
    name: str = ""
    overview: str = ""
    still_path: str | None = None
    air_date: str | None = None
    runtime: int | None = None
    finale_type: str | None = None
    translations: dict[str, dict[str, str]] = field(default_factory=dict)


class BaseProvider(ABC):
    name: str
    # Whether genre names differ per language. Providers that publish a single
    # set of names are only asked for the catalog once instead of once per
    # selectable language.
    translates_genres: bool = False

    @abstractmethod
    def search(
        self,
        query: str,
        *,
        language: str,
        page: int = 1,
        media_type: str = "movie",
    ) -> list[SearchResultDTO]:
        raise NotImplementedError

    @abstractmethod
    def fetch_detail(
        self,
        external_id: str,
        *,
        language: str,
        media_type: str = "movie",
    ) -> DetailDTO:
        raise NotImplementedError

    def fetch_episodes(self, external_id: str, *, language: str) -> list[EpisodeDTO]:
        raise NotImplementedError(
            f"{self.__class__.__name__} does not support episode fetching."
        )

    def fetch_seasons(self, external_id: str, *, language: str) -> list[SeasonDTO]:
        raise NotImplementedError(
            f"{self.__class__.__name__} does not support season fetching."
        )

    def list_languages(self) -> list[LanguageOptionDTO]:
        raise NotImplementedError(
            f"{self.__class__.__name__} does not support language listing."
        )

    def fetch_genres(self, *, media_type: str, language: str) -> list[GenreDTO]:
        raise NotImplementedError(
            f"{self.__class__.__name__} does not support genre listing."
        )
