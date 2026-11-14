"""Argus score: one 0-100 number combined from every rating we sync.

Each source is put on a 0-100 scale and weighted by how much it is trusted and
by how many votes stand behind it, then the total is pulled towards a neutral
prior so a title with a handful of votes can't top the charts. It is the same
idea as IMDb's weighted rating.
"""

from django.contrib.contenttypes.models import ContentType

# source -> (multiplier to reach 0-100, trust weight, votes for half confidence).
# Critic sources count reviews, not users, so they reach confidence much sooner.
# The Rotten Tomatoes tomatometer is a % of positive reviews, not a score, and
# MDBList's own score already blends the others, so both are left out.
SOURCES = {
    "imdb": (10, 1.0, 5000),
    "letterboxd": (20, 1.0, 1000),
    "metacritic": (1, 1.0, 10),
    "myanimelist": (10, 1.0, 1000),
    "popcorn": (1, 0.75, 1000),
    "tmdb": (1, 0.75, 500),  # 0-100, as MDBList sends it
    "trakt": (1, 0.75, 500),
    "metacriticuser": (10, 0.5, 200),
    "simkl": (10, 0.5, 500),
    "rogerebert": (25, 0.5, 1),
}
PRIOR = 65.0  # where a title with no trustworthy votes lands
PRIOR_WEIGHT = 0.5  # how hard the prior pulls, in trust-weight units
UNKNOWN_VOTES_CONFIDENCE = 0.5


def aggregate(ratings: dict[str, tuple[float, int | None]]) -> int | None:
    """``{source: (value on the source's own scale, votes)}`` -> 0-100 score."""
    total = weights = 0.0
    for source, (value, votes) in ratings.items():
        if source not in SOURCES or value is None:
            continue
        scale, trust, half = SOURCES[source]
        confidence = UNKNOWN_VOTES_CONFIDENCE if votes is None else votes / (votes + half)
        weight = trust * confidence
        total += weight * min(max(value * scale, 0), 100)
        weights += weight
    if not weights:
        return None
    return round((total + PRIOR_WEIGHT * PRIOR) / (weights + PRIOR_WEIGHT))


def collect_ratings(media) -> dict[str, tuple[float, int | None]]:
    """Every synced rating for a stored movie or show, one value per source."""
    from apps.mdblist.models import MdblistMediaInfo
    from apps.simkl.models import SimklMediaInfo

    target = {
        "content_type": ContentType.objects.get_for_model(type(media)),
        "object_id": media.pk,
        "status": "ok",
    }
    ratings = {}
    # TVDB's "score" is a popularity count, not a rating, so only TMDB's counts.
    if getattr(media, "provider", None) == "tmdb" and getattr(media, "vote_average", None):
        ratings["tmdb"] = (media.vote_average * 10, media.vote_count)
    simkl = SimklMediaInfo.objects.filter(**target).first()
    if simkl:
        if simkl.simkl_rating:
            ratings["simkl"] = (simkl.simkl_rating, simkl.simkl_votes)
        if simkl.imdb_rating:
            ratings["imdb"] = (simkl.imdb_rating, simkl.imdb_votes)
    mdblist = MdblistMediaInfo.objects.filter(**target).first()
    # MDBList last: it is refreshed more often, so its values win on overlap.
    for rating in mdblist.ratings if mdblist else []:
        if rating.get("value") is not None:
            ratings[rating["source"]] = (rating["value"], rating.get("votes"))
    return ratings


def refresh_aggregate_score(media) -> int | None:
    """Recompute and store the score; call after any of its inputs change."""
    score = aggregate(collect_ratings(media))
    if score != media.aggregate_score:
        media.aggregate_score = score
        type(media).objects.filter(pk=media.pk).update(aggregate_score=score)
    return score
