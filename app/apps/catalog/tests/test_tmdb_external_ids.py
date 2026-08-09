import json

from django.test import SimpleTestCase, override_settings

from apps.catalog.providers.tmdb import TMDBProvider


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def read(self):
        return json.dumps(self.payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class FakeOpener:
    def __init__(self, payload):
        self.payload = payload
        self.requests = []

    def __call__(self, request, timeout):
        self.requests.append((request, timeout))
        return FakeResponse(self.payload)


@override_settings(TMDB_API_KEY="test-key")
class TMDBExternalIdTests(SimpleTestCase):
    def test_find_by_imdb_id_returns_movie_or_tv_result_id(self):
        opener = FakeOpener(
            {
                "movie_results": [{"id": 550}],
                "tv_results": [{"id": 1399}],
            }
        )
        provider = TMDBProvider(opener=opener)

        self.assertEqual(provider.find_by_imdb_id("tt0137523", "movie"), "550")
        self.assertEqual(provider.find_by_imdb_id("tt0944947", "tv"), "1399")
        self.assertIn("/find/tt0137523", opener.requests[0][0].full_url)
        self.assertIn("external_source=imdb_id", opener.requests[0][0].full_url)

    def test_find_by_imdb_id_returns_none_when_tmdb_has_no_result(self):
        provider = TMDBProvider(opener=FakeOpener({"movie_results": [], "tv_results": []}))

        self.assertIsNone(provider.find_by_imdb_id("tt0000000", "movie"))
