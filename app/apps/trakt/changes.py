from contextlib import contextmanager
from contextvars import ContextVar

from django.db import transaction

from apps.trakt.identities import (
    identity_key_for_payload,
    latest_timestamp_from_payload,
)
from apps.trakt.models import TraktAccount, TraktSyncIntent


_LOCAL_INTENTS_SUPPRESSED = ContextVar("trakt_local_intents_suppressed", default=False)


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
    if TraktAccount.objects.filter(user_id=user.pk).exists():
        intents.append(
            _record_provider_intent(
                TraktSyncIntent,
                user,
                kind,
                identity_key,
                payload,
                desired,
            )
        )

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
        TraktSyncIntent.Kind.MOVIE_HISTORY,
        TraktSyncIntent.Kind.EPISODE_HISTORY,
    }:
        return incoming

    existing_timestamp = latest_timestamp_from_payload(existing)
    incoming_timestamp = latest_timestamp_from_payload(incoming)
    if existing_timestamp is None or (
        incoming_timestamp is not None and incoming_timestamp >= existing_timestamp
    ):
        return incoming
    return existing
