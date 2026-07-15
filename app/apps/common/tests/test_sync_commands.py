from io import StringIO
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.core.management.base import CommandError
from django.test import SimpleTestCase
from huey.exceptions import TaskException

from apps.common.management.sync import run_sync_command


class SyncCommandHelperTests(SimpleTestCase):
    @patch("apps.common.management.sync.HUEY.result")
    def test_wait_checks_every_item_and_raises_after_failures(self, huey_result):
        dispatch_result = Mock()
        dispatch_result.get.return_value = ["item-1", "item-2"]
        huey_result.side_effect = [
            {"item_id": 1, "translation_task_id": "translation-1"},
            {"ok": True},
            {"item_id": 2, "translation_task_id": "translation-2"},
            TaskException({"error": "translation failed"}),
        ]
        dispatch_task = Mock(return_value=dispatch_result)
        command = SimpleNamespace(stdout=StringIO(), stderr=StringIO())

        with self.assertRaises(CommandError):
            run_sync_command(
                command,
                dispatch_task,
                label="movies",
                force_all=False,
                wait=True,
            )

        dispatch_task.assert_called_once_with(force_all=False)
        self.assertIn("translation failed", command.stderr.getvalue())
        self.assertEqual(huey_result.call_count, 4)
