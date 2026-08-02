from unittest.mock import AsyncMock, patch

from asgiref.sync import async_to_sync
from django.test import SimpleTestCase


class ProcrastinateJobRetentionTests(SimpleTestCase):
    def test_cleanup_is_registered_daily_with_a_queueing_lock(self):
        from procrastinate.contrib.django import app

        periodic_task = app.periodic_registry.periodic_tasks[("cleanup_old_jobs", "")]

        self.assertEqual(periodic_task.cron, "0 4 * * *")
        self.assertEqual(
            app.tasks["cleanup_old_jobs"].queueing_lock,
            "cleanup_old_jobs",
        )

    @patch(
        "apps.common.tasks.builtin_tasks.remove_old_jobs",
        new_callable=AsyncMock,
    )
    def test_cleanup_removes_final_jobs_older_than_thirty_days(self, remove_old_jobs):
        from apps.common.tasks import cleanup_old_jobs

        context = object()

        async_to_sync(cleanup_old_jobs.func)(context, timestamp=0)

        remove_old_jobs.assert_awaited_once_with(
            context,
            max_hours=30 * 24,
            remove_failed=True,
            remove_cancelled=True,
            remove_aborted=True,
        )
