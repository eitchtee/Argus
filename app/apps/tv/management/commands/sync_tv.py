from django.core.management.base import BaseCommand

from apps.common.management.sync import run_sync_command
from apps.tv.tasks import sync_tv


class Command(BaseCommand):
    help = "Queue synchronization for stored TV shows."

    def add_arguments(self, parser):
        parser.add_argument(
            "--all",
            action="store_true",
            dest="force_all",
            help="Synchronize every stored TVDB show instead of stale tracked shows.",
        )
        parser.add_argument(
            "--wait",
            action="store_true",
            help="Wait for synchronization tasks to finish.",
        )
        parser.add_argument(
            "--wait-timeout",
            type=int,
            default=300,
            metavar="SECONDS",
            help="Maximum time to wait for each task result (default: 300).",
        )

    def handle(self, *args, **options):
        run_sync_command(
            self,
            sync_tv,
            label="TV",
            force_all=options["force_all"],
            wait=options["wait"],
            wait_timeout=options["wait_timeout"],
        )
