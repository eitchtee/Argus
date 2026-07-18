from django.core.cache import cache
from django.test import TestCase

from apps.catalog.models import Genre
from apps.catalog.providers.base import SearchResultDTO
from apps.catalog.services import search


class FakeProvider:
    def __init__(self, name="tmdb"):
        self.name = name
        self.calls = []

    def search(self, query, *, language, page=1, media_type="movie"):
        self.calls.append((query, language, page, media_type))
        return [
            SearchResultDTO(
                provider=self.name,
                external_id="550",
                title="Fight Club",
                year=1999,
                poster_url="https://image.tmdb.org/t/p/w342/poster.jpg",
                overview="Overview",
            )
        ]


class SearchServiceTests(TestCase):
    def setUp(self):
        cache.clear()

    def tearDown(self):
        cache.clear()

    def test_search_cache_miss_calls_provider_and_writes_only_cache(self):
        provider = FakeProvider()
        before_count = Genre.objects.count()

        results = search(
            "Fight Club",
            media_type="movie",
            language="en-US",
            page=2,
            provider_getter=lambda name: provider,
        )

        self.assertEqual(provider.calls, [("Fight Club", "en-US", 2, "movie")])
        self.assertEqual(results[0].external_id, "550")
        self.assertEqual(Genre.objects.count(), before_count)

    def test_search_cache_hit_avoids_provider_call(self):
        provider = FakeProvider()

        first_results = search(
            "Fight Club",
            media_type="movie",
            language="en-US",
            page=1,
            provider_getter=lambda name: provider,
        )
        second_results = search(
            "Fight Club",
            media_type="movie",
            language="en-US",
            page=1,
            provider_getter=lambda name: provider,
        )

        self.assertEqual(provider.calls, [("Fight Club", "en-US", 1, "movie")])
        self.assertEqual(second_results, first_results)

    def test_search_cache_is_isolated_by_language(self):
        provider = FakeProvider()

        search("Fight Club", media_type="movie", language="en-US", provider_getter=lambda name: provider)
        search("Fight Club", media_type="movie", language="pt-BR", provider_getter=lambda name: provider)

        self.assertEqual(
            provider.calls,
            [
                ("Fight Club", "en-US", 1, "movie"),
                ("Fight Club", "pt-BR", 1, "movie"),
            ],
        )

    def test_search_maps_tv_type_to_tvdb_provider(self):
        provider = FakeProvider()
        seen_provider_names = []

        def provider_getter(name):
            seen_provider_names.append(name)
            return provider

        search("Game of Thrones", media_type="tv", language="eng", provider_getter=provider_getter)

        self.assertEqual(seen_provider_names, ["tvdb"])

    def test_search_uses_explicit_provider_for_any_media_type(self):
        provider = FakeProvider("tvdb")
        seen_provider_names = []

        def provider_getter(name):
            seen_provider_names.append(name)
            return provider

        results = search(
            "Fight Club",
            media_type="movie",
            provider="tvdb",
            language="eng",
            provider_getter=provider_getter,
        )

        self.assertEqual(seen_provider_names, ["tvdb"])
        self.assertEqual(provider.calls, [("Fight Club", "eng", 1, "movie")])
        self.assertEqual(results[0].provider, "tvdb")

    def test_search_cache_isolated_by_media_type_when_provider_is_same(self):
        provider = FakeProvider("tmdb")

        search(
            "The Office",
            media_type="movie",
            provider="tmdb",
            language="en-US",
            provider_getter=lambda name: provider,
        )
        search(
            "The Office",
            media_type="tv",
            provider="tmdb",
            language="en-US",
            provider_getter=lambda name: provider,
        )

        self.assertEqual(
            provider.calls,
            [
                ("The Office", "en-US", 1, "movie"),
                ("The Office", "en-US", 1, "tv"),
            ],
        )

    def test_search_rejects_unknown_provider(self):
        with self.assertRaisesMessage(ValueError, "Unsupported provider"):
            search(
                "Naruto",
                media_type="tv",
                provider="other",
                language="eng",
                provider_getter=lambda name: FakeProvider(),
            )

    def test_search_rejects_unsupported_media_type(self):
        with self.assertRaisesMessage(ValueError, "Unsupported search type"):
            search(
                "Naruto",
                media_type="anime",
                language="eng",
                provider_getter=lambda name: FakeProvider(),
            )
