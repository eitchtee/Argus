from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class MovieNormalizedStatusMigrationTests(TransactionTestCase):
    migrate_from = [("movies", "0006_normalize_default_titles")]
    migrate_to = [("movies", "0007_movie_normalized_status")]

    def setUp(self):
        super().setUp()
        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_from)
        old_apps = executor.loader.project_state(self.migrate_from).apps
        Movie = old_apps.get_model("movies", "Movie")

        self.movie_ids = {
            status: Movie.objects.create(
                provider="tmdb",
                external_id=status.lower().replace(" ", "-"),
                title=status or "Unknown",
                status=status,
            ).pk
            for status in (
                "Planned",
                "In Production",
                "Completed",
                "Released",
                "Canceled",
                "Not Available",
                "",
            )
        }

        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_to)
        self.apps = executor.loader.project_state(self.migrate_to).apps

    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
        super().tearDown()

    def test_existing_movie_statuses_are_backfilled(self):
        Movie = self.apps.get_model("movies", "Movie")

        expected = {
            "Planned": "Upcoming",
            "In Production": "Upcoming",
            "Completed": "Upcoming",
            "Released": "Released",
            "Canceled": "Canceled",
            "Not Available": "Unknown",
            "": "Unknown",
        }
        for status, normalized_status in expected.items():
            with self.subTest(status=status):
                movie = Movie.objects.get(pk=self.movie_ids[status])
                self.assertEqual(movie.normalized_status, normalized_status)
