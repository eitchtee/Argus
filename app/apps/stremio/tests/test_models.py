from datetime import timedelta

from django.contrib.auth import get_user_model
from django.contrib import admin
from django.db import IntegrityError
from django.test import TestCase
from django.utils import timezone

from apps.stremio.admin import StremioAccountAdmin
from apps.stremio.models import StremioAccount, StremioSyncIntent


class StremioModelTests(TestCase):
    def test_admin_form_excludes_the_decrypted_auth_key(self):
        model_admin = StremioAccountAdmin(StremioAccount, admin.site)

        self.assertIn("auth_key", model_admin.exclude)

    def test_account_stores_auth_key_through_the_existing_encrypted_field(self):
        user = get_user_model().objects.create_user("user@example.com", password="pw")

        account = StremioAccount.objects.create(
            user=user,
            auth_key="secret-auth-key",
            stremio_user_id="stremio-user",
        )

        account.refresh_from_db()
        self.assertEqual(account.auth_key, "secret-auth-key")
        self.assertEqual(account.stremio_user_id, "stremio-user")

    def test_each_user_has_only_one_stremio_account(self):
        user = get_user_model().objects.create_user("user@example.com", password="pw")
        StremioAccount.objects.create(user=user, auth_key="one")

        with self.assertRaises(IntegrityError):
            with self.captureOnCommitCallbacks(execute=True):
                StremioAccount.objects.create(user=user, auth_key="two")

    def test_sync_intent_is_unique_and_keeps_newest_history_payload(self):
        user = get_user_model().objects.create_user("user@example.com", password="pw")
        older = timezone.now() - timedelta(days=1)
        newer = timezone.now()

        first = StremioSyncIntent.objects.create(
            user=user,
            kind=StremioSyncIntent.Kind.MOVIE_HISTORY,
            identity_key="imdb:tt0137523",
            payload={"ids": {"imdb": "tt0137523"}, "watched_at": older.isoformat()},
        )
        self.assertEqual(first.identity_key, "imdb:tt0137523")
