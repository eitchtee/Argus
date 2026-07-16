from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.test import SimpleTestCase


class SyncMoviesCommandTests(SimpleTestCase):
    @patch("apps.movies.management.commands.sync_movies.sync_movies")
    def test_default_dispatch_is_stale_only(self, sync_movies):
        call_command("sync_movies", stdout=StringIO())

        sync_movies.defer.assert_called_once_with(force_all=False)

    @patch("apps.movies.management.commands.sync_movies.sync_movies")
    def test_all_dispatch_forces_every_movie(self, sync_movies):
        call_command("sync_movies", "--all", stdout=StringIO())

        sync_movies.defer.assert_called_once_with(force_all=True)
