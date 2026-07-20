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
    if not TraktAccount.objects.filter(user_id=user.pk).exists():
        return None

    kind = str(kind)
    identity_key = identity_key_for_payload(kind, payload)
    with transaction.atomic():
        intent = (
            TraktSyncIntent.objects.select_for_update()
            .filter(user=user, kind=kind, identity_key=identity_key)
            .first()
        )
        if intent is None:
            return TraktSyncIntent.objects.create(
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
