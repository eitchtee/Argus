from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.test import SimpleTestCase


class SyncTVCommandTests(SimpleTestCase):
    @patch("apps.tv.management.commands.sync_tv.sync_tv")
    def test_default_dispatch_is_stale_only(self, sync_tv):
        call_command("sync_tv", stdout=StringIO())

        sync_tv.assert_called_once_with(force_all=False)

    @patch("apps.tv.management.commands.sync_tv.sync_tv")
    def test_all_dispatch_forces_every_show(self, sync_tv):
        call_command("sync_tv", "--all", stdout=StringIO())

        sync_tv.assert_called_once_with(force_all=True)
