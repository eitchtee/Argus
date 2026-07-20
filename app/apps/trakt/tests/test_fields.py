from django.contrib.auth import get_user_model
from django.db import IntegrityError, connection
from django.test import TestCase
from django.utils import timezone

from apps.trakt.models import TraktAccount


class TraktAccountModelTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            "user@example.com",
            password="password",
        )

    def test_token_field_encrypts_at_rest_and_round_trips(self):
        account = TraktAccount.objects.create(
            user=self.user,
            access_token="access-secret",
            refresh_token="refresh-secret",
            token_expires_at=timezone.now(),
        )

        account.refresh_from_db()

        self.assertEqual(account.access_token, "access-secret")
        self.assertEqual(account.refresh_token, "refresh-secret")
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT access_token FROM trakt_traktaccount WHERE id = %s",
                [account.id],
            )
            self.assertNotEqual(cursor.fetchone()[0], "access-secret")

    def test_account_is_one_per_user(self):
        TraktAccount.objects.create(
            user=self.user,
            access_token="a",
            refresh_token="b",
        )

        with self.assertRaises(IntegrityError):
            TraktAccount.objects.create(
                user=self.user,
                access_token="c",
                refresh_token="d",
            )
