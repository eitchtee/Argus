from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone

from apps.mdblist.client import MdblistAuthenticationError, MdblistRateLimited, TokenResponse
from apps.mdblist.models import MdblistAccount, MdblistSyncIntent
from apps.mdblist.tasks import accounts_due_for_sync, sync_account_task
from apps.movies.models import Movie
from apps.movies.services import mark_seen


OAUTH = dict(MDBLIST_CLIENT_ID="cid", MDBLIST_CLIENT_SECRET="secret")


@override_settings(**OAUTH)
class SyncTaskTests(TransactionTestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("user@example.com", password="password")
        self.account = MdblistAccount.objects.create(
            user=self.user,
            access_token="access",
            refresh_token="refresh",
            token_expires_at=timezone.now() + timedelta(days=20),
        )

    @patch("apps.mdblist.tasks.sync_account", return_value="report")
    def test_success_clears_the_error(self, _sync):
        MdblistAccount.objects.filter(id=self.account.id).update(sync_status="error", last_error="boom")

        sync_account_task.func(self.account.id)

        self.account.refresh_from_db()
        self.assertEqual(self.account.sync_status, MdblistAccount.SyncStatus.OK)
        self.assertEqual(self.account.last_error, "")
        self.assertIsNotNone(self.account.last_synced_at)

    @patch("apps.mdblist.tasks.sync_account", side_effect=[MdblistAuthenticationError("expired"), "report"])
    def test_a_rejected_token_is_refreshed_once(self, sync):
        token = TokenResponse(access_token="new-access", refresh_token="new-refresh", expires_in=2592000)
        with patch("apps.mdblist.client.MdblistClient.refresh", return_value=token) as refresh:
            sync_account_task.func(self.account.id)

        refresh.assert_called_once_with("refresh")
        self.assertEqual(sync.call_count, 2)
        self.account.refresh_from_db()
        self.assertEqual((self.account.access_token, self.account.refresh_token), ("new-access", "new-refresh"))
        self.assertEqual(self.account.sync_status, MdblistAccount.SyncStatus.OK)

    @patch("apps.mdblist.tasks.sync_account", side_effect=MdblistAuthenticationError("revoked"))
    def test_a_revoked_api_key_asks_to_reconnect(self, _sync):
        MdblistAccount.objects.filter(id=self.account.id).update(auth_method="apikey", refresh_token="")

        sync_account_task.func(self.account.id)

        self.account.refresh_from_db()
        self.assertEqual(self.account.sync_status, MdblistAccount.SyncStatus.REAUTHORIZE)

    @patch("apps.mdblist.tasks.enqueue_account_sync")
    @patch("apps.mdblist.tasks.sync_account", side_effect=MdblistRateLimited(3600))
    def test_the_daily_quota_reschedules_the_sync(self, _sync, enqueue):
        sync_account_task.func(self.account.id)

        enqueue.assert_called_once_with(self.account.id, schedule_in={"seconds": 3600})
        self.account.refresh_from_db()
        self.assertIn("limit", self.account.last_error)

    def test_tokens_close_to_expiry_are_refreshed_before_syncing(self):
        from apps.mdblist.config import build_client

        MdblistAccount.objects.filter(id=self.account.id).update(token_expires_at=timezone.now() + timedelta(hours=1))
        self.account.refresh_from_db()
        token = TokenResponse(access_token="fresh", refresh_token="r2", expires_in=2592000)
        with patch("apps.mdblist.client.MdblistClient.refresh", return_value=token):
            client = build_client(self.account)

        self.assertEqual(client.access_token, "fresh")
        self.account.refresh_from_db()
        self.assertEqual(self.account.access_token, "fresh")


class DueAccountsTests(TestCase):
    def test_idle_accounts_wait_unless_they_have_changes_to_push(self):
        user = get_user_model().objects.create_user("idle@example.com", password="password")
        idle = MdblistAccount.objects.create(user=user, access_token="k", last_seen_at=timezone.now() - timedelta(days=3))
        active_user = get_user_model().objects.create_user("active@example.com", password="password")
        active = MdblistAccount.objects.create(user=active_user, access_token="k", last_seen_at=timezone.now())

        self.assertEqual(list(accounts_due_for_sync()), [active])

        mark_seen(user, Movie.objects.create(external_id="550", tmdb_id="550", title="Fight Club"))
        self.assertTrue(MdblistSyncIntent.objects.filter(user=user).exists())
        self.assertEqual({account.id for account in accounts_due_for_sync()}, {idle.id, active.id})
