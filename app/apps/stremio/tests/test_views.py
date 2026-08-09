from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.stremio.models import StremioAccount, StremioSyncIntent


@override_settings(
    STORAGES={
        "staticfiles": {
            "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
        },
    }
)
class StremioViewTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            "user@example.com",
            password="password",
        )
        self.client.login(username="user@example.com", password="password")

    @patch("apps.stremio.views.StremioClient")
    def test_connect_starts_link_flow_without_exposing_auth_key(self, client_class):
        client_class.return_value.create_link_code.return_value = {
            "code": "ABCD",
            "link": "https://link.stremio.com/ABCD",
            "qrcode": "data:image/png;base64,qr",
        }

        response = self.client.get(reverse("stremio_connect"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "https://link.stremio.com/ABCD")
        self.assertNotContains(response, "auth-key")
        self.assertEqual(self.client.session["stremio_link_code"], "ABCD")

    @patch("apps.stremio.views.enqueue_account_sync")
    @patch("apps.stremio.views.StremioClient")
    def test_complete_stores_encrypted_account_and_queues_sync(self, client_class, enqueue):
        session = self.client.session
        session["stremio_link_code"] = "ABCD"
        session.save()
        client_class.return_value.read_link_code.return_value = "auth-key"
        client_class.return_value.get_user.return_value = {
            "_id": "stremio-user",
            "username": "stremio-user",
        }

        response = self.client.post(reverse("stremio_complete"))

        self.assertEqual(response.status_code, 302)
        account = StremioAccount.objects.get(user=self.user)
        self.assertEqual(account.auth_key, "auth-key")
        self.assertEqual(account.stremio_user_id, "stremio-user")
        self.assertEqual(account.stremio_username, "stremio-user")
        self.assertFalse(account.initial_sync_complete)
        enqueue.assert_called_once_with(account.id)
        self.assertNotIn("stremio_link_code", self.client.session)

    @patch("apps.stremio.views.StremioClient")
    def test_complete_reports_pending_link(self, client_class):
        session = self.client.session
        session["stremio_link_code"] = "ABCD"
        session.save()
        client_class.return_value.read_link_code.return_value = None

        response = self.client.post(reverse("stremio_complete"))

        self.assertEqual(response.status_code, 202)
        self.assertContains(response, "still waiting", status_code=202)

    @patch("apps.stremio.views.StremioClient")
    def test_disconnect_is_scoped_to_current_user(self, client_class):
        other = get_user_model().objects.create_user("other@example.com")
        account = StremioAccount.objects.create(user=self.user, auth_key="auth-key")
        other_account = StremioAccount.objects.create(user=other, auth_key="other-key")
        StremioSyncIntent.objects.create(
            user=self.user,
            kind=StremioSyncIntent.Kind.MOVIE_WATCHLIST,
            identity_key="imdb:tt0137523",
        )

        response = self.client.post(reverse("stremio_disconnect"))

        self.assertEqual(response.status_code, 204)
        self.assertFalse(StremioAccount.objects.filter(id=account.id).exists())
        self.assertFalse(StremioSyncIntent.objects.filter(user=self.user).exists())
        self.assertTrue(StremioAccount.objects.filter(id=other_account.id).exists())
        client_class.return_value.logout.assert_called_once_with()

    def test_disconnect_deletes_account_when_auth_key_cannot_be_decrypted(self):
        account = StremioAccount.objects.create(user=self.user, auth_key="auth-key")
        StremioSyncIntent.objects.create(
            user=self.user,
            kind=StremioSyncIntent.Kind.MOVIE_WATCHLIST,
            identity_key="imdb:tt0137523",
        )

        self.client.logout()
        with override_settings(SECRET_KEY="rotated-secret-key"):
            self.client.force_login(self.user)
            response = self.client.post(reverse("stremio_disconnect"))

        self.assertEqual(response.status_code, 204)
        self.assertFalse(StremioAccount.objects.filter(id=account.id).exists())
        self.assertFalse(StremioSyncIntent.objects.filter(user=self.user).exists())

    @patch("apps.stremio.views.enqueue_account_sync")
    def test_manual_sync_queues_only_current_user(self, enqueue):
        account = StremioAccount.objects.create(user=self.user, auth_key="auth-key")

        response = self.client.post(reverse("stremio_sync"))

        self.assertEqual(response.status_code, 204)
        enqueue.assert_called_once_with(account.id)

    def test_settings_fragment_exposes_connection_state_without_auth_key(self):
        response = self.client.get(
            reverse("user_settings"),
            HTTP_HX_REQUEST="true",
        )
        self.assertContains(response, "Connect Stremio")

        StremioAccount.objects.create(
            user=self.user,
            stremio_username="stremio-user",
            auth_key="auth-secret",
        )
        response = self.client.get(
            reverse("user_settings"),
            HTTP_HX_REQUEST="true",
        )
        self.assertContains(response, "stremio-user")
        self.assertNotContains(response, "auth-secret")
