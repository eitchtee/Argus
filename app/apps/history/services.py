from dataclasses import dataclass
from datetime import datetime

from django.core.paginator import Paginator
from django.db.models import F

from apps.movies.models import UserMovie
from apps.tv.models import UserEpisode


HISTORY_PAGE_SIZE = 25


@dataclass(frozen=True)
class HistoryEntry:
    kind: str
    record: UserMovie | UserEpisode
    watched_at: datetime | None


def get_history_page(user, page_number, per_page=HISTORY_PAGE_SIZE):
    movie_entries = (
        UserMovie.objects.filter(
            user=user,
            is_seen=True,
        )
        .select_related("movie")
        .order_by(
            F("seen_at").desc(nulls_last=True),
            "id",
        )
    )
    episode_entries = (
        UserEpisode.objects.filter(user=user)
        .select_related("episode__show")
        .order_by(
            F("seen_at").desc(nulls_last=True),
            "id",
        )
    )

    movie_count = movie_entries.count()
    episode_count = episode_entries.count()
    total = movie_count + episode_count
    paginator = Paginator(range(total), per_page)
    page = paginator.get_page(page_number)
    offset = (page.number - 1) * per_page
    limit = offset + per_page
    movie_start = max(0, offset - episode_count)
    episode_start = max(0, offset - movie_count)
    entries = [
        HistoryEntry("movie", row, row.seen_at)
        for row in movie_entries[movie_start:limit]
    ] + [
        HistoryEntry("episode", row, row.seen_at)
        for row in episode_entries[episode_start:limit]
    ]
    candidate_offset = offset - movie_start - episode_start
    page.object_list = sorted(
        entries,
        key=lambda entry: (
            entry.watched_at is None,
            -(entry.watched_at.timestamp() if entry.watched_at else 0),
            entry.kind,
            entry.record.pk,
        ),
    )[candidate_offset : candidate_offset + per_page]
    return page
