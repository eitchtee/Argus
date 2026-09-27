"""Record MDBList-only local changes (ratings) as sync intents.

Watchlists, history and dropped shows are fanned out by
:func:`apps.sync.changes.record_intent`; MDBList has no paused state, so
ratings are the only extra kind.
"""

from apps.mdblist.identities import identity_key_for_payload
from apps.mdblist.models import MdblistAccount, MdblistSyncIntent


def record_mdblist_intent(user, kind: str, payload: dict, *, desired: bool = True):
    from apps.sync.changes import _record_provider_intent, local_intents_suppressed

    if local_intents_suppressed():
        return None
    if user is None or getattr(user, "pk", None) is None:
        return None
    if not MdblistAccount.objects.filter(user_id=user.pk).exists():
        return None
    kind = str(kind)
    return _record_provider_intent(
        MdblistSyncIntent,
        user,
        kind,
        identity_key_for_payload(kind, payload),
        payload,
        desired,
    )


def record_rating_intent(user, media, *, score) -> None:
    """Queue a rating change for a movie or show; episode ratings stay local."""
    from apps.movies.models import Movie
    from apps.sync.identities import movie_payload, show_payload
    from apps.tv.models import Show

    if isinstance(media, Movie):
        kind = MdblistSyncIntent.Kind.MOVIE_RATING
        payload = movie_payload(media)
    elif isinstance(media, Show):
        kind = MdblistSyncIntent.Kind.SHOW_RATING
        payload = show_payload(media)
    else:
        return
    if score is None:
        record_mdblist_intent(user, kind, payload, desired=False)
        return
    payload["rating"] = mdblist_rating_from_score(score)
    record_mdblist_intent(user, kind, payload, desired=True)


def mdblist_rating_from_score(score) -> int:
    """Half stars (0.5-5) map losslessly onto the MDBList 1-10 scale."""
    value = int(round(float(score) * 2))
    return min(10, max(1, value))
