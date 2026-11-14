from collections import Counter
from decimal import Decimal

from django.utils.translation import gettext_lazy as _

from apps.catalog.artwork import localized_media_records
from apps.catalog.library_filters import (
    attach_genre_names,
    pick,
    pick_sort,
    sorted_nulls_last,
    title_key,
)
from apps.catalog.ratings import attach_user_scores
from apps.movies.models import Movie


RELEASE_CHOICES = (
    ("all", _("All")),
    (Movie.NormalizedStatus.RELEASED, _("Released")),
    (Movie.NormalizedStatus.UPCOMING, _("Upcoming")),
)
WATCHLIST_SORT_CHOICES = (
    ("added", _("Recently added")),
    ("title", _("Title")),
    ("release", _("Release date")),
)
WATCHED_SORT_CHOICES = (
    ("watched", _("Recently watched")),
    ("title", _("Title")),
    ("release", _("Release date")),
    ("rating", _("Your rating")),
)
RATING_CHOICES = (
    ("rated", _("Rated")),
    ("unrated", _("Unrated")),
    ("5", _("5 stars")),
    ("4", _("4+ stars")),
    ("3", _("3+ stars")),
    ("2", _("2+ stars")),
    ("1", _("1+ stars")),
)


def filter_watchlist(user, movies, params):
    """Context for the movie watchlist: release tabs, genre and sort.

    ``movies`` arrives ordered by when it was added to the watchlist.
    """
    release = pick(params, "release", dict(RELEASE_CHOICES), "all")
    context = _filter(user, movies, params, WATCHLIST_SORT_CHOICES)
    counts = Counter(record.normalized_status for record in context["movies"])
    counts["all"] = len(context["movies"])
    if release != "all":
        context["movies"] = [
            record
            for record in context["movies"]
            if record.normalized_status == release
        ]
    context["releases"] = [
        (value, label, counts[value]) for value, label in RELEASE_CHOICES
    ]
    context["release"] = release
    context["is_filtered"] |= release != "all"
    return context


def filter_watched(user, movies, params):
    """Context for the watched movies page; ``movies`` arrives newest-watched first."""
    rating = pick(params, "rating", dict(RATING_CHOICES))
    context = _filter(user, movies, params, WATCHED_SORT_CHOICES)
    if rating:
        context["movies"] = [
            record
            for record in context["movies"]
            if _rating_matches(record.user_rating, rating)
        ]
    context["ratings"] = RATING_CHOICES
    context["rating"] = rating
    context["is_filtered"] |= bool(rating)
    return context


def _rating_matches(score, rating):
    if rating == "unrated":
        return score is None
    if score is None:
        return False
    return rating == "rated" or Decimal(score) >= Decimal(rating)


def _filter(user, movies, params, sort_choices):
    sort = pick_sort(params, sort_choices)
    genres = attach_genre_names(user, movies)
    genre = pick(params, "genre", genres)
    if genre:
        movies = [movie for movie in movies if genre in movie.genre_names]

    records = localized_media_records(movies, user)
    attach_user_scores(user, [record.source for record in records])
    if sort == "title":
        records = sorted(records, key=title_key)
    elif sort == "release":
        records = sorted_nulls_last(
            records, lambda record: record.release_date, reverse=True
        )
    elif sort == "rating":
        records = sorted_nulls_last(
            records, lambda record: record.user_rating, reverse=True
        )

    return {
        "movies": records,
        "genres": genres,
        "genre": genre,
        "sorts": sort_choices,
        "sort": sort,
        "is_filtered": bool(genre),
    }
