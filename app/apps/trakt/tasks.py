from datetime import timedelta

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.utils import timezone
from procrastinate.contrib.django import app
from procrastinate.exceptions import AlreadyEnqueued

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


def enqueue_account_sync(account_id: int, *, schedule_in: dict | None = None) -> int | None:
    lock = f"trakt-account:{account_id}"
    options = {"lock": lock, "queueing_lock": lock}
    if schedule_in is not None:
        options["schedule_in"] = schedule_in
    try:
        return sync_account_task.configure(**options).defer(account_id=account_id)
    except AlreadyEnqueued:
        return None


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
