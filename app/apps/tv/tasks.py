from django.conf import settings
from django.db.models import Q
from django.utils import timezone
from procrastinate.contrib.django import app

from apps.catalog.models import SyncStatus
from apps.catalog.localization import PROVIDER_DEFAULT_LANGUAGES
from apps.tv import services as tv_services
from apps.tv.models import Show, UserShow


@app.task(name="hydrate_show_translations")
def hydrate_show_translations(show_id: int):
    return tv_services.hydrate_show_translations_sync(show_id)


@app.task(name="sync_show")
def sync_show(show_id: int):
    show = Show.objects.get(id=show_id)
    show.sync_status = SyncStatus.ERROR
    show.save(update_fields=["sync_status", "updated_at"])

    try:
        imported_show = tv_services.import_show(
            show.external_id,
            provider=show.provider,
            language=PROVIDER_DEFAULT_LANGUAGES[show.provider],
        )
        translation_task_id = hydrate_show_translations.defer(show_id=imported_show.id)
        return {
            "item_id": imported_show.id,
            "translation_task_id": translation_task_id,
        }
    except Exception:
        Show.objects.filter(id=show_id).update(sync_status=SyncStatus.ERROR)
        raise


@app.task(name="sync_tv")
def sync_tv(force_all: bool = False):
    if force_all:
        show_ids = Show.objects.filter(
            provider__in=PROVIDER_DEFAULT_LANGUAGES,
        ).values_list("id", flat=True)
    else:
        now = timezone.now()
        cutoff = now - timezone.timedelta(
            days=settings.CATALOG_SHOW_SYNC_INTERVAL_DAYS,
        )
        ended_cutoff = now - timezone.timedelta(
            days=settings.CATALOG_ENDED_SHOW_SYNC_INTERVAL_DAYS,
        )
        tracked_show_ids = UserShow.objects.values_list("show_id", flat=True).distinct()
        show_ids = (
            Show.objects.filter(
                provider__in=PROVIDER_DEFAULT_LANGUAGES,
                id__in=tracked_show_ids,
            )
            .filter(
                Q(last_synced_at__isnull=True)
                | Q(status__iexact="Ended", last_synced_at__lte=ended_cutoff)
                | (
                    (Q(status__isnull=True) | ~Q(status__iexact="Ended"))
                    & Q(last_synced_at__lte=cutoff)
                )
            )
            .values_list("id", flat=True)
        )

    return [sync_show.defer(show_id=show_id) for show_id in show_ids]


@app.periodic(cron="0 2 * * *")
@app.task(name="daily_tv_sync")
def daily_tv_sync(timestamp: int):
    sync_tv.defer()
