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

from apps.trakt.client import (
    TraktAuthenticationError,
    TraktClient,
    TraktError,
    TraktRateLimited,
)
from apps.trakt.models import TraktAccount
from apps.trakt.sync import sync_account


class TraktConfigurationError(TraktError):
    pass


_SYNC_TASK_NAME = "sync_trakt_account"
_STALLED_WORKER_TIMEOUT = timedelta(seconds=30)


def build_client(account) -> TraktClient:
    if not all(
        (
            settings.TRAKT_CLIENT_ID,
            settings.TRAKT_CLIENT_SECRET,
            settings.TRAKT_REDIRECT_URI,
        )
    ):
        raise TraktConfigurationError(
            "Trakt client credentials and redirect URI are not configured "
            "by the server administrator."
        )

    client = TraktClient(
        account.access_token,
        client_id=settings.TRAKT_CLIENT_ID,
        client_secret=settings.TRAKT_CLIENT_SECRET,
        user_agent=settings.TRAKT_USER_AGENT,
    )
    refresh_cutoff = timezone.now() + timedelta(minutes=1)
    if account.token_expires_at is None or account.token_expires_at <= refresh_cutoff:
        try:
            token = client.refresh_access_token(
                account.refresh_token,
                settings.TRAKT_REDIRECT_URI,
            )
        except TraktAuthenticationError:
            account.sync_status = TraktAccount.SyncStatus.REAUTHORIZE
            account.last_error = "Trakt authorization expired; reconnect the account."
            account.save(update_fields=["sync_status", "last_error", "updated_at"])
            raise
        account.access_token = token.access_token
        if token.refresh_token:
            account.refresh_token = token.refresh_token
        account.token_expires_at = timezone.now() + timedelta(seconds=token.expires_in)
        account.sync_status = TraktAccount.SyncStatus.OK
        account.last_error = ""
        account.save(
            update_fields=[
                "access_token",
                "refresh_token",
                "token_expires_at",
                "sync_status",
                "last_error",
                "updated_at",
            ]
        )
        client = TraktClient(
            account.access_token,
            client_id=settings.TRAKT_CLIENT_ID,
            client_secret=settings.TRAKT_CLIENT_SECRET,
            user_agent=settings.TRAKT_USER_AGENT,
        )
    return client


def _finish_stalled_job(job_id: int) -> None:
    async_to_sync(
        app.job_manager.finish_job_by_id_async,
        job_id=job_id,
        status=jobs.Status.ABORTED,
        delete_job=True,
    )


def _recover_stalled_account_sync(account_id: int) -> int | None:
    """Release a dead worker's account lock and keep the newest queued sync."""
    lock = f"trakt-account:{account_id}"
    stalled_before = timezone.now() - _STALLED_WORKER_TIMEOUT
    stalled_jobs = list(
        ProcrastinateJob.objects.filter(
            task_name=_SYNC_TASK_NAME,
            lock=lock,
            status=jobs.Status.DOING.value,
        )
        .filter(
            Q(worker__isnull=True)
            | Q(worker__last_heartbeat__lt=stalled_before)
        )
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
    app.job_manager.retry_job_by_id(
        stalled_jobs[0].id,
        retry_at=timezone.now(),
    )
    return stalled_jobs[0].id


def enqueue_account_sync(account_id: int, *, schedule_in: dict | None = None) -> int | None:
    lock = f"trakt-account:{account_id}"
    options = {"lock": lock, "queueing_lock": lock}
    if schedule_in is not None:
        options["schedule_in"] = schedule_in
    try:
        return sync_account_task.configure(**options).defer(account_id=account_id)
    except AlreadyEnqueued:
        return _recover_stalled_account_sync(account_id)


@app.task(name="sync_trakt_account")
def sync_account_task(account_id: int):
    try:
        report = sync_account(
            account_id,
            client_factory=build_client,
        )
    except TraktRateLimited as exc:
        TraktAccount.objects.filter(id=account_id).update(
            sync_status=TraktAccount.SyncStatus.ERROR,
            last_error=f"Trakt rate limit; retrying in {exc.retry_after} seconds.",
            updated_at=timezone.now(),
        )
        enqueue_account_sync(
            account_id,
            schedule_in={"seconds": exc.retry_after},
        )
        return None
    except TraktAuthenticationError:
        TraktAccount.objects.filter(id=account_id).update(
            sync_status=TraktAccount.SyncStatus.REAUTHORIZE,
            last_error="Trakt authorization expired; reconnect the account.",
            updated_at=timezone.now(),
        )
        return None
    except TraktConfigurationError as exc:
        TraktAccount.objects.filter(id=account_id).update(
            sync_status=TraktAccount.SyncStatus.ERROR,
            last_error=str(exc),
            updated_at=timezone.now(),
        )
        return None
    except ImproperlyConfigured:
        TraktAccount.objects.filter(id=account_id).update(
            sync_status=TraktAccount.SyncStatus.REAUTHORIZE,
            last_error="Trakt tokens cannot be decrypted; reconnect the account.",
            updated_at=timezone.now(),
        )
        return None
    except TraktError as exc:
        TraktAccount.objects.filter(id=account_id).update(
            sync_status=TraktAccount.SyncStatus.ERROR,
            last_error=str(exc),
            updated_at=timezone.now(),
        )
        raise

    TraktAccount.objects.filter(id=account_id).update(
        sync_status=TraktAccount.SyncStatus.OK,
        last_error="",
        last_synced_at=timezone.now(),
        initial_sync_complete=True,
        updated_at=timezone.now(),
    )
    return report


@app.periodic(cron=settings.TRAKT_SYNC_CRON)
@app.task(name="periodic_trakt_sync")
def periodic_trakt_sync(timestamp: int | None = None):
    account_ids = TraktAccount.objects.exclude(
        sync_status=TraktAccount.SyncStatus.REAUTHORIZE
    ).values_list("id", flat=True)
    return [enqueue_account_sync(account_id) for account_id in account_ids]
