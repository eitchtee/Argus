"""Stamp SIMKL accounts with the last time their user touched Argus.

SIMKL's API rules forbid background polling without user interaction, so the
periodic sync only considers accounts whose user has been around recently.
The stamp is written at most once per interval to keep the cost to a cache
lookup on ordinary requests. Coming back after an idle spell counts as the
"sync on startup" trigger SIMKL recommends: a sync is queued right away so
changes made on SIMKL meanwhile show up on the first page load.
"""

from datetime import timedelta

from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

from apps.simkl.models import SimklAccount


STAMP_INTERVAL_SECONDS = 15 * 60


class SimklActivityMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        user = getattr(request, "user", None)
        if user is not None and user.is_authenticated:
            touch_account(user.pk)
        return response


def touch_account(user_id: int) -> None:
    key = f"simkl:seen:{user_id}"
    if cache.get(key):
        return
    cache.set(key, True, STAMP_INTERVAL_SECONDS)
    account = (
        SimklAccount.objects.filter(user_id=user_id)
        .only("id", "last_seen_at", "sync_status")
        .first()
    )
    if account is None:
        return
    now = timezone.now()
    idle_cutoff = now - timedelta(hours=settings.SIMKL_IDLE_HOURS)
    was_idle = account.last_seen_at is None or account.last_seen_at < idle_cutoff
    SimklAccount.objects.filter(id=account.id).update(last_seen_at=now)
    if was_idle and account.sync_status != SimklAccount.SyncStatus.REAUTHORIZE:
        from apps.simkl.config import is_configured
        from apps.simkl.tasks import enqueue_account_sync

        if is_configured():
            enqueue_account_sync(account.id)
