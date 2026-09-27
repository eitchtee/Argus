"""Stamp MDBList accounts with the last time their user touched Argus.

Every MDBList key has a daily request quota, so the periodic sync only
considers accounts whose user has been around recently. The stamp is written
at most once per interval to keep the cost to a cache lookup on ordinary
requests. Coming back after an idle spell queues a sync right away, so
changes made on MDBList meanwhile show up on the first page load.
"""

from datetime import timedelta

from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

from apps.mdblist.models import MdblistAccount


STAMP_INTERVAL_SECONDS = 15 * 60


class MdblistActivityMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        user = getattr(request, "user", None)
        if user is not None and user.is_authenticated:
            touch_account(user.pk)
        return response


def touch_account(user_id: int) -> None:
    key = f"mdblist:seen:{user_id}"
    if cache.get(key):
        return
    cache.set(key, True, STAMP_INTERVAL_SECONDS)
    account = (
        MdblistAccount.objects.filter(user_id=user_id)
        .only("id", "last_seen_at", "sync_status")
        .first()
    )
    if account is None:
        return
    now = timezone.now()
    idle_cutoff = now - timedelta(hours=settings.MDBLIST_IDLE_HOURS)
    was_idle = account.last_seen_at is None or account.last_seen_at < idle_cutoff
    MdblistAccount.objects.filter(id=account.id).update(last_seen_at=now)
    if was_idle and account.sync_status != MdblistAccount.SyncStatus.REAUTHORIZE:
        from apps.mdblist.tasks import enqueue_account_sync

        enqueue_account_sync(account.id)
