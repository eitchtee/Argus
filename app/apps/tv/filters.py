from collections import Counter

from django.db.models import Max
from django.utils.translation import gettext_lazy as _

from apps.catalog.artwork import localized_media_records
from apps.catalog.library_filters import (
    attach_genre_names,
    pick,
    pick_sort,
    sorted_nulls_last,
    title_key,
)
from apps.tv.models import Show, UserEpisode
from apps.tv.services import get_watchlist_shows


CONDITION_CHOICES = (
    ("watching", _("Watching")),
    ("completed", _("Completed")),
    ("paused", _("Paused")),
    ("dropped", _("Dropped")),
    ("all", _("All")),
)
STATUS_CHOICES = (
    (Show.NormalizedStatus.CONTINUING, _("Continuing")),
    (Show.NormalizedStatus.ENDED, _("Ended")),
    (Show.NormalizedStatus.UPCOMING, _("Upcoming")),
)
SORT_CHOICES = (
    ("title", _("Title")),
    ("watched", _("Recently watched")),
    ("added", _("Recently added")),
    ("airing", _("Airing next")),
)


def filter_watchlist(user, params):
    """Return the template context for the TV watchlist grid and its filters.

    Tab counts reflect the genre/status filters so each tab shows what
    clicking it would reveal.
    """
    condition = pick(params, "condition", dict(CONDITION_CHOICES), "watching")
    status = pick(params, "status", dict(STATUS_CHOICES))
    sort = pick_sort(params, SORT_CHOICES)

    shows = get_watchlist_shows(user, "all")
    genres = attach_genre_names(user, shows)
    genre = pick(params, "genre", genres)
    shows = [
        show
        for show in shows
        if (not status or show.normalized_status == status)
        and (not genre or genre in show.genre_names)
    ]
    counts = Counter(show.user_condition for show in shows)
    counts["all"] = len(shows)
    if condition != "all":
        shows = [show for show in shows if show.user_condition == condition]

    records = sorted(localized_media_records(shows, user), key=title_key)
    if sort == "watched":
        last_seen = dict(
            UserEpisode.objects.filter(user=user, episode__show__in=shows)
            .values("episode__show_id")
            .annotate(last_seen=Max("seen_at"))
            .values_list("episode__show_id", "last_seen")
        )
        records = sorted_nulls_last(
            records, lambda record: last_seen.get(record.pk), reverse=True
        )
    elif sort == "added":
        records = sorted(
            records, key=lambda record: record.tracking_started_at, reverse=True
        )
    elif sort == "airing":
        records = sorted_nulls_last(records, lambda record: record.next_air_date)

    return {
        "shows": records,
        "conditions": [
            (value, label, counts[value]) for value, label in CONDITION_CHOICES
        ],
        "condition": condition,
        "statuses": STATUS_CHOICES,
        "status": status,
        "genres": genres,
        "genre": genre,
        "sorts": SORT_CHOICES,
        "sort": sort,
        "is_filtered": bool(status or genre),
    }
