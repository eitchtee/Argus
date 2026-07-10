from django.core.cache import cache
from django.test import TestCase

from apps.catalog.providers.base import DetailDTO, EpisodeDTO
from apps.catalog.providers.exceptions import NotFound
from apps.catalog.services import get_movie_detail, get_show_detail, get_show_episodes


class FakeDetailProvider:
    def __init__(self, name):
        self.name = name
        self.detail_calls = []
        self.episode_calls = []

    def fetch_detail(self, external_id):
        self.detail_calls.append(external_id)
        return DetailDTO(provider=self.name, external_id=external_id, title="Fight Club")

    def fetch_episodes(self, external_id):
        self.episode_calls.append(external_id)
        return [EpisodeDTO(season_number=1, episode_number=1, name="Pilot")]


class FailingDetailProvider:
    name = "tmdb"

    def fetch_detail(self, external_id):
        raise NotFound("missing")


class GetMovieDetailTests(TestCase):
    def setUp(self):
        cache.clear()

    def tearDown(self):
        cache.clear()

    def test_cache_miss_calls_provider(self):
        provider = FakeDetailProvider("tmdb")

        detail = get_movie_detail("550", provider_getter=lambda name: provider)

        self.assertEqual(provider.detail_calls, ["550"])
        self.assertEqual(detail.title, "Fight Club")

    def test_cache_hit_avoids_provider_call(self):
        provider = FakeDetailProvider("tmdb")

        get_movie_detail("550", provider_getter=lambda name: provider)
        get_movie_detail("550", provider_getter=lambda name: provider)

        self.assertEqual(provider.detail_calls, ["550"])

    def test_provider_error_is_not_cached(self):
        with self.assertRaises(NotFound):
            get_movie_detail("550", provider_getter=lambda name: FailingDetailProvider())

        provider = FakeDetailProvider("tmdb")
        get_movie_detail("550", provider_getter=lambda name: provider)

        self.assertEqual(provider.detail_calls, ["550"])


class GetShowDetailTests(TestCase):
    def setUp(self):
        cache.clear()

    def tearDown(self):
        cache.clear()

    def test_cache_miss_calls_provider(self):
        provider = FakeDetailProvider("tvdb")

        detail = get_show_detail("123", provider_getter=lambda name: provider)

        self.assertEqual(provider.detail_calls, ["123"])
        self.assertEqual(detail.title, "Fight Club")

    def test_cache_hit_avoids_provider_call(self):
        provider = FakeDetailProvider("tvdb")

        get_show_detail("123", provider_getter=lambda name: provider)
        get_show_detail("123", provider_getter=lambda name: provider)

        self.assertEqual(provider.detail_calls, ["123"])


class GetShowEpisodesTests(TestCase):
    def setUp(self):
        cache.clear()

    def tearDown(self):
        cache.clear()

    def test_cache_miss_calls_provider(self):
        provider = FakeDetailProvider("tvdb")

        episodes = get_show_episodes("123", provider_getter=lambda name: provider)

        self.assertEqual(provider.episode_calls, ["123"])
        self.assertEqual(episodes[0].name, "Pilot")

    def test_cache_hit_avoids_provider_call(self):
        provider = FakeDetailProvider("tvdb")

        get_show_episodes("123", provider_getter=lambda name: provider)
        get_show_episodes("123", provider_getter=lambda name: provider)

        self.assertEqual(provider.episode_calls, ["123"])

    def test_movie_and_show_detail_caches_are_independent(self):
        movie_provider = FakeDetailProvider("tmdb")
        show_provider = FakeDetailProvider("tvdb")

        get_movie_detail("123", provider_getter=lambda name: movie_provider)
        get_show_detail("123", provider_getter=lambda name: show_provider)

        self.assertEqual(movie_provider.detail_calls, ["123"])
        self.assertEqual(show_provider.detail_calls, ["123"])
