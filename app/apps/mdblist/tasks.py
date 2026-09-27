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

from apps.mdblist.client import (
    MdblistAuthenticationError,
    MdblistError,
    MdblistRateLimited,
)
from apps.mdblist.config import (  # noqa: F401 - re-exported for callers
    MdblistConfigurationError,
    build_client,
    catalog_configured,
    oauth_client,
    refresh_account_token,
)
from apps.mdblist.enrichment import refresh_media_batch, stale_media_queryset
from apps.mdblist.models import MdblistAccount, MdblistSyncIntent
from apps.mdblist.sync import sync_account


_SYNC_TASK_NAME = "sync_mdblist_account"
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
    lock = f"mdblist-account:{account_id}"
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
    lock = f"mdblist-account:{account_id}"
    options = {"lock": lock, "queueing_lock": lock}
    if schedule_in is not None:
        options["schedule_in"] = schedule_in
    try:
        return sync_account_task.configure(**options).defer(account_id=account_id)
    except AlreadyEnqueued:
        return _recover_stalled_account_sync(account_id)


def _mark(account_id: int, status: str, error: str) -> None:
    MdblistAccount.objects.filter(id=account_id).update(
        sync_status=status,
        last_error=error,
        updated_at=timezone.now(),
    )


@app.task(name=_SYNC_TASK_NAME)
def sync_account_task(account_id: int):
    try:
        try:
            report = sync_account(account_id, client_factory=build_client)
        except MdblistAuthenticationError:
            # An OAuth token can be revoked or expire early; a refresh token
            # usually still works, so try once before asking the user.
            account = MdblistAccount.objects.get(id=account_id)
            if account.auth_method != MdblistAccount.AuthMethod.OAUTH or not account.refresh_token:
                raise
            refresh_account_token(account, oauth_client(account.access_token))
            report = sync_account(account_id, client_factory=build_client)
    except MdblistRateLimited as exc:
        _mark(
            account_id,
            MdblistAccount.SyncStatus.ERROR,
            f"MDBList request limit reached; retrying in {exc.retry_after} seconds.",
        )
        enqueue_account_sync(account_id, schedule_in={"seconds": exc.retry_after})
        return None
    except MdblistAuthenticationError:
        _mark(
            account_id,
            MdblistAccount.SyncStatus.REAUTHORIZE,
            "MDBList rejected the stored credentials; reconnect the account.",
        )
        return None
    except MdblistConfigurationError as exc:
        _mark(account_id, MdblistAccount.SyncStatus.ERROR, str(exc))
        return None
    except ImproperlyConfigured:
        _mark(
            account_id,
            MdblistAccount.SyncStatus.REAUTHORIZE,
            "MDBList credentials cannot be decrypted; reconnect the account.",
        )
        return None
    except MdblistError as exc:
        _mark(account_id, MdblistAccount.SyncStatus.ERROR, str(exc))
        raise

    MdblistAccount.objects.filter(id=account_id).update(
        sync_status=MdblistAccount.SyncStatus.OK,
        last_error="",
        last_synced_at=timezone.now(),
        updated_at=timezone.now(),
    )
    return report


def accounts_due_for_sync():
    """Accounts worth polling: recently active users, or pending local changes.

    Every MDBList key has a daily request quota, so an account whose user has
    been away longer than ``MDBLIST_IDLE_HOURS`` waits until they are back
    (or press Sync now), unless there is something of theirs to push.
    """
    idle_cutoff = timezone.now() - timedelta(hours=settings.MDBLIST_IDLE_HOURS)
    pending = MdblistSyncIntent.objects.filter(user_id=OuterRef("user_id"))
    return (
        MdblistAccount.objects.exclude(sync_status=MdblistAccount.SyncStatus.REAUTHORIZE)
        .annotate(has_pending=Exists(pending))
        .filter(Q(last_seen_at__gte=idle_cutoff) | Q(has_pending=True))
    )


@app.periodic(cron=settings.MDBLIST_SYNC_CRON)
@app.task(name="periodic_mdblist_sync")
def periodic_mdblist_sync(timestamp: int | None = None):
    account_ids = accounts_due_for_sync().values_list("id", flat=True)
    return [enqueue_account_sync(account_id) for account_id in account_ids]


# -- Catalog enrichment ------------------------------------------------------


@app.periodic(cron=settings.MDBLIST_METADATA_CRON)
@app.task(name="periodic_mdblist_metadata_refresh", queueing_lock="periodic_mdblist_metadata_refresh")
def periodic_mdblist_metadata_refresh(timestamp: int | None = None):
    """Refresh ratings for tracked titles, oldest first, a slice per run.

    Titles go out in batches, so a whole slice costs a handful of requests.
    """
    if not catalog_configured():
        return 0
    client = build_client()
    try:
        return refresh_media_batch(
            stale_media_queryset(limit=settings.MDBLIST_METADATA_BATCH_SIZE),
            client=client,
        )
    except MdblistRateLimited:
        return 0
