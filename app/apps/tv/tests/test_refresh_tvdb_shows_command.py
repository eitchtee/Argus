from io import StringIO
from unittest.mock import call, patch

from django.core.management import call_command
from django.test import TestCase

from apps.catalog.providers.exceptions import ProviderError
from apps.tv.models import Show


class RefreshTVDBShowsCommandTests(TestCase):
    @patch("apps.tv.management.commands.refresh_tvdb_shows.import_show")
    def test_refreshes_each_tvdb_show(self, import_show_mock):
        Show.objects.create(provider="tvdb", external_id="1", name="One")
        Show.objects.create(provider="tvdb", external_id="2", name="Two")
        Show.objects.create(provider="other", external_id="3", name="Other")

        call_command("refresh_tvdb_shows", stdout=StringIO())

        self.assertEqual(import_show_mock.call_args_list, [call("1"), call("2")])

    @patch("apps.tv.management.commands.refresh_tvdb_shows.import_show")
    def test_continues_after_provider_error(self, import_show_mock):
        Show.objects.create(provider="tvdb", external_id="1", name="One")
        Show.objects.create(provider="tvdb", external_id="2", name="Two")
        import_show_mock.side_effect = [ProviderError("down"), None]
        stderr = StringIO()

        call_command("refresh_tvdb_shows", stderr=stderr)

        self.assertEqual(import_show_mock.call_args_list, [call("1"), call("2")])
        self.assertIn("Failed to refresh show 1: down", stderr.getvalue())
