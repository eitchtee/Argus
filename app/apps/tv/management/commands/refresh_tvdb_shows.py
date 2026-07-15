from django.core.management.base import BaseCommand

from apps.catalog.providers.exceptions import ProviderError
from apps.tv.models import Show
from apps.tv.services import hydrate_show_translations_sync, import_show


class Command(BaseCommand):
    help = "Refreshes all TVDB shows so their metadata and cross-provider IDs are current."

    def handle(self, *args, **options):
        for show in Show.objects.filter(provider="tvdb").order_by("id"):
            try:
                imported_show = import_show(show.external_id)
                hydrate_show_translations_sync(imported_show.id)
            except ProviderError as exc:
                self.stderr.write(f"Failed to refresh show {show.external_id}: {exc}")
            else:
                self.stdout.write(f"Refreshed show {show.external_id}")
