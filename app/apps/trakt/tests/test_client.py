import json
from io import BytesIO
from unittest.mock import Mock

from django.test import SimpleTestCase

from apps.trakt.client import (
    TraktClient,
    TraktRateLimited,
)


class FakeResponse:
    def __init__(self, payload, headers=None, status=200):
        self.status = status
        self.headers = headers or {}
        self._body = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._body

    def getcode(self):
        return self.status

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class FakeOpener:
    def __init__(self, responses=None):
        self.responses = list(responses or [])
        self.requests = []

    def __call__(self, request, timeout):
        self.requests.append((request, timeout))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class TraktClientTests(SimpleTestCase):
    def setUp(self):
        self.opener = FakeOpener([FakeResponse([])])
        self.client = TraktClient(
            "token",
            client_id="client",
            client_secret="secret",
            opener=self.opener,
        )

    def test_get_sends_required_trakt_headers(self):
        self.client.get_paginated("/sync/watchlist/movies")

        request = self.opener.requests[0][0]
        self.assertEqual(request.get_header("Trakt-api-key"), "client")
        self.assertEqual(request.get_header("Trakt-api-version"), "2")
        self.assertEqual(request.get_header("Authorization"), "Bearer token")
        self.assertEqual(request.get_header("Content-type"), "application/json")

    def test_pagination_follows_x_pagination_page_count(self):
        self.opener.responses = [
            FakeResponse([{"page": 1}], {"X-Pagination-Page-Count": "2"}),
            FakeResponse([{"page": 2}], {}),
        ]

        result = self.client.get_paginated(
            "/sync/watched/movies",
            params={"limit": 100},
        )

        self.assertEqual(result, [{"page": 1}, {"page": 2}])
        self.assertIn("page=2", self.opener.requests[1][0].full_url)

    def test_snapshot_reads_episode_history_incrementally(self):
        history = [
            {
                "watched_at": "2026-07-20T00:00:00Z",
                "show": {"ids": {"trakt": 10}},
                "episode": {"season": 1, "number": 2, "ids": {"trakt": 12}},
            }
        ]
        self.opener.responses = [
            FakeResponse([]),
            FakeResponse([]),
            FakeResponse([]),
            FakeResponse([]),
            FakeResponse([]),
            FakeResponse(history),
        ]

        snapshot = self.client.get_snapshot(
            episode_history_start_at="2026-07-19T23:55:00Z",
        )

        self.assertEqual(snapshot.watched_episodes, history)
        self.assertIn("start_at=2026-07-19T23%3A55%3A00Z", self.opener.requests[-1][0].full_url)

    def test_rate_limit_exposes_retry_after(self):
        from urllib.error import HTTPError

        self.opener.responses = [
            HTTPError(
                "https://api.trakt.tv/sync/watchlist/movies",
                429,
                "Too many requests",
                {"Retry-After": "17"},
                BytesIO(b"{}"),
            )
        ]

        with self.assertRaisesMessage(TraktRateLimited, "17"):
            self.client.get_paginated("/sync/watchlist/movies")

    def test_oauth_exchange_uses_form_encoded_body(self):
        self.opener.responses = [
            FakeResponse(
                {
                    "access_token": "access",
                    "refresh_token": "refresh",
                    "expires_in": 604800,
                }
            )
        ]

        response = self.client.exchange_code("auth-code", "https://argus.test/callback/")

        request = self.opener.requests[0][0]
        self.assertEqual(response.access_token, "access")
        self.assertEqual(request.full_url, "https://api.trakt.tv/oauth/token")
        self.assertEqual(request.get_header("Trakt-api-key"), "client")
        self.assertEqual(request.get_header("Trakt-api-version"), "2")
        self.assertEqual(request.get_header("Content-type"), "application/x-www-form-urlencoded")
        body = request.data.decode("ascii")
        self.assertIn("grant_type=authorization_code", body)
        self.assertIn("code=auth-code", body)

    def test_writes_wait_one_second_apart(self):
        clock = Mock(side_effect=[100.0, 100.25, 100.25])
        sleeper = Mock()
        self.opener.responses = [FakeResponse({}), FakeResponse({})]
        client = TraktClient(
            "token",
            client_id="client",
            client_secret="secret",
            opener=self.opener,
            clock=clock,
            sleeper=sleeper,
        )

        client.post_watchlist({"movies": [{"ids": {"trakt": 1}}]})
        client.post_watchlist({"movies": [{"ids": {"trakt": 2}}]})

        sleeper.assert_called_once()
        self.assertAlmostEqual(sleeper.call_args.args[0], 0.75, places=2)
