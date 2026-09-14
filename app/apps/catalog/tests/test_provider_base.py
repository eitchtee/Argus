
from django.test import SimpleTestCase

from apps.catalog.providers.base import (
    BaseProvider,
    SearchResultDTO,
)
from apps.catalog.providers.exceptions import NotFound


class ProviderDTOTests(SimpleTestCase):
    def test_search_result_dto_collapses_line_breaks_in_text_fields(self):
        result = SearchResultDTO(
            provider="tvdb",
            external_id="123",
            title="Testo\nPart 2",
            year=2024,
            poster_url=None,
            overview="First paragraph.\n\nSecond paragraph.\r\nThird one.",
        )

        self.assertEqual(result.title, "Testo Part 2")
        self.assertEqual(
            result.overview, "First paragraph. Second paragraph. Third one."
        )

class BaseProviderTests(SimpleTestCase):
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
