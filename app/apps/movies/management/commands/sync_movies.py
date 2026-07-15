from django.core.management.base import BaseCommand

from apps.common.management.sync import run_sync_command
from apps.movies.tasks import sync_movies


class Command(BaseCommand):
    help = "Queue synchronization for stored movies."

    def add_arguments(self, parser):
        parser.add_argument(
            "--all",
            action="store_true",
            dest="force_all",
            help="Synchronize every stored TMDB movie instead of stale tracked movies.",
        )
        parser.add_argument(
            "--wait",
            action="store_true",
            help="Wait for primary and translation tasks to finish.",
        )

    def handle(self, *args, **options):
        run_sync_command(
            self,
            sync_movies,
            label="movie",
            force_all=options["force_all"],
            wait=options["wait"],
        )
