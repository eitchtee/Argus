from contextlib import contextmanager
from contextvars import ContextVar

from django.db import models, transaction

from apps.sync.identities import (
    identity_key_for_payload,
    latest_timestamp_from_payload,
)


class IntentKind(models.TextChoices):
    """Library changes every sync provider understands."""

    MOVIE_WATCHLIST = "movie_watchlist", "Movie watchlist"
    SHOW_WATCHLIST = "show_watchlist", "Show watchlist"
    MOVIE_HISTORY = "movie_history", "Movie history"
    EPISODE_HISTORY = "episode_history", "Episode history"
    SHOW_DROPPED = "show_dropped", "Dropped show"


_LOCAL_INTENTS_SUPPRESSED = ContextVar("local_intents_suppressed", default=False)


def local_intents_suppressed() -> bool:
    return _LOCAL_INTENTS_SUPPRESSED.get()


@contextmanager
def suppress_local_intents():
    token = _LOCAL_INTENTS_SUPPRESSED.set(True)
    try:
        yield
    finally:
        _LOCAL_INTENTS_SUPPRESSED.reset(token)


def record_intent(user, kind: str, payload: dict, *, desired: bool = True):
    if _LOCAL_INTENTS_SUPPRESSED.get():
        return None
    kind = str(kind)
    identity_key = identity_key_for_payload(kind, payload)
    intents = []

    from apps.stremio.models import StremioAccount, StremioSyncIntent

    if (
        kind in {
            StremioSyncIntent.Kind.MOVIE_WATCHLIST,
            StremioSyncIntent.Kind.SHOW_WATCHLIST,
            StremioSyncIntent.Kind.MOVIE_HISTORY,
            StremioSyncIntent.Kind.EPISODE_HISTORY,
        }
        and StremioAccount.objects.filter(user_id=user.pk).exists()
    ):
        intents.append(
            _record_provider_intent(
                StremioSyncIntent,
                user,
                kind,
                identity_key,
                payload,
                desired,
            )
        )
    from apps.simkl.models import SimklAccount, SimklSyncIntent

    if (
        kind in {
            SimklSyncIntent.Kind.MOVIE_WATCHLIST,
            SimklSyncIntent.Kind.SHOW_WATCHLIST,
            SimklSyncIntent.Kind.MOVIE_HISTORY,
            SimklSyncIntent.Kind.EPISODE_HISTORY,
            SimklSyncIntent.Kind.SHOW_DROPPED,
        }
        and SimklAccount.objects.filter(user_id=user.pk).exists()
    ):
        intents.append(
            _record_provider_intent(
                SimklSyncIntent,
                user,
                kind,
                identity_key,
                payload,
                desired,
            )
        )
    return intents[0] if intents else None


def _record_provider_intent(
    model,
    user,
    kind: str,
    identity_key: str,
    payload: dict,
    desired: bool,
):
    with transaction.atomic():
        intent = (
            model.objects.select_for_update()
            .filter(user=user, kind=kind, identity_key=identity_key)
            .first()
        )
        if intent is None:
            return model.objects.create(
                user=user,
                kind=kind,
                identity_key=identity_key,
                payload=payload,
                desired=desired,
            )

        intent.payload = _merge_payload(intent.payload, payload, kind=kind)
        intent.desired = desired
        intent.save(update_fields=["payload", "desired", "updated_at"])
        return intent


def _merge_payload(existing: dict, incoming: dict, *, kind: str) -> dict:
    if kind not in {
        IntentKind.MOVIE_HISTORY,
        IntentKind.EPISODE_HISTORY,
    }:
        return incoming

    existing_timestamp = latest_timestamp_from_payload(existing)
    incoming_timestamp = latest_timestamp_from_payload(incoming)
    if existing_timestamp is None or (
        incoming_timestamp is not None and incoming_timestamp >= existing_timestamp
    ):
        return incoming
    return existing
