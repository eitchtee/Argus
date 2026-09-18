import json
from io import BytesIO
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit

from django.test import SimpleTestCase

from apps.simkl.client import (
    SimklAuthenticationError,
    SimklClient,
    SimklError,
    SimklRateLimited,
    build_authorize_url,
)


class FakeResponse:
    def __init__(self, payload, headers=None, status=200, raw=None):
        self.status = status
        self.headers = headers or {}
        self._body = raw if raw is not None else json.dumps(payload).encode("utf-8")

    def read(self):
        return self._body

    def getcode(self):
        return self.status


class FakeOpener:
    def __init__(self, responses=None):
        self.responses = list(responses or [])
        self.requests = []

    def __call__(self, request, timeout):
        self.requests.append(request)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def http_error(status, body=b"{}", headers=None):
    return HTTPError(
        "https://api.simkl.com/x",
        status,
        "error",
        headers or {},
        BytesIO(body),
    )


class SimklClientTests(SimpleTestCase):
    def setUp(self):
        self.opener = FakeOpener([FakeResponse({})])
        self.sleeps = []
        self.client = SimklClient(
            "token",
            client_id="client",
            client_secret="secret",
            app_name="argus-test",
            app_version="9.9",
            opener=self.opener,
            sleeper=self.sleeps.append,
            max_attempts=3,
        )

    def _query(self, index=0):
        return parse_qs(urlsplit(self.opener.requests[index].full_url).query)

    def test_requests_carry_required_params_and_headers(self):
        self.client.get_activities()

        request = self.opener.requests[0]
        query = self._query()
        self.assertEqual(query["client_id"], ["client"])
        self.assertEqual(query["app-name"], ["argus-test"])
        self.assertEqual(query["app-version"], ["9.9"])
        self.assertEqual(request.get_header("Authorization"), "Bearer token")
        self.assertEqual(request.get_header("User-agent"), "argus-test/9.9")
        self.assertTrue(request.full_url.startswith("https://api.simkl.com/sync/activities"))

    def test_all_items_builds_path_and_flags(self):
        self.opener.responses = [FakeResponse({"shows": []})]

        result = self.client.get_all_items(
            "shows",
            date_from="2026-05-08T14:23:11Z",
            extended="full",
            episode_watched_at=True,
            include_all_episodes=True,
        )

        self.assertEqual(result, {"shows": []})
        request = self.opener.requests[0]
        self.assertIn("/sync/all-items/shows?", request.full_url)
        query = self._query()
        self.assertEqual(query["date_from"], ["2026-05-08T14:23:11Z"])
        self.assertEqual(query["extended"], ["full"])
        self.assertEqual(query["episode_watched_at"], ["yes"])
        self.assertEqual(query["include_all_episodes"], ["yes"])

    def test_writes_post_json_bodies(self):
        self.opener.responses = [FakeResponse({"added": {"movies": 1}})]

        self.client.add_to_history({"movies": [{"ids": {"imdb": "tt1"}}]})

        request = self.opener.requests[0]
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(request.get_header("Content-type"), "application/json")
        self.assertEqual(json.loads(request.data), {"movies": [{"ids": {"imdb": "tt1"}}]})

    def test_oauth_exchange_posts_client_credentials_without_bearer(self):
        self.opener.responses = [
            FakeResponse({"access_token": "access", "expires_in": 157680000})
        ]

        token = self.client.exchange_code("code", "https://argus.test/user/simkl/callback/")

        self.assertEqual(token.access_token, "access")
        request = self.opener.requests[0]
        self.assertIsNone(request.get_header("Authorization"))
        body = json.loads(request.data)
        self.assertEqual(body["grant_type"], "authorization_code")
        self.assertEqual(body["client_secret"], "secret")
        self.assertEqual(body["redirect_uri"], "https://argus.test/user/simkl/callback/")

    def test_resolve_reads_simkl_id_from_redirect_location(self):
        self.opener.responses = [
            FakeResponse(
                None,
                headers={"Location": "https://simkl.com/tv/17465/game-of-thrones"},
                status=301,
                raw=b"",
            )
        ]

        target = self.client.resolve(media_type="show", imdb="tt0944947", tmdb="1399")

        self.assertEqual(target.simkl_id, 17465)
        self.assertEqual(target.media_type, "show")
        query = self._query()
        self.assertEqual(query["to"], ["simkl"])
        self.assertEqual(query["type"], ["tv"])
        self.assertEqual(query["tmdb"], ["1399"])

    def test_resolve_handles_redirect_raised_as_http_error(self):
        self.opener.responses = [
            http_error(301, headers={"Location": "https://simkl.com/movies/472214/inception"})
        ]

        target = self.client.resolve(media_type="movie", imdb="tt1375666")

        self.assertEqual(target.section, "movies")
        self.assertEqual(target.simkl_id, 472214)

    def test_resolve_returns_none_for_unknown_ids(self):
        self.opener.responses = [
            FakeResponse(None, headers={"Location": "//simkl.com"}, status=301, raw=b"")
        ]

        self.assertIsNone(self.client.resolve(media_type="movie", imdb="tt0"))
        self.assertIsNone(self.client.resolve(media_type="movie"))
        self.assertEqual(len(self.opener.requests), 1)

    def test_detail_treats_empty_list_as_missing(self):
        self.opener.responses = [FakeResponse([])]

        self.assertIsNone(self.client.get_movie(1))

    def test_transient_errors_are_retried_with_backoff(self):
        self.opener.responses = [http_error(502), FakeResponse({"all": "x"})]

        result = self.client.get_activities()

        self.assertEqual(result, {"all": "x"})
        self.assertEqual(len(self.opener.requests), 2)
        self.assertIn(1, self.sleeps)

    def test_get_rate_limit_is_retried_then_raised(self):
        self.opener.responses = [
            http_error(429, headers={"Retry-After": "17"}),
            http_error(429, headers={"Retry-After": "17"}),
            http_error(429, headers={"Retry-After": "17"}),
        ]

        with self.assertRaises(SimklRateLimited) as caught:
            self.client.get_activities()

        self.assertEqual(caught.exception.retry_after, 17)
        self.assertEqual(len(self.opener.requests), 3)

    def test_post_rate_limit_is_not_retried(self):
        self.opener.responses = [http_error(429, headers={"Retry-After": "5"})]

        with self.assertRaises(SimklRateLimited):
            self.client.add_to_history({"movies": []})

        self.assertEqual(len(self.opener.requests), 1)

    def test_busy_sync_lock_is_retried(self):
        self.opener.responses = [
            http_error(400, body=json.dumps({"error": "rate_limit"}).encode()),
            FakeResponse({"added": {}}),
        ]

        self.client.add_to_history({"movies": []})

        self.assertEqual(len(self.opener.requests), 2)

    def test_unauthorized_raises_authentication_error(self):
        self.opener.responses = [
            http_error(401, body=json.dumps({"error": "user_token_failed"}).encode())
        ]

        with self.assertRaises(SimklAuthenticationError):
            self.client.get_activities()

    def test_bad_request_is_not_retried(self):
        self.opener.responses = [
            http_error(400, body=json.dumps({"error": "empty_field", "message": "Missed"}).encode())
        ]

        with self.assertRaisesMessage(SimklError, "Missed"):
            self.client.add_to_list({"movies": []})
        self.assertEqual(len(self.opener.requests), 1)

    def test_trending_uses_data_host_without_bearer(self):
        self.opener.responses = [FakeResponse([{"title": "x"}])]

        result = self.client.get_trending("movies", "week")

        self.assertEqual(result, [{"title": "x"}])
        request = self.opener.requests[0]
        self.assertTrue(
            request.full_url.startswith("https://data.simkl.in/discover/trending/movies/week_100.json?")
        )
        self.assertIsNone(request.get_header("Authorization"))
        self.assertEqual(self._query()["client_id"], ["client"])

    def test_writes_are_spaced_one_second_apart(self):
        clock = iter([0.0, 0.2, 1.2])
        sleeps = []
        client = SimklClient(
            "token",
            client_id="client",
            opener=FakeOpener([FakeResponse({}), FakeResponse({})]),
            sleeper=sleeps.append,
            clock=lambda: next(clock),
        )

        client.add_to_history({})
        client.add_to_list({})

        self.assertEqual(len(sleeps), 1)
        self.assertAlmostEqual(sleeps[0], 0.8)

    def test_authorize_url_points_at_simkl_com(self):
        url = build_authorize_url(
            client_id="client",
            redirect_uri="https://argus.test/cb/",
            state="abc",
        )

        self.assertTrue(url.startswith("https://simkl.com/oauth/authorize?"))
        query = parse_qs(urlsplit(url).query)
        self.assertEqual(query["response_type"], ["code"])
        self.assertEqual(query["state"], ["abc"])
        self.assertEqual(query["redirect_uri"], ["https://argus.test/cb/"])
