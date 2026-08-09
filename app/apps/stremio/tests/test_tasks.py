from unittest.mock import ANY, patch

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TransactionTestCase
from django.utils import timezone

from apps.stremio.client import StremioAPIError
from apps.stremio.models import StremioAccount
from apps.stremio.sync import SyncReport


class StremioTaskSchedulingTests(SimpleTestCase):
    @patch("apps.stremio.tasks.sync_account_task")
    def test_enqueue_uses_one_lock_per_account(self, task):
        task.configure.return_value.defer.return_value = 41

        from apps.stremio.tasks import enqueue_account_sync

        result = enqueue_account_sync(7)

        self.assertEqual(result, 41)
        task.configure.assert_called_once_with(
            lock="stremio-account:7",
            queueing_lock="stremio-account:7",
        )
        task.configure.return_value.defer.assert_called_once_with(account_id=7)

    @patch("apps.stremio.tasks.StremioAccount.objects.filter")
    @patch("apps.stremio.tasks.sync_account")
    def test_sync_warnings_keep_the_job_successful(self, sync, account_filter):
        sync.return_value = SyncReport(warnings=["metadata unavailable"])

        from apps.stremio.tasks import sync_account_task

        report = sync_account_task.func(7)

        self.assertEqual(report.warnings, ["metadata unavailable"])
        account_filter.assert_called_once_with(id=7)
        account_filter.return_value.update.assert_called_once_with(
            sync_status=StremioAccount.SyncStatus.ERROR,
            last_error="metadata unavailable",
            updated_at=ANY,
        )


class StremioTaskTests(TransactionTestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("user@example.com")
        self.account = StremioAccount.objects.create(user=self.user, auth_key="auth-key")

    @patch("apps.stremio.tasks.sync_account", side_effect=StremioAPIError("expired", code=401))
    def test_authentication_failure_requires_reauthorization(self, _sync):
        from apps.stremio.tasks import sync_account_task

        sync_account_task.func(self.account.id)

        self.account.refresh_from_db()
        self.assertEqual(self.account.sync_status, StremioAccount.SyncStatus.REAUTHORIZE)

    @patch("apps.stremio.tasks.enqueue_account_sync")
    def test_periodic_sync_enqueues_each_connected_account(self, enqueue):
        second_user = get_user_model().objects.create_user("second@example.com")
        second = StremioAccount.objects.create(user=second_user, auth_key="auth-key-2")

        from apps.stremio.tasks import periodic_stremio_sync

        periodic_stremio_sync.func(timestamp=0)

        self.assertCountEqual(
            [call.args[0] for call in enqueue.call_args_list],
            [self.account.id, second.id],
        )
