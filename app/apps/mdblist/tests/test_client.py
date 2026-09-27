import json
from io import BytesIO
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit

from django.test import SimpleTestCase

from apps.mdblist.client import (
    MdblistAuthenticationError,
    MdblistClient,
    MdblistRateLimited,
    build_authorize_url,
    pkce_pair,
)


class FakeResponse:
    def __init__(self, payload, status=200, headers=None):
        self._body = json.dumps(payload).encode()
        self.status = status
        self.headers = headers or {}

    def getcode(self):
        return self.status

    def read(self):
        return self._body


class RecordingOpener:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def __call__(self, request, timeout=None):
        self.requests.append(request)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def client_with(responses, **kwargs):
    opener = RecordingOpener(responses)
    return MdblistClient("secret", opener=opener, sleeper=lambda _s: None, **kwargs), opener


def query(request) -> dict:
    return {key: values[0] for key, values in parse_qs(urlsplit(request.full_url).query).items()}


class ClientAuthTests(SimpleTestCase):
    def test_api_keys_travel_as_a_query_parameter(self):
        client, opener = client_with([FakeResponse({})], api_key=True)

        client.get_activities()

        request = opener.requests[0]
        self.assertEqual(query(request)["apikey"], "secret")
        self.assertIsNone(request.get_header("Authorization"))

    def test_oauth_tokens_travel_as_a_bearer_header(self):
        client, opener = client_with([FakeResponse({})])

        client.get_activities()

        request = opener.requests[0]
        self.assertEqual(request.get_header("Authorization"), "Bearer secret")
        self.assertNotIn("apikey", query(request))

    def test_authorize_url_carries_pkce(self):
        verifier, challenge = pkce_pair()
        url = build_authorize_url(client_id="cid", redirect_uri="https://a/cb/", state="s", code_challenge=challenge)
        params = {key: values[0] for key, values in parse_qs(urlsplit(url).query).items()}

        self.assertTrue(url.startswith("https://mdblist.com/oauth/authorize/?"))
        self.assertEqual(params["code_challenge_method"], "S256")
        self.assertEqual(params["code_challenge"], challenge)
        self.assertNotEqual(verifier, challenge)

    def test_code_exchange_posts_a_form_with_the_verifier(self):
        client, opener = client_with(
            [FakeResponse({"access_token": "a", "refresh_token": "r", "expires_in": 2592000})],
            client_id="cid",
            client_secret="csecret",
        )

        token = client.exchange_code("code", "https://a/cb/", "verifier")

        request = opener.requests[0]
        body = {key: values[0] for key, values in parse_qs(request.data.decode()).items()}
        self.assertEqual(body["code_verifier"], "verifier")
        self.assertEqual(body["client_secret"], "csecret")
        self.assertEqual(request.get_header("Content-type"), "application/x-www-form-urlencoded")
        self.assertEqual((token.access_token, token.refresh_token, token.expires_in), ("a", "r", 2592000))


class ClientPagingTests(SimpleTestCase):
    def test_snapshots_follow_the_cursor(self):
        client, opener = client_with(
            [
                FakeResponse({"movies": [{"tmdb": 1}], "pagination": {"next_cursor": "c2"}}),
                FakeResponse({"movies": [{"tmdb": 2}], "episodes": [{"tmdb": 3}], "pagination": {}}),
            ]
        )

        payload = client.get_watched()

        self.assertEqual(payload["movies"], [{"tmdb": 1}, {"tmdb": 2}])
        self.assertEqual(payload["episodes"], [{"tmdb": 3}])
        self.assertEqual(query(opener.requests[1])["cursor"], "c2")

    def test_journal_never_sends_since_and_cursor_together(self):
        client, opener = client_with(
            [
                FakeResponse({"journal": [{"a": 1}], "server_time": "T", "pagination": {"next_cursor": "c2"}}),
                FakeResponse({"journal": [{"b": 2}], "pagination": {"has_more": False}}),
            ]
        )

        payload = client.get_journal("2026-01-01T00:00:00Z")

        self.assertEqual(payload["journal"], [{"a": 1}, {"b": 2}])
        self.assertEqual(payload["server_time"], "T")
        first, second = (query(request) for request in opener.requests)
        self.assertIn("since", first)
        self.assertNotIn("cursor", first)
        self.assertEqual(second["cursor"], "c2")
        self.assertNotIn("since", second)

    def test_expired_journal_is_reported(self):
        client, _opener = client_with([FakeResponse({"requires_full_sync": True, "reason": "sync_window_expired"})])

        self.assertTrue(client.get_journal("2020-01-01T00:00:00Z")["requires_full_sync"])


class ClientErrorTests(SimpleTestCase):
    def http_error(self, status, body, headers=None):
        return HTTPError("https://api.mdblist.com/x", status, "error", headers or {}, BytesIO(json.dumps(body).encode()))

    def test_rejected_credentials_raise_an_authentication_error(self):
        client, _opener = client_with([self.http_error(401, {"error": "Invalid API key"})])

        with self.assertRaises(MdblistAuthenticationError):
            client.get_activities()

    def test_daily_quota_waits_until_the_reset(self):
        client, _opener = client_with(
            [self.http_error(429, {"error": "limit"}, {"X-RateLimit-Reset": "10000"})],
            wall_clock=lambda: 7000,
        )

        with self.assertRaises(MdblistRateLimited) as caught:
            client.get_activities()
        self.assertEqual(caught.exception.retry_after, 3000)

    def test_transient_errors_are_retried(self):
        client, opener = client_with([self.http_error(502, {}), FakeResponse({"ok": True})])

        self.assertEqual(client.get_activities(), {"ok": True})
        self.assertEqual(len(opener.requests), 2)

    def test_unknown_titles_come_back_as_none(self):
        client, _opener = client_with([self.http_error(404, {"error": "Not found"})])

        self.assertIsNone(client.get_media("tmdb", "movie", 999))
