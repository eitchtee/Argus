import json
from pathlib import Path
from urllib.error import HTTPError

from django.core.cache import cache
from django.test import SimpleTestCase, override_settings

from apps.catalog.providers.exceptions import AuthError, NotFound, RateLimited
from apps.catalog.providers.tvdb import TVDBProvider


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


class SequenceOpener:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def __call__(self, request, timeout):
        self.requests.append((request, timeout))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return FakeResponse(response)


def load_fixture(name):
    return json.loads((FIXTURE_DIR / name).read_text())


def http_error(url, status_code, headers=None):
    return HTTPError(url, status_code, "Provider error", headers or {}, None)


@override_settings(TVDB_API_KEY="test-tvdb-key")
class TVDBProviderTests(SimpleTestCase):
    def setUp(self):
        cache.clear()

    def tearDown(self):
        cache.clear()

    def test_search_logs_in_caches_token_and_normalizes_results(self):
        opener = SequenceOpener(
            [
                load_fixture("tvdb_login.json"),
                load_fixture("tvdb_search.json"),
                {
                    "status": "success",
                    "data": {
                        "name": "Game of Thrones",
                        "overview": "Nine noble families fight for control.",
                    },
                },
            ]
        )
        provider = TVDBProvider(opener=opener)

        results = provider.search("game of thrones", language="por", page=1)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].provider, "tvdb")
        self.assertEqual(results[0].external_id, "121361")
        self.assertEqual(results[0].title, "Game of Thrones")
        self.assertEqual(results[0].year, 2011)
        self.assertEqual(results[0].poster_url, "https://artworks.thetvdb.com/poster.jpg")
        self.assertEqual(cache.get("catalog:tvdb:token"), "cached-tvdb-token")

        login_request = opener.requests[0][0]
        search_request = opener.requests[1][0]
        self.assertIn("/login", login_request.full_url)
        self.assertEqual(json.loads(login_request.data.decode("utf-8")), {"apikey": "test-tvdb-key"})
        self.assertIn("/search", search_request.full_url)
        self.assertIn("query=game+of+thrones", search_request.full_url)
        self.assertNotIn("language=", search_request.full_url)
        self.assertEqual(search_request.headers["Authorization"], "Bearer cached-tvdb-token")

    def test_search_normalizes_movie_results(self):
        cache.set("catalog:tvdb:token", "existing-token")
        opener = SequenceOpener(
            [
                {
                    "status": "success",
                    "data": [
                        {
                            "tvdb_id": "42",
                            "name": "A Movie",
                            "year": "2020",
                            "image_url": "https://artworks.thetvdb.com/movie.jpg",
                            "overview": "A movie.",
                        }
                    ],
                },
                {
                    "status": "success",
                    "data": {
                        "name": "A Movie",
                        "overview": "A movie.",
                    },
                },
            ]
        )
        provider = TVDBProvider(opener=opener)

        results = provider.search("A Movie", language="eng", media_type="movie")

        self.assertEqual(results[0].provider, "tvdb")
        self.assertEqual(results[0].external_id, "42")
        self.assertEqual(results[0].year, 2020)
        self.assertIn("type=movie", opener.requests[0][0].full_url)

    def test_search_uses_requested_translation(self):
        cache.set("catalog:tvdb:token", "existing-token")
        opener = SequenceOpener(
            [
                {
                    "status": "success",
                    "data": [
                        {
                            "tvdb_id": "401003",
                            "name": "FROM",
                            "year": "2022",
                            "image_url": "https://artworks.thetvdb.com/from.jpg",
                            "overview": "An English overview.",
                        }
                    ],
                },
                {
                    "status": "success",
                    "data": {
                        "name": "Origem",
                        "overview": "Uma sinopse em português.",
                    },
                },
            ]
        )
        provider = TVDBProvider(opener=opener)

        results = provider.search("Origem", language="por")

        self.assertEqual(results[0].title, "Origem")
        self.assertEqual(results[0].overview, "Uma sinopse em português.")
        self.assertIn("/series/401003/translations/por", opener.requests[1][0].full_url)

    def test_search_falls_back_to_english_translation(self):
        cache.set("catalog:tvdb:token", "existing-token")
        opener = SequenceOpener(
            [
                {
                    "status": "success",
                    "data": [
                        {
                            "tvdb_id": "394756",
                            "name": "Lupin",
                            "year": "2021",
                            "overview": "Un cambrioleur français.",
                        }
                    ],
                },
                http_error(
                    "https://api4.thetvdb.com/v4/series/394756/translations/por",
                    404,
                ),
                {
                    "status": "success",
                    "data": {
                        "name": "Lupin",
                        "overview": "A gentleman thief seeks revenge.",
                    },
                },
            ]
        )
        provider = TVDBProvider(opener=opener)

        results = provider.search("Lupin", language="por")

        self.assertEqual(results[0].title, "Lupin")
        self.assertEqual(results[0].overview, "A gentleman thief seeks revenge.")
        self.assertIn("/series/394756/translations/por", opener.requests[1][0].full_url)
        self.assertIn("/series/394756/translations/eng", opener.requests[2][0].full_url)

    def test_fetch_detail_normalizes_movie_extended_response(self):
        cache.set("catalog:tvdb:token", "existing-token")
        opener = SequenceOpener(
            [
                {
                    "status": "success",
                    "data": {
                        "id": 42,
                        "name": "A Movie",
                        "overview": "A movie.",
                        "releaseDate": "2020-01-02",
                        "image": "https://artworks.thetvdb.com/movie.jpg",
                        "genres": [{"id": 1, "name": "Drama"}],
                        "remoteIds": [
                            {"sourceName": "IMDB", "id": "tt1234567"},
                            {"sourceName": "TheMovieDB.com", "id": "550"},
                        ],
                    },
                }
            ]
        )
        provider = TVDBProvider(opener=opener)

        detail = provider.fetch_detail("42", language="eng", media_type="movie")

        self.assertEqual(detail.provider, "tvdb")
        self.assertEqual(detail.external_id, "42")
        self.assertEqual(detail.title, "A Movie")
        self.assertEqual(detail.release_date, "2020-01-02")
        self.assertEqual(detail.imdb_id, "tt1234567")
        self.assertEqual(detail.tvdb_id, "42")
        self.assertEqual(detail.tmdb_id, "550")
        self.assertEqual(detail.genres[0].name, "Drama")
        self.assertIn("/movies/42/extended", opener.requests[0][0].full_url)

    def test_cached_token_avoids_login_call(self):
        cache.set("catalog:tvdb:token", "existing-token")
        opener = SequenceOpener(
            [
                load_fixture("tvdb_search.json"),
                {
                    "status": "success",
                    "data": {
                        "name": "Game of Thrones",
                        "overview": "Nine noble families fight for control.",
                    },
                },
            ]
        )
        provider = TVDBProvider(opener=opener)

        provider.search("game of thrones", language="eng")

        self.assertEqual(len(opener.requests), 2)
        self.assertIn("/search", opener.requests[0][0].full_url)
        self.assertEqual(opener.requests[0][0].headers["Authorization"], "Bearer existing-token")

    def test_fetch_detail_normalizes_series_extended_response(self):
        cache.set("catalog:tvdb:token", "existing-token")
        opener = SequenceOpener([load_fixture("tvdb_series_extended.json")])
        provider = TVDBProvider(opener=opener)

        detail = provider.fetch_detail("121361", language="eng")

        self.assertEqual(detail.provider, "tvdb")
        self.assertEqual(detail.external_id, "121361")
        self.assertEqual(detail.tvdb_id, "121361")
        self.assertEqual(detail.title, "Game of Thrones")
        self.assertEqual(detail.overview, "Nine noble families fight for control.")
        self.assertEqual(detail.poster_path, "https://artworks.thetvdb.com/poster.jpg")
        self.assertEqual(detail.release_date, "2011-04-17")
        self.assertEqual(detail.status, "Ended")
        self.assertEqual(detail.network, "HBO")
        self.assertEqual([genre.name for genre in detail.genres], ["Drama", "Fantasy"])
        self.assertEqual(detail.backdrop_path, "https://artworks.thetvdb.com/fanart-high.jpg")
        self.assertEqual(detail.imdb_id, "tt0944947")
        self.assertEqual(detail.tmdb_id, "1399")
        self.assertEqual(detail.trailer_url, "https://www.youtube.com/watch?v=abc123")
        self.assertEqual(detail.average_runtime, 57)
        self.assertIsNone(detail.next_air_date)
        self.assertEqual(detail.last_air_date, "2019-05-19")
        self.assertEqual(detail.airs_time, "21:00")
        self.assertEqual(len(detail.cast), 1)
        self.assertEqual(detail.cast[0].name, "Emilia Clarke")
        self.assertEqual(detail.cast[0].character, "Daenerys Targaryen")
        self.assertEqual(detail.cast[0].photo_url, "https://artworks.thetvdb.com/clarke.jpg")
        self.assertIn("/series/121361/extended", opener.requests[0][0].full_url)
        self.assertEqual(
            detail.translations["eng"],
            {
                "title": "Game of Thrones",
                "overview": "Nine noble families fight for control.",
            },
        )

    def test_fetch_detail_normalizes_all_series_translation_metadata(self):
        cache.set("catalog:tvdb:token", "existing-token")
        payload = load_fixture("tvdb_series_extended.json")
        payload["data"]["translations"] = {
            "nameTranslations": [
                {"language": "por", "name": "A Guerra dos Tronos"},
                {"language": "spa", "name": "Juego de Tronos"},
            ],
            "overviewTranslations": [
                {"language": "por", "overview": "Visão geral."},
            ],
        }
        opener = SequenceOpener([payload])
        provider = TVDBProvider(opener=opener)

        detail = provider.fetch_detail("121361", language="eng")

        self.assertEqual(
            detail.translations,
            {
                "eng": {
                    "title": "Game of Thrones",
                    "overview": "Nine noble families fight for control.",
                },
                "por": {"title": "A Guerra dos Tronos", "overview": "Visão geral."},
                "spa": {"title": "Juego de Tronos"},
            },
        )
        self.assertIn("meta=translations", opener.requests[0][0].full_url)

    def test_fetch_detail_merges_requested_series_translation(self):
        cache.set("catalog:tvdb:token", "existing-token")
        opener = SequenceOpener(
            [
                load_fixture("tvdb_series_extended.json"),
                load_fixture("tvdb_series_translation_por.json"),
            ]
        )
        provider = TVDBProvider(opener=opener)

        detail = provider.fetch_detail("121361", language="por")

        self.assertEqual(
            detail.translations["por"],
            {"title": "A Guerra dos Tronos", "overview": "Visão geral."},
        )
        self.assertIn("/series/121361/translations/por", opener.requests[1][0].full_url)

    def test_fetch_episodes_normalizes_default_episode_response(self):
        cache.set("catalog:tvdb:token", "existing-token")
        opener = SequenceOpener([load_fixture("tvdb_episodes_default.json")])
        provider = TVDBProvider(opener=opener)

        episodes = provider.fetch_episodes("121361", language="eng")

        self.assertEqual(len(episodes), 2)
        self.assertEqual(episodes[0].season_number, 1)
        self.assertEqual(episodes[0].episode_number, 1)
        self.assertEqual(episodes[0].absolute_number, 1)
        self.assertEqual(episodes[0].name, "Winter Is Coming")
        self.assertEqual(episodes[0].still_path, "https://artworks.thetvdb.com/still.jpg")
        self.assertEqual(episodes[0].air_date, "2011-04-17")
        self.assertEqual(episodes[0].runtime, 60)
        self.assertEqual(episodes[0].finale_type, "series")
        self.assertIsNone(episodes[1].finale_type)
        self.assertEqual(episodes[1].season_number, 0)
        self.assertIn("/series/121361/episodes/default", opener.requests[0][0].full_url)
        self.assertEqual(
            episodes[0].translations["eng"],
            {"name": "Winter Is Coming", "overview": "Episode overview."},
        )

    def test_fetch_episodes_uses_requested_language_batch(self):
        cache.set("catalog:tvdb:token", "existing-token")
        opener = SequenceOpener([load_fixture("tvdb_episodes_por.json")])
        provider = TVDBProvider(opener=opener)

        episodes = provider.fetch_episodes("121361", language="por")

        self.assertEqual(episodes[0].name, "O Inverno Está Chegando")
        self.assertEqual(
            episodes[0].translations["por"],
            {"name": "O Inverno Está Chegando", "overview": "Resumo do episódio."},
        )
        self.assertIn("/series/121361/episodes/default/por", opener.requests[0][0].full_url)

    def test_fetch_episodes_expands_relative_still_urls(self):
        cache.set("catalog:tvdb:token", "existing-token")
        payload = load_fixture("tvdb_episodes_default.json")
        payload["data"]["episodes"][0]["image"] = "/banners/episodes/still.jpg"
        provider = TVDBProvider(opener=SequenceOpener([payload]))

        episodes = provider.fetch_episodes("121361", language="eng")

        self.assertEqual(
            episodes[0].still_path,
            "https://artworks.thetvdb.com/banners/episodes/still.jpg",
        )

    def test_fetch_seasons_merges_requested_translation_by_season_number(self):
        cache.set("catalog:tvdb:token", "existing-token")
        opener = SequenceOpener(
            [
                load_fixture("tvdb_series_extended.json"),
                load_fixture("tvdb_season_translation_por.json"),
            ]
        )
        provider = TVDBProvider(opener=opener)

        seasons = provider.fetch_seasons("121361", language="por")

        self.assertEqual(seasons[0].season_number, 1)
        self.assertEqual(seasons[0].name, "")
        self.assertEqual(
            seasons[0].translations["por"],
            {"overview": "A primeira temporada."},
        )
        self.assertIn("/seasons/501/translations/por", opener.requests[1][0].full_url)

    def test_fetch_seasons_reuses_cached_series_extended_response(self):
        cache.set("catalog:tvdb:token", "existing-token")
        series_payload = load_fixture("tvdb_series_extended.json")
        opener = SequenceOpener(
            [
                series_payload,
                load_fixture("tvdb_season_translation_por.json"),
            ]
        )
        provider = TVDBProvider(opener=opener)

        provider.fetch_detail("121361", language="eng")
        seasons = provider.fetch_seasons("121361", language="por")

        self.assertEqual(len(opener.requests), 2)
        self.assertNotIn("name", seasons[0].translations.get("por", {}))

    def test_list_languages_returns_provider_native_codes(self):
        cache.set("catalog:tvdb:token", "existing-token")
        provider = TVDBProvider(opener=SequenceOpener([load_fixture("tvdb_languages.json")]))

        languages = provider.list_languages()

        self.assertEqual(
            [(language.code, language.name) for language in languages],
            [("eng", "English"), ("por", "Português")],
        )

    def test_401_refreshes_token_once_and_retries_request(self):
        cache.set("catalog:tvdb:token", "expired-token")
        opener = SequenceOpener(
            [
                http_error("https://api4.thetvdb.com/v4/search", 401),
                load_fixture("tvdb_login.json"),
                load_fixture("tvdb_search.json"),
                {
                    "status": "success",
                    "data": {
                        "name": "Game of Thrones",
                        "overview": "Nine noble families fight for control.",
                    },
                },
            ]
        )
        provider = TVDBProvider(opener=opener)

        results = provider.search("game of thrones", language="eng")

        self.assertEqual(results[0].external_id, "121361")
        self.assertEqual(len(opener.requests), 4)
        self.assertEqual(opener.requests[0][0].headers["Authorization"], "Bearer expired-token")
        self.assertIn("/login", opener.requests[1][0].full_url)
        self.assertEqual(opener.requests[2][0].headers["Authorization"], "Bearer cached-tvdb-token")

    def test_missing_api_key_raises_auth_error(self):
        provider = TVDBProvider(api_key="", opener=SequenceOpener([]))

        with self.assertRaises(AuthError):
            provider.search("game of thrones", language="eng")

    def test_http_404_maps_to_not_found(self):
        cache.set("catalog:tvdb:token", "existing-token")
        provider = TVDBProvider(opener=SequenceOpener([http_error("url", 404)]))

        with self.assertRaises(NotFound):
            provider.fetch_detail("missing", language="eng")

    def test_http_429_maps_to_rate_limited(self):
        cache.set("catalog:tvdb:token", "existing-token")
        provider = TVDBProvider(
            opener=SequenceOpener([http_error("url", 429, {"Retry-After": "20"})])
        )

        with self.assertRaisesMessage(RateLimited, "Retry after 20 seconds"):
            provider.search("game of thrones", language="eng")


class TVDBAirsTimeTests(SimpleTestCase):
    def setUp(self):
        cache.clear()
        cache.set("catalog:tvdb:token", "existing-token")

    def tearDown(self):
        cache.clear()

    def _fetch_with_data_overrides(self, **overrides):
        payload = load_fixture("tvdb_series_extended.json")
        payload["data"].update(overrides)
        opener = SequenceOpener([payload])
        provider = TVDBProvider(opener=opener)
        return provider.fetch_detail("121361", language="eng")

    def test_preserves_raw_airing_time(self):
        detail = self._fetch_with_data_overrides(airsTime="21:00")

        self.assertEqual(detail.airs_time, "21:00")

    def test_missing_airing_time_returns_none(self):
        detail = self._fetch_with_data_overrides(airsTime=None)

        self.assertIsNone(detail.airs_time)

    def test_airing_days_do_not_change_the_raw_time(self):
        detail = self._fetch_with_data_overrides(
            airsDays={day: False for day in [
                "sunday", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday"
            ]},
            airsTime="00:00",
        )

        self.assertEqual(detail.airs_time, "00:00")

    def test_no_trailers_returns_none(self):
        detail = self._fetch_with_data_overrides(trailers=[])

        self.assertIsNone(detail.trailer_url)

    def test_no_imdb_remote_id_returns_none(self):
        detail = self._fetch_with_data_overrides(
            remoteIds=[{"id": "1399", "type": 12, "sourceName": "TheMovieDB.com"}]
        )

        self.assertIsNone(detail.imdb_id)

    def test_no_tmdb_remote_id_returns_none(self):
        detail = self._fetch_with_data_overrides(
            remoteIds=[{"id": "tt0944947", "type": 2, "sourceName": "IMDB"}]
        )

        self.assertIsNone(detail.tmdb_id)

    def test_cast_excludes_non_actor_people_types(self):
        detail = self._fetch_with_data_overrides()

        names = [member.name for member in detail.cast]
        self.assertNotIn("David Benioff", names)
