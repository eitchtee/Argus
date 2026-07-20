from datetime import timedelta
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.trakt.client import TokenResponse
from apps.trakt.models import TraktAccount, TraktSyncIntent


class TraktViewTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            "user@example.com",
            password="password",
        )
        self.client.login(username="user@example.com", password="password")

    @override_settings(TRAKT_CLIENT_ID="", TRAKT_CLIENT_SECRET="")
    def test_connect_requires_server_credentials(self):
        response = self.client.get(reverse("trakt_connect"))

        self.assertEqual(response.status_code, 503)

    @override_settings(
        TRAKT_CLIENT_ID="client",
        TRAKT_CLIENT_SECRET="secret",
        TRAKT_REDIRECT_URI="https://argus.test/user/trakt/callback/",
    )
    def test_connect_redirects_to_trakt_with_state(self):
        response = self.client.get(reverse("trakt_connect"))

        self.assertEqual(response.status_code, 302)
        query = parse_qs(urlsplit(response["Location"]).query)
        self.assertEqual(query["client_id"], ["client"])
        self.assertEqual(query["redirect_uri"], ["https://argus.test/user/trakt/callback/"])
        self.assertEqual(query["response_type"], ["code"])
        self.assertTrue(self.client.session.get("trakt_oauth_state"))

    @override_settings(
        TRAKT_CLIENT_ID="client",
        TRAKT_CLIENT_SECRET="secret",
        TRAKT_REDIRECT_URI="https://argus.test/user/trakt/callback/",
    )
    @patch("apps.trakt.views.TraktClient")
    def test_callback_rejects_state_mismatch_without_exchanging_code(self, client_class):
        session = self.client.session
        session["trakt_oauth_state"] = "expected"
        session.save()

        response = self.client.get(
            reverse("trakt_callback"),
            {"code": "code", "state": "wrong"},
        )

        self.assertEqual(response.status_code, 400)
        client_class.assert_not_called()
        self.assertNotIn("trakt_oauth_state", self.client.session)

    @override_settings(
        TRAKT_CLIENT_ID="client",
        TRAKT_CLIENT_SECRET="secret",
        TRAKT_REDIRECT_URI="https://argus.test/user/trakt/callback/",
    )
    @patch("apps.trakt.views.enqueue_account_sync")
    @patch("apps.trakt.views.TraktClient")
    def test_callback_stores_account_and_queues_initial_sync(self, client_class, enqueue):
        session = self.client.session
        session["trakt_oauth_state"] = "expected"
        session.save()
        client_class.return_value.exchange_code.return_value = TokenResponse(
            access_token="access",
            refresh_token="refresh",
            expires_in=3600,
        )
        client_class.return_value.get_user_settings.return_value = {
            "username": "trakt-user"
        }

        response = self.client.get(
            reverse("trakt_callback"),
            {"code": "code", "state": "expected"},
        )

        self.assertEqual(response.status_code, 302)
        account = TraktAccount.objects.get(user=self.user)
        self.assertEqual(account.trakt_username, "trakt-user")
        self.assertEqual(account.access_token, "access")
        self.assertEqual(account.refresh_token, "refresh")
        self.assertFalse(account.initial_sync_complete)
        enqueue.assert_called_once_with(account.id)

    def test_disconnect_is_scoped_to_current_user(self):
        other = get_user_model().objects.create_user("other@example.com")
        account = TraktAccount.objects.create(
            user=self.user,
            access_token="access",
            refresh_token="refresh",
        )
        other_account = TraktAccount.objects.create(
            user=other,
            access_token="other-access",
            refresh_token="other-refresh",
        )
        TraktSyncIntent.objects.create(
            user=self.user,
            kind=TraktSyncIntent.Kind.MOVIE_WATCHLIST,
            identity_key="trakt:1",
        )

        response = self.client.post(reverse("trakt_disconnect"))

        self.assertEqual(response.status_code, 204)
        self.assertFalse(TraktAccount.objects.filter(id=account.id).exists())
        self.assertFalse(TraktSyncIntent.objects.filter(user=self.user).exists())
        self.assertTrue(TraktAccount.objects.filter(id=other_account.id).exists())

    @patch("apps.trakt.views.enqueue_account_sync")
    def test_manual_sync_queues_only_current_user(self, enqueue):
        account = TraktAccount.objects.create(
            user=self.user,
            access_token="access",
            refresh_token="refresh",
        )

        response = self.client.post(reverse("trakt_sync"))

        self.assertEqual(response.status_code, 204)
        enqueue.assert_called_once_with(account.id)

    def test_settings_fragment_exposes_connection_state_without_tokens(self):
        response = self.client.get(
            reverse("user_settings"),
            HTTP_HX_REQUEST="true",
        )
        self.assertContains(response, "Connect Trakt.tv")

        TraktAccount.objects.create(
            user=self.user,
            trakt_username="trakt-user",
            access_token="access-secret",
            refresh_token="refresh-secret",
            last_synced_at=timezone.now() - timedelta(minutes=1),
        )
        response = self.client.get(
            reverse("user_settings"),
            HTTP_HX_REQUEST="true",
        )
        self.assertContains(response, "trakt-user")
        self.assertNotContains(response, "access-secret")
        self.assertNotContains(response, "refresh-secret")
