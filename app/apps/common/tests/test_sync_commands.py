from io import StringIO
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.core.management.base import CommandError
from django.test import SimpleTestCase
from procrastinate import jobs

from apps.common.management.sync import run_sync_command


class SyncCommandHelperTests(SimpleTestCase):
    @patch("apps.common.management.sync.wait_for_jobs")
    def test_wait_checks_every_item_and_raises_after_failures(self, wait_for_jobs):
        dispatch_task = Mock()
        dispatch_task.func.return_value = [11, 12]
        wait_for_jobs.return_value = [
            (11, jobs.Status.SUCCEEDED),
            (12, jobs.Status.FAILED),
        ]
        command = SimpleNamespace(stdout=StringIO(), stderr=StringIO())

        with self.assertRaisesMessage(
            CommandError,
            "1 movies synchronization task(s) failed.",
        ):
            run_sync_command(
                command,
                dispatch_task,
                label="movies",
                force_all=False,
                wait=True,
            )

        dispatch_task.func.assert_called_once_with(force_all=False)
        wait_for_jobs.assert_called_once_with([11, 12], timeout=300)
        self.assertIn("12: failed", command.stderr.getvalue())

    @patch("apps.common.management.sync.wait_for_jobs")
    def test_wait_reports_successful_item_count(self, wait_for_jobs):
        dispatch_task = Mock()
        dispatch_task.func.return_value = [11, 12]
        wait_for_jobs.return_value = [
            (11, jobs.Status.SUCCEEDED),
            (12, jobs.Status.SUCCEEDED),
        ]
        command = SimpleNamespace(stdout=StringIO(), stderr=StringIO())

        run_sync_command(
            command,
            dispatch_task,
            label="movies",
            force_all=True,
            wait=True,
        )

        self.assertIn("Synchronized 2 movies item(s).", command.stdout.getvalue())

    @patch("apps.common.management.sync.wait_for_jobs")
    def test_wait_timeout_is_reported(self, wait_for_jobs):
        dispatch_task = Mock()
        dispatch_task.func.return_value = [11]
        wait_for_jobs.side_effect = CommandError(
            "Timed out waiting for Procrastinate jobs."
        )
        command = SimpleNamespace(stdout=StringIO(), stderr=StringIO())

        with self.assertRaisesMessage(CommandError, "Timed out waiting"):
            run_sync_command(
                command,
                dispatch_task,
                label="movies",
                force_all=False,
                wait=True,
                wait_timeout=10,
            )

        wait_for_jobs.assert_called_once_with([11], timeout=10)

    def test_invalid_wait_timeout_is_rejected_before_dispatch(self):
        dispatch_task = Mock()
        command = SimpleNamespace(stdout=StringIO(), stderr=StringIO())

        with self.assertRaisesMessage(
            CommandError,
            "--wait-timeout must be greater than zero.",
        ):
            run_sync_command(
                command,
                dispatch_task,
                label="movies",
                force_all=False,
                wait=True,
                wait_timeout=0,
            )

        dispatch_task.func.assert_not_called()
        dispatch_task.defer.assert_not_called()

    def test_non_waiting_dispatch_reports_task_id(self):
        dispatch_task = Mock()
        dispatch_task.defer.return_value = 41
        command = SimpleNamespace(stdout=StringIO(), stderr=StringIO())

        run_sync_command(
            command,
            dispatch_task,
            label="movies",
            force_all=True,
            wait=False,
        )

        dispatch_task.defer.assert_called_once_with(force_all=True)
        self.assertIn("41", command.stdout.getvalue())
