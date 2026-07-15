from dataclasses import is_dataclass

from django.test import SimpleTestCase

from apps.catalog.providers.base import (
    BaseProvider,
    DetailDTO,
    EpisodeDTO,
    GenreDTO,
    LanguageOptionDTO,
    SearchResultDTO,
    SeasonDTO,
)
from apps.catalog.providers.exceptions import NotFound, ProviderError


class ProviderDTOTests(SimpleTestCase):
    def test_language_and_translation_dtos_use_provider_native_codes(self):
        language = LanguageOptionDTO(code="pt-BR", name="Português (Brasil)")
        detail = DetailDTO(
            provider="tmdb",
            external_id="550",
            title="Fight Club",
            translations={"pt-BR": {"title": "Clube da Luta"}},
        )
        genre = GenreDTO(
            provider="tmdb",
            external_id="18",
            name="Drama",
            translations={"pt-BR": {"name": "Drama"}},
        )

        self.assertEqual(language.code, "pt-BR")
        self.assertEqual(detail.translations["pt-BR"]["title"], "Clube da Luta")
        self.assertEqual(genre.translations["pt-BR"]["name"], "Drama")

    def test_search_result_dto_contains_normalized_search_fields(self):
        self.assertTrue(is_dataclass(SearchResultDTO))

        result = SearchResultDTO(
            provider="tmdb",
            external_id="550",
            title="Fight Club",
            year=1999,
            poster_url="https://image.tmdb.org/t/p/w342/poster.jpg",
            overview="An insomniac office worker...",
        )

        self.assertEqual(result.provider, "tmdb")
        self.assertEqual(result.external_id, "550")
        self.assertEqual(result.title, "Fight Club")
        self.assertEqual(result.year, 1999)
        self.assertEqual(result.poster_url, "https://image.tmdb.org/t/p/w342/poster.jpg")
        self.assertEqual(result.overview, "An insomniac office worker...")

    def test_detail_dto_contains_shared_movie_and_show_metadata(self):
        self.assertTrue(is_dataclass(DetailDTO))

        detail = DetailDTO(
            provider="tvdb",
            external_id="121361",
            title="Game of Thrones",
            original_title="Game of Thrones",
            overview="Nine noble families fight for control.",
            tagline="Winter is coming.",
            poster_path="/poster.jpg",
            backdrop_path="/backdrop.jpg",
            release_date="2011-04-17",
            runtime=60,
            status="Ended",
            vote_average=8.4,
            vote_count=10000,
            imdb_id="tt0944947",
            network="HBO",
            genres=[GenreDTO(provider="tvdb", external_id="1", name="Drama")],
        )

        self.assertEqual(detail.provider, "tvdb")
        self.assertEqual(detail.external_id, "121361")
        self.assertEqual(detail.title, "Game of Thrones")
        self.assertEqual(detail.network, "HBO")
        self.assertEqual(detail.genres[0].name, "Drama")

    def test_episode_and_season_dtos_normalize_tv_catalog_shape(self):
        self.assertTrue(is_dataclass(SeasonDTO))
        self.assertTrue(is_dataclass(EpisodeDTO))

        season = SeasonDTO(
            season_number=1,
            name="Season 1",
            overview="The first season.",
            poster_path="/season.jpg",
        )
        episode = EpisodeDTO(
            season_number=1,
            episode_number=1,
            absolute_number=1,
            name="Winter Is Coming",
            overview="Episode overview.",
            still_path="/still.jpg",
            air_date="2011-04-17",
            runtime=60,
        )

        self.assertEqual(season.season_number, 1)
        self.assertEqual(episode.episode_number, 1)
        self.assertEqual(episode.air_date, "2011-04-17")


class BaseProviderTests(SimpleTestCase):
    def test_base_provider_requires_search_and_fetch_detail(self):
        with self.assertRaises(TypeError):
            BaseProvider()

    def test_base_provider_fetch_episodes_defaults_to_not_implemented(self):
        class MovieOnlyProvider(BaseProvider):
            name = "movie-only"

            def search(self, query, *, language, page=1):
                return []

            def fetch_detail(self, external_id, *, language):
                raise NotFound("missing")

        provider = MovieOnlyProvider()

        with self.assertRaises(NotImplementedError):
            provider.fetch_episodes("550", language="en-US")

    def test_provider_exceptions_share_base_type(self):
        self.assertTrue(issubclass(NotFound, ProviderError))
