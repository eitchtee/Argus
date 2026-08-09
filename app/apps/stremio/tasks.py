from datetime import timedelta

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db.models import Q
from django.utils import timezone
from procrastinate import jobs
from procrastinate.contrib.django import app
from procrastinate.contrib.django.models import ProcrastinateJob
from procrastinate.exceptions import AlreadyEnqueued
from procrastinate.utils import async_to_sync

from apps.stremio.client import StremioAPIError, StremioClient
from apps.stremio.models import StremioAccount
from apps.stremio.sync import sync_account


_SYNC_TASK_NAME = "sync_stremio_account"
_STALLED_WORKER_TIMEOUT = timedelta(seconds=30)


def build_client(account) -> StremioClient:
    if not account.auth_key:
        raise ImproperlyConfigured("Stremio authorization is missing; reconnect the account.")
    return StremioClient(account.auth_key)


def _finish_stalled_job(job_id: int) -> None:
    async_to_sync(
        app.job_manager.finish_job_by_id_async,
        job_id=job_id,
        status=jobs.Status.ABORTED,
        delete_job=True,
    )


def _recover_stalled_account_sync(account_id: int) -> int | None:
    lock = f"stremio-account:{account_id}"
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
    lock = f"stremio-account:{account_id}"
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
    except (StremioAPIError, ImproperlyConfigured) as exc:
        status = (
            StremioAccount.SyncStatus.REAUTHORIZE
            if isinstance(exc, ImproperlyConfigured) or exc.code in {101, 401, 403}
            else StremioAccount.SyncStatus.ERROR
        )
        StremioAccount.objects.filter(id=account_id).update(
            sync_status=status,
            last_error=str(exc),
            updated_at=timezone.now(),
        )
        if status == StremioAccount.SyncStatus.ERROR:
            raise
        return None

    if report.warnings:
        message = "; ".join(report.warnings)
        StremioAccount.objects.filter(id=account_id).update(
            sync_status=StremioAccount.SyncStatus.ERROR,
            last_error=message,
            updated_at=timezone.now(),
        )
    else:
        StremioAccount.objects.filter(id=account_id).update(
            sync_status=StremioAccount.SyncStatus.OK,
            last_error="",
            last_synced_at=timezone.now(),
            initial_sync_complete=True,
            updated_at=timezone.now(),
        )
    return report


@app.periodic(cron=settings.STREMIO_SYNC_CRON)
@app.task(name="periodic_stremio_sync")
def periodic_stremio_sync(timestamp: int | None = None):
    account_ids = StremioAccount.objects.exclude(
        sync_status=StremioAccount.SyncStatus.REAUTHORIZE
    ).values_list("id", flat=True)
    return [enqueue_account_sync(account_id) for account_id in account_ids]
