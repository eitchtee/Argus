from datetime import timedelta

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db.models import Exists, OuterRef, Q
from django.utils import timezone
from procrastinate import jobs
from procrastinate.contrib.django import app
from procrastinate.contrib.django.models import ProcrastinateJob
from procrastinate.exceptions import AlreadyEnqueued
from procrastinate.utils import async_to_sync

from apps.simkl.client import (
    SimklAuthenticationError,
    SimklError,
    SimklRateLimited,
)
from apps.simkl.config import (  # noqa: F401 - re-exported for callers
    SimklConfigurationError,
    build_client,
    catalog_configured,
    is_configured,
)
from apps.simkl.enrichment import refresh_media_info, stale_media_queryset
from apps.simkl.models import SimklAccount, SimklSyncIntent
from apps.simkl.sync import sync_account


_SYNC_TASK_NAME = "sync_simkl_account"
_STALLED_WORKER_TIMEOUT = timedelta(seconds=30)


def _finish_stalled_job(job_id: int) -> None:
    async_to_sync(
        app.job_manager.finish_job_by_id_async,
        job_id=job_id,
        status=jobs.Status.ABORTED,
        delete_job=True,
    )


def _recover_stalled_account_sync(account_id: int) -> int | None:
    """Release a dead worker's account lock and keep the newest queued sync."""
    lock = f"simkl-account:{account_id}"
    stalled_before = timezone.now() - _STALLED_WORKER_TIMEOUT
    stalled_jobs = list(
        ProcrastinateJob.objects.filter(
            task_name=_SYNC_TASK_NAME,
            lock=lock,
            status=jobs.Status.DOING.value,
        )
        .filter(Q(worker__isnull=True) | Q(worker__last_heartbeat__lt=stalled_before))
        .order_by("id")
    )
    if not stalled_jobs:
        return None

    waiting_job = (
        ProcrastinateJob.objects.filter(
            task_name=_SYNC_TASK_NAME,
            queueing_lock=lock,
            status=jobs.Status.TODO.value,
        )
        .order_by("id")
        .first()
    )
    if waiting_job is not None:
        for stalled_job in stalled_jobs:
            _finish_stalled_job(stalled_job.id)
        return waiting_job.id

    for stalled_job in stalled_jobs[1:]:
        _finish_stalled_job(stalled_job.id)
    app.job_manager.retry_job_by_id(stalled_jobs[0].id, retry_at=timezone.now())
    return stalled_jobs[0].id


def enqueue_account_sync(account_id: int, *, schedule_in: dict | None = None) -> int | None:
    lock = f"simkl-account:{account_id}"
    options = {"lock": lock, "queueing_lock": lock}
    if schedule_in is not None:
        options["schedule_in"] = schedule_in
    try:
        return sync_account_task.configure(**options).defer(account_id=account_id)
    except AlreadyEnqueued:
        return _recover_stalled_account_sync(account_id)


@app.task(name=_SYNC_TASK_NAME)
def sync_account_task(account_id: int):
    try:
        report = sync_account(account_id, client_factory=build_client)
    except SimklRateLimited as exc:
        SimklAccount.objects.filter(id=account_id).update(
            sync_status=SimklAccount.SyncStatus.ERROR,
            last_error=f"SIMKL rate limit; retrying in {exc.retry_after} seconds.",
            updated_at=timezone.now(),
        )
        enqueue_account_sync(account_id, schedule_in={"seconds": exc.retry_after})
        return None
    except SimklAuthenticationError:
        SimklAccount.objects.filter(id=account_id).update(
            sync_status=SimklAccount.SyncStatus.REAUTHORIZE,
            last_error="SIMKL authorization was revoked; reconnect the account.",
            updated_at=timezone.now(),
        )
        return None
    except SimklConfigurationError as exc:
        SimklAccount.objects.filter(id=account_id).update(
            sync_status=SimklAccount.SyncStatus.ERROR,
            last_error=str(exc),
            updated_at=timezone.now(),
        )
        return None
    except ImproperlyConfigured:
        SimklAccount.objects.filter(id=account_id).update(
            sync_status=SimklAccount.SyncStatus.REAUTHORIZE,
            last_error="SIMKL token cannot be decrypted; reconnect the account.",
            updated_at=timezone.now(),
        )
        return None
    except SimklError as exc:
        SimklAccount.objects.filter(id=account_id).update(
            sync_status=SimklAccount.SyncStatus.ERROR,
            last_error=str(exc),
            updated_at=timezone.now(),
        )
        raise

    SimklAccount.objects.filter(id=account_id).update(
        sync_status=SimklAccount.SyncStatus.OK,
        last_error="",
        last_synced_at=timezone.now(),
        updated_at=timezone.now(),
    )
    return report


def accounts_due_for_sync():
    """Accounts worth polling: recently active users, or pending local changes.

    SIMKL asks clients not to poll without user interaction; an account whose
    user has been away longer than ``SIMKL_IDLE_HOURS`` waits until they are
    back (or press Sync now), unless there is something of theirs to push.
    """
    idle_cutoff = timezone.now() - timedelta(hours=settings.SIMKL_IDLE_HOURS)
    pending = SimklSyncIntent.objects.filter(user_id=OuterRef("user_id"))
    return (
        SimklAccount.objects.exclude(sync_status=SimklAccount.SyncStatus.REAUTHORIZE)
        .annotate(has_pending=Exists(pending))
        .filter(Q(last_seen_at__gte=idle_cutoff) | Q(has_pending=True))
    )


@app.periodic(cron=settings.SIMKL_SYNC_CRON)
@app.task(name="periodic_simkl_sync")
def periodic_simkl_sync(timestamp: int | None = None):
    if not is_configured():
        return []
    account_ids = accounts_due_for_sync().values_list("id", flat=True)
    return [enqueue_account_sync(account_id) for account_id in account_ids]


# -- Catalog enrichment ------------------------------------------------------


@app.periodic(cron=settings.SIMKL_METADATA_CRON)
@app.task(name="periodic_simkl_metadata_refresh", queueing_lock="periodic_simkl_metadata_refresh")
def periodic_simkl_metadata_refresh(timestamp: int | None = None):
    """Refresh community data for tracked titles, oldest first, a slice per run."""
    if not catalog_configured():
        return 0
    client = build_client()
    refreshed = 0
    for media in stale_media_queryset(limit=settings.SIMKL_METADATA_BATCH_SIZE):
        try:
            refresh_media_info(media, client=client)
        except SimklRateLimited:
            break
        except SimklError:
            continue
        refreshed += 1
    return refreshed
