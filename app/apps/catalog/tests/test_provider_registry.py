from django.test import SimpleTestCase

from apps.catalog.providers.exceptions import ProviderError
from apps.catalog.providers.registry import get_provider
from apps.catalog.providers.tmdb import TMDBProvider
from apps.catalog.providers.tvdb import TVDBProvider


class ProviderRegistryTests(SimpleTestCase):
    def test_get_provider_returns_tmdb_provider_for_tmdb_name(self):
        provider = get_provider("tmdb")

        self.assertIsInstance(provider, TMDBProvider)
        self.assertEqual(provider.name, "tmdb")

    def test_get_provider_returns_tvdb_provider_for_tvdb_name(self):
        provider = get_provider("tvdb")

        self.assertIsInstance(provider, TVDBProvider)
        self.assertEqual(provider.name, "tvdb")

    def test_get_provider_rejects_unknown_provider_name(self):
        with self.assertRaisesMessage(ProviderError, "Unknown provider"):
            get_provider("unknown")

    def test_get_provider_normalizes_case_and_whitespace(self):
        provider = get_provider(" TMDB ")

        self.assertIsInstance(provider, TMDBProvider)
