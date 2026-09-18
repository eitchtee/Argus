"""Record SIMKL-only local changes (paused shows and ratings) as sync intents.

The kinds SIMKL shares with Trakt and Stremio -- watchlists, history and
dropped shows -- are fanned out by :func:`apps.trakt.changes.record_intent`;
this module covers the two extra kinds SIMKL can express.
"""

from apps.simkl.identities import identity_key_for_payload
from apps.simkl.models import SimklAccount, SimklSyncIntent


def record_simkl_intent(user, kind: str, payload: dict, *, desired: bool = True):
    from apps.trakt.changes import _record_provider_intent, local_intents_suppressed

    if local_intents_suppressed():
        return None
    if user is None or getattr(user, "pk", None) is None:
        return None
    if not SimklAccount.objects.filter(user_id=user.pk).exists():
        return None
    kind = str(kind)
    return _record_provider_intent(
        SimklSyncIntent,
        user,
        kind,
        identity_key_for_payload(kind, payload),
        payload,
        desired,
    )


def record_rating_intent(user, media, *, score) -> None:
    """Queue a rating change for a movie or show; episodes have no SIMKL rating."""
    from apps.movies.models import Movie
    from apps.trakt.identities import movie_payload, show_payload
    from apps.tv.models import Show

    if isinstance(media, Movie):
        kind = SimklSyncIntent.Kind.MOVIE_RATING
        payload = movie_payload(media)
    elif isinstance(media, Show):
        kind = SimklSyncIntent.Kind.SHOW_RATING
        payload = show_payload(media)
    else:
        return
    if score is None:
        record_simkl_intent(user, kind, payload, desired=False)
        return
    payload["rating"] = simkl_rating_from_score(score)
    record_simkl_intent(user, kind, payload, desired=True)


def simkl_rating_from_score(score) -> int:
    """Half stars (0.5-5) map losslessly onto the SIMKL 1-10 scale."""
    value = int(round(float(score) * 2))
    return min(10, max(1, value))
