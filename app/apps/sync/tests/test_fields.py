from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase

from apps.simkl.models import SimklAccount


class EncryptedTextFieldTests(TestCase):
    def test_token_field_encrypts_at_rest_and_round_trips(self):
        user = get_user_model().objects.create_user(
            "user@example.com",
            password="password",
        )
        account = SimklAccount.objects.create(user=user, access_token="access-secret")

        account.refresh_from_db()

        self.assertEqual(account.access_token, "access-secret")
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT access_token FROM simkl_simklaccount WHERE id = %s",
                [account.id],
            )
            self.assertNotEqual(cursor.fetchone()[0], "access-secret")
