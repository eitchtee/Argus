from io import StringIO
from unittest.mock import call, patch

from django.core.management import call_command
from django.test import TestCase

from apps.catalog.providers.exceptions import ProviderError
from apps.tv.models import Show


class RefreshTVDBShowsCommandTests(TestCase):
    @patch(
        "apps.tv.management.commands.refresh_tvdb_shows.hydrate_show_translations",
        create=True,
    )
    @patch(
        "apps.tv.management.commands.refresh_tvdb_shows.hydrate_show_translations_sync",
        create=True,
    )
    @patch("apps.tv.management.commands.refresh_tvdb_shows.import_show")
    def test_refreshes_each_tvdb_show_and_hydrates_translations_synchronously(
        self,
        import_show_mock,
        hydrate_show_translations_sync,
        hydrate_show_translations_task,
    ):
        one = Show.objects.create(provider="tvdb", external_id="1", name="One")
        two = Show.objects.create(provider="tvdb", external_id="2", name="Two")
        Show.objects.create(provider="other", external_id="3", name="Other")
        import_show_mock.side_effect = [one, two]

        call_command("refresh_tvdb_shows", stdout=StringIO())

        self.assertEqual(import_show_mock.call_args_list, [call("1"), call("2")])
        self.assertEqual(
            hydrate_show_translations_sync.call_args_list,
            [call(one.id), call(two.id)],
        )
        hydrate_show_translations_task.assert_not_called()

    @patch(
        "apps.tv.management.commands.refresh_tvdb_shows.hydrate_show_translations_sync",
        create=True,
    )
    @patch("apps.tv.management.commands.refresh_tvdb_shows.import_show")
    def test_continues_after_provider_error(
        self,
        import_show_mock,
        hydrate_show_translations_sync,
    ):
        Show.objects.create(provider="tvdb", external_id="1", name="One")
        two = Show.objects.create(provider="tvdb", external_id="2", name="Two")
        import_show_mock.side_effect = [ProviderError("down"), two]
        stderr = StringIO()

        call_command("refresh_tvdb_shows", stderr=stderr)

        self.assertEqual(import_show_mock.call_args_list, [call("1"), call("2")])
        hydrate_show_translations_sync.assert_called_once_with(two.id)
        self.assertIn("Failed to refresh show 1: down", stderr.getvalue())
