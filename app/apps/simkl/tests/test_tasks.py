from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TransactionTestCase, override_settings

from apps.simkl.client import SimklAuthenticationError, SimklRateLimited
from apps.simkl.models import SimklAccount


class SimklTaskSchedulingTests(SimpleTestCase):
    @patch("apps.simkl.tasks.sync_account_task")
    def test_enqueue_uses_one_lock_per_account(self, task):
        task.configure.return_value.defer.return_value = 41

        from apps.simkl.tasks import enqueue_account_sync

        self.assertEqual(enqueue_account_sync(7), 41)
        task.configure.assert_called_once_with(
            lock="simkl-account:7",
            queueing_lock="simkl-account:7",
        )
        task.configure.return_value.defer.assert_called_once_with(account_id=7)

    @patch("apps.simkl.tasks._recover_stalled_account_sync", return_value=43)
    @patch("apps.simkl.tasks.sync_account_task")
    def test_enqueue_recovers_stalled_job_when_lock_is_taken(self, task, recover):
        from procrastinate.exceptions import AlreadyEnqueued

        task.configure.return_value.defer.side_effect = AlreadyEnqueued()

        from apps.simkl.tasks import enqueue_account_sync

        self.assertEqual(enqueue_account_sync(7), 43)
        recover.assert_called_once_with(7)


class SimklTaskTests(TransactionTestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("user@example.com")
        self.account = SimklAccount.objects.create(user=self.user, access_token="token")

    @override_settings(
        SIMKL_CLIENT_ID="client",
        SIMKL_CLIENT_SECRET="secret",
        SIMKL_REDIRECT_URI="https://argus.test/user/simkl/callback/",
    )
    @patch("apps.simkl.tasks.enqueue_account_sync")
    @patch("apps.simkl.tasks.sync_account", side_effect=SimklRateLimited(23))
    def test_rate_limit_reschedules_the_sync(self, _sync, enqueue):
        from apps.simkl.tasks import sync_account_task

        sync_account_task.func(self.account.id)

        enqueue.assert_called_once_with(self.account.id, schedule_in={"seconds": 23})
        self.account.refresh_from_db()
        self.assertEqual(self.account.sync_status, SimklAccount.SyncStatus.ERROR)
        self.assertIn("23", self.account.last_error)

    @override_settings(
        SIMKL_CLIENT_ID="client",
        SIMKL_CLIENT_SECRET="secret",
        SIMKL_REDIRECT_URI="https://argus.test/user/simkl/callback/",
    )
    @patch("apps.simkl.tasks.sync_account", side_effect=SimklAuthenticationError("revoked"))
    def test_revoked_token_marks_account_for_reauthorization(self, _sync):
        from apps.simkl.tasks import sync_account_task

        sync_account_task.func(self.account.id)

        self.account.refresh_from_db()
        self.assertEqual(self.account.sync_status, SimklAccount.SyncStatus.REAUTHORIZE)

    @override_settings(SIMKL_CLIENT_ID="", SIMKL_CLIENT_SECRET="", SIMKL_REDIRECT_URI="")
    def test_missing_configuration_is_reported_on_the_account(self):
        from apps.simkl.tasks import sync_account_task

        sync_account_task.func(self.account.id)

        self.account.refresh_from_db()
        self.assertEqual(self.account.sync_status, SimklAccount.SyncStatus.ERROR)
        self.assertIn("not configured", self.account.last_error)

    @override_settings(SIMKL_IDLE_HOURS=24)
    def test_periodic_sync_skips_idle_accounts_without_pending_changes(self):
        from datetime import timedelta

        from django.utils import timezone

        from apps.simkl.models import SimklSyncIntent
        from apps.simkl.tasks import accounts_due_for_sync

        # Never seen, nothing pending: skipped.
        self.assertEqual(list(accounts_due_for_sync()), [])

        self.account.last_seen_at = timezone.now() - timedelta(hours=48)
        self.account.save(update_fields=["last_seen_at"])
        self.assertEqual(list(accounts_due_for_sync()), [])

        SimklSyncIntent.objects.create(user=self.user, kind="movie_history", identity_key="x", payload={})
        self.assertEqual(list(accounts_due_for_sync()), [self.account])

        SimklSyncIntent.objects.all().delete()
        self.account.last_seen_at = timezone.now() - timedelta(hours=1)
        self.account.save(update_fields=["last_seen_at"])
        self.assertEqual(list(accounts_due_for_sync()), [self.account])

    @override_settings(
        SIMKL_CLIENT_ID="client",
        SIMKL_CLIENT_SECRET="secret",
        SIMKL_REDIRECT_URI="https://argus.test/user/simkl/callback/",
        SIMKL_IDLE_HOURS=24,
    )
    @patch("apps.simkl.tasks.enqueue_account_sync")
    def test_returning_after_idle_stamps_account_and_queues_a_sync(self, enqueue):
        from datetime import timedelta

        from django.core.cache import cache
        from django.utils import timezone

        from apps.simkl.middleware import touch_account

        cache.clear()
        # First visit ever: stamped and synced at once.
        touch_account(self.user.pk)
        self.account.refresh_from_db()
        first = self.account.last_seen_at
        self.assertIsNotNone(first)
        enqueue.assert_called_once_with(self.account.id)

        # Within the stamp interval nothing is written again.
        touch_account(self.user.pk)
        self.account.refresh_from_db()
        self.assertEqual(self.account.last_seen_at, first)
        enqueue.assert_called_once()

        # Still active: the stamp moves but the periodic job handles syncing.
        cache.clear()
        touch_account(self.user.pk)
        enqueue.assert_called_once()

        # Back after a long idle spell: sync queued again.
        cache.clear()
        self.account.last_seen_at = timezone.now() - timedelta(hours=48)
        self.account.save(update_fields=["last_seen_at"])
        touch_account(self.user.pk)
        self.assertEqual(enqueue.call_count, 2)
        cache.clear()

    @override_settings(
        SIMKL_CLIENT_ID="client",
        SIMKL_CLIENT_SECRET="secret",
        SIMKL_REDIRECT_URI="https://argus.test/user/simkl/callback/",
    )
    @patch("apps.simkl.tasks.sync_account")
    def test_successful_sync_clears_errors(self, sync):
        from apps.simkl.tasks import sync_account_task

        self.account.sync_status = SimklAccount.SyncStatus.ERROR
        self.account.last_error = "boom"
        self.account.save()

        sync_account_task.func(self.account.id)

        self.account.refresh_from_db()
        self.assertEqual(self.account.sync_status, SimklAccount.SyncStatus.OK)
        self.assertEqual(self.account.last_error, "")
        self.assertIsNotNone(self.account.last_synced_at)
        sync.assert_called_once()
