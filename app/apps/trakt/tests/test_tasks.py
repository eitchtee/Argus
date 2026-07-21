from datetime import timedelta
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TransactionTestCase, override_settings
from django.utils import timezone

from apps.trakt.client import TraktAuthenticationError, TraktRateLimited, TokenResponse
from apps.trakt.models import TraktAccount


class TraktTaskSchedulingTests(SimpleTestCase):
    @patch("apps.trakt.tasks.sync_account_task")
    def test_enqueue_uses_one_lock_per_account(self, task):
        task.configure.return_value.defer.return_value = 41

        from apps.trakt.tasks import enqueue_account_sync

        result = enqueue_account_sync(7)

        self.assertEqual(result, 41)
        task.configure.assert_called_once_with(
            lock="trakt-account:7",
            queueing_lock="trakt-account:7",
        )
        task.configure.return_value.defer.assert_called_once_with(account_id=7)

    @patch("apps.trakt.tasks.sync_account_task")
    def test_enqueue_can_schedule_after_retry_delay(self, task):
        task.configure.return_value.defer.return_value = 42

        from apps.trakt.tasks import enqueue_account_sync

        enqueue_account_sync(7, schedule_in={"seconds": 23})

        task.configure.assert_called_once_with(
            lock="trakt-account:7",
            queueing_lock="trakt-account:7",
            schedule_in={"seconds": 23},
        )

    @patch("apps.trakt.tasks._recover_stalled_account_sync", return_value=43)
    @patch("apps.trakt.tasks.sync_account_task")
    def test_enqueue_recovers_stalled_job_when_queueing_lock_is_taken(
        self, task, recover
    ):
        from procrastinate.exceptions import AlreadyEnqueued

        task.configure.return_value.defer.side_effect = AlreadyEnqueued()

        from apps.trakt.tasks import enqueue_account_sync

        result = enqueue_account_sync(7)

        self.assertEqual(result, 43)
        recover.assert_called_once_with(7)


class TraktTaskTests(TransactionTestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("user@example.com")
        self.account = TraktAccount.objects.create(
            user=self.user,
            access_token="access",
            refresh_token="refresh",
            token_expires_at=timezone.now() - timedelta(minutes=1),
        )

    @override_settings(TRAKT_CLIENT_ID="client", TRAKT_CLIENT_SECRET="secret", TRAKT_REDIRECT_URI="https://argus.test/user/trakt/callback/")
    @patch("apps.trakt.tasks.TraktClient")
    def test_build_client_refreshes_expiring_access_token(self, client_class):
        client = client_class.return_value
        client.refresh_access_token.return_value = TokenResponse(
            access_token="new-access",
            refresh_token="new-refresh",
            expires_in=3600,
        )

        from apps.trakt.tasks import build_client

        build_client(self.account)

        client.refresh_access_token.assert_called_once_with(
            "refresh",
            "https://argus.test/user/trakt/callback/",
        )
        self.account.refresh_from_db()
        self.assertEqual(self.account.access_token, "new-access")
        self.assertEqual(self.account.refresh_token, "new-refresh")

    @patch("apps.trakt.tasks.enqueue_account_sync")
    @patch("apps.trakt.tasks.sync_account", side_effect=TraktRateLimited(23))
    def test_rate_limit_schedules_after_retry_after(self, _sync, enqueue):
        from apps.trakt.tasks import sync_account_task

        sync_account_task.func(self.account.id)

        enqueue.assert_called_once_with(
            self.account.id,
            schedule_in={"seconds": 23},
        )

    @patch("apps.trakt.tasks.sync_account", side_effect=TraktAuthenticationError("bad"))
    def test_authentication_failure_requires_reauthorization(self, _sync):
        from apps.trakt.tasks import sync_account_task

        sync_account_task.func(self.account.id)

        self.account.refresh_from_db()
        self.assertEqual(self.account.sync_status, TraktAccount.SyncStatus.REAUTHORIZE)

    @patch("apps.trakt.tasks.enqueue_account_sync")
    def test_periodic_sync_enqueues_each_connected_account(self, enqueue):
        second_user = get_user_model().objects.create_user("second@example.com")
        second = TraktAccount.objects.create(
            user=second_user,
            access_token="access-2",
            refresh_token="refresh-2",
        )

        from apps.trakt.tasks import periodic_trakt_sync

        periodic_trakt_sync.func(timestamp=0)

        self.assertCountEqual(
            [call.args[0] for call in enqueue.call_args_list],
            [self.account.id, second.id],
        )

    @override_settings(PROCRASTINATE_READONLY_MODELS=False)
    @patch("apps.trakt.tasks._finish_stalled_job")
    @patch("apps.trakt.tasks.app.job_manager.retry_job_by_id")
    def test_stalled_sync_keeps_waiting_job_and_releases_dead_worker_lock(
        self, retry_job, finish_job
    ):
        from procrastinate.contrib.django.models import ProcrastinateJob

        lock = f"trakt-account:{self.account.id}"
        stalled = ProcrastinateJob.objects.create(
            queue_name="default",
            task_name="sync_trakt_account",
            priority=0,
            lock=lock,
            args={"account_id": self.account.id},
            status="doing",
            scheduled_at=None,
            attempts=0,
            queueing_lock=lock,
            abort_requested=False,
            worker=None,
        )
        waiting = ProcrastinateJob.objects.create(
            queue_name="default",
            task_name="sync_trakt_account",
            priority=0,
            lock=lock,
            args={"account_id": self.account.id},
            status="todo",
            scheduled_at=None,
            attempts=0,
            queueing_lock=lock,
            abort_requested=False,
            worker=None,
        )

        from apps.trakt.tasks import _recover_stalled_account_sync

        result = _recover_stalled_account_sync(self.account.id)

        self.assertEqual(result, waiting.id)
        finish_job.assert_called_once_with(stalled.id)
        retry_job.assert_not_called()
