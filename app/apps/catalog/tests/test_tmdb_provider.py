import json
from pathlib import Path
from urllib.error import HTTPError

from django.test import SimpleTestCase, override_settings

from apps.catalog.providers.exceptions import AuthError, NotFound, RateLimited
from apps.catalog.providers.tmdb import TMDBProvider, build_backdrop_url, build_poster_url, build_profile_url


FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures"


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


class FakeOpener:
    def __init__(self, payload):
        self.payload = payload
        self.requests = []

    def __call__(self, request, timeout):
        self.requests.append((request, timeout))
        return FakeResponse(self.payload)


class SequenceOpener:
    def __init__(self, payloads):
        self.payloads = list(payloads)
        self.requests = []

    def __call__(self, request, timeout):
        self.requests.append((request, timeout))
        return FakeResponse(self.payloads.pop(0))


def load_fixture(name):
    return json.loads((FIXTURE_DIR / name).read_text())


@override_settings(
    TMDB_API_KEY="test-key",
    TMDB_IMAGE_BASE_URL="https://image.tmdb.org/t/p/",
)
class TMDBProviderTests(SimpleTestCase):
    def test_search_normalizes_movie_results(self):
        opener = FakeOpener(load_fixture("tmdb_search_movie.json"))
        provider = TMDBProvider(opener=opener)

        results = provider.search("fight club", language="pt-BR", page=2)

        self.assertEqual(len(results), 2)
        self.assertEqual(results[0].provider, "tmdb")
        self.assertEqual(results[0].external_id, "550")
        self.assertEqual(results[0].title, "Fight Club")
        self.assertEqual(results[0].year, 1999)
        self.assertEqual(
            results[0].poster_url,
            "https://image.tmdb.org/t/p/w342/pB8BM7pdSp6B6Ih7QZ4DrQ3PmJK.jpg",
        )
        self.assertIsNone(results[1].year)
        self.assertIsNone(results[1].poster_url)

        requested_url = opener.requests[0][0].full_url
        self.assertIn("/search/movie", requested_url)
        self.assertIn("api_key=test-key", requested_url)
        self.assertIn("query=fight+club", requested_url)
        self.assertIn("page=2", requested_url)
        self.assertIn("language=pt-BR", requested_url)

    def test_fetch_detail_normalizes_movie_detail(self):
        opener = FakeOpener(load_fixture("tmdb_movie_detail.json"))
        provider = TMDBProvider(opener=opener)

        detail = provider.fetch_detail("550", language="en-US")

        self.assertEqual(detail.provider, "tmdb")
        self.assertEqual(detail.external_id, "550")
        self.assertEqual(detail.imdb_id, "tt0137523")
        self.assertEqual(detail.title, "Fight Club")
        self.assertEqual(detail.poster_path, "/pB8BM7pdSp6B6Ih7QZ4DrQ3PmJK.jpg")
        self.assertEqual(detail.release_date, "1999-10-15")
        self.assertEqual(detail.runtime, 139)
        self.assertEqual(detail.status, "Released")
        self.assertEqual(detail.vote_average, 8.4)
        self.assertEqual(detail.vote_count, 29400)
        self.assertEqual([genre.name for genre in detail.genres], ["Drama", "Thriller"])
        self.assertEqual(detail.director, "David Fincher")
        self.assertEqual(detail.trailer_url, "https://www.youtube.com/watch?v=SUXWAEX2jlg")
        self.assertEqual(len(detail.cast), 2)
        self.assertEqual(detail.cast[0].name, "Edward Norton")
        self.assertEqual(detail.cast[0].character, "The Narrator")
        self.assertEqual(
            detail.cast[0].photo_url,
            "https://image.tmdb.org/t/p/w185/norton.jpg",
        )
        self.assertEqual(detail.cast[1].name, "Brad Pitt")

        requested_url = opener.requests[0][0].full_url
        self.assertIn("/movie/550", requested_url)
        self.assertIn(
            "append_to_response=credits%2Cexternal_ids%2Cvideos%2Ctranslations",
            requested_url,
        )
        self.assertIn("language=en-US", requested_url)

    def test_fetch_detail_normalizes_all_movie_translations(self):
        payload = load_fixture("tmdb_movie_detail.json")
        payload["translations"] = load_fixture("tmdb_movie_translations.json")
        provider = TMDBProvider(opener=FakeOpener(payload))

        detail = provider.fetch_detail("550", language="pt-BR")

        self.assertEqual(
            detail.translations,
            {
                "en-US": {"title": "Fight Club", "overview": "English overview"},
                "pt-BR": {
                    "title": "Clube da Luta",
                    "overview": "Visão geral em português.",
                    "tagline": "Caos. Confusão. Sabão.",
                },
            },
        )
        self.assertEqual(
            detail.genres[0].translations,
            {"pt-BR": {"name": "Drama"}},
        )

    def test_list_languages_uses_primary_tags_and_readable_names(self):
        opener = SequenceOpener(
            [
                load_fixture("tmdb_primary_translations.json"),
                load_fixture("tmdb_languages.json"),
            ]
        )
        provider = TMDBProvider(opener=opener)

        languages = provider.list_languages()

        self.assertEqual(
            [(language.code, language.name) for language in languages],
            [("en-US", "English (US)"), ("pt-BR", "Português (BR)")],
        )

    def test_missing_api_key_raises_auth_error(self):
        provider = TMDBProvider(api_key="", opener=FakeOpener({}))

        with self.assertRaises(AuthError):
            provider.search("fight club", language="en-US")

    def test_http_404_maps_to_not_found(self):
        provider = TMDBProvider(opener=self.raise_http_error(404))

        with self.assertRaises(NotFound):
            provider.fetch_detail("missing", language="en-US")

    def test_http_401_maps_to_auth_error(self):
        provider = TMDBProvider(opener=self.raise_http_error(401))

        with self.assertRaises(AuthError):
            provider.search("fight club", language="en-US")

    def test_http_429_maps_to_rate_limited(self):
        provider = TMDBProvider(opener=self.raise_http_error(429, {"Retry-After": "12"}))

        with self.assertRaisesMessage(RateLimited, "Retry after 12 seconds"):
            provider.search("fight club", language="en-US")

    def raise_http_error(self, status_code, headers=None):
        def opener(request, timeout):
            raise HTTPError(
                request.full_url,
                status_code,
                "Provider error",
                headers or {},
                None,
            )

        return opener

    def test_fetch_detail_caps_cast_at_ten_and_sorts_by_billing_order(self):
        payload = json.loads((FIXTURE_DIR / "tmdb_movie_detail.json").read_text())
        payload["credits"]["cast"] = [
            {"name": f"Actor {i}", "character": f"Character {i}", "profile_path": None, "order": i}
            for i in reversed(range(12))
        ]
        opener = FakeOpener(payload)
        provider = TMDBProvider(opener=opener)

        detail = provider.fetch_detail("550", language="en-US")

        self.assertEqual(len(detail.cast), 10)
        self.assertEqual(detail.cast[0].name, "Actor 0")
        self.assertEqual(detail.cast[-1].name, "Actor 9")

    def test_fetch_detail_handles_missing_director_and_trailer(self):
        payload = json.loads((FIXTURE_DIR / "tmdb_movie_detail.json").read_text())
        payload["credits"]["crew"] = [{"name": "Jim Uhls", "job": "Screenplay"}]
        payload["videos"] = {"results": []}
        opener = FakeOpener(payload)
        provider = TMDBProvider(opener=opener)

        detail = provider.fetch_detail("550", language="en-US")

        self.assertIsNone(detail.director)
        self.assertIsNone(detail.trailer_url)


class BuildBackdropUrlTests(SimpleTestCase):
    @override_settings(TMDB_IMAGE_BASE_URL="https://image.tmdb.org/t/p/")
    def test_builds_full_url_from_relative_path(self):
        self.assertEqual(
            build_backdrop_url("/abc.jpg"),
            "https://image.tmdb.org/t/p/w1280/abc.jpg",
        )

    def test_returns_none_for_missing_path(self):
        self.assertIsNone(build_backdrop_url(None))


class BuildProfileUrlTests(SimpleTestCase):
    @override_settings(TMDB_IMAGE_BASE_URL="https://image.tmdb.org/t/p/")
    def test_builds_full_url_from_relative_path(self):
        self.assertEqual(
            build_profile_url("/norton.jpg"),
            "https://image.tmdb.org/t/p/w185/norton.jpg",
        )

    def test_returns_none_for_missing_path(self):
        self.assertIsNone(build_profile_url(None))


class BuildPosterUrlTests(SimpleTestCase):
    @override_settings(TMDB_IMAGE_BASE_URL="https://image.tmdb.org/t/p/")
    def test_builds_full_url_from_relative_path(self):
        self.assertEqual(
            build_poster_url("/abc.jpg"),
            "https://image.tmdb.org/t/p/w342/abc.jpg",
        )

    def test_returns_none_for_missing_path(self):
        self.assertIsNone(build_poster_url(None))
