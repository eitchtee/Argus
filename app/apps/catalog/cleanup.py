"""Removes catalog titles nobody uses.

Movies and shows are imported on demand (tracking, provider syncs, imports)
and shared by every user. Once nobody tracks a title and nobody has watched,
rated or customised it, the row is dead weight: the nightly refreshes skip it
and nothing links to it. Seasons and episodes go with their show.
"""

from datetime import timedelta

from django.contrib.contenttypes.models import ContentType
from django.db import transaction
from django.db.models import Exists, OuterRef
from django.utils import timezone

from apps.catalog.models import MediaArtwork, MediaRating, UserMediaArtworkPreference
from apps.mdblist.models import MdblistMediaInfo
from apps.movies.models import Movie, UserMovie
from apps.simkl.models import SimklMediaInfo
from apps.tv.models import Episode, Show, UserEpisode, UserShow

# Titles touched recently are left alone: tracking imports the title before it
# creates the user's row, and a refresh in flight should not race a delete.
GRACE_PERIOD = timedelta(days=7)
BATCH_SIZE = 200


def unused_movies(cutoff):
    movie_type = ContentType.objects.get_for_model(Movie)
    return (
        Movie.objects.filter(updated_at__lt=cutoff)
        .exclude(Exists(UserMovie.objects.filter(movie=OuterRef("pk"))))
        .exclude(_rated(movie_type))
        .exclude(_artwork_preference(MediaArtwork.MediaType.MOVIE))
    )


def unused_shows(cutoff):
    show_type = ContentType.objects.get_for_model(Show)
    episode_type = ContentType.objects.get_for_model(Episode)
    rated_episodes = Episode.objects.filter(show=OuterRef("pk")).filter(_rated(episode_type))
    return (
        Show.objects.filter(updated_at__lt=cutoff)
        .exclude(Exists(UserShow.objects.filter(show=OuterRef("pk"))))
        .exclude(Exists(UserEpisode.objects.filter(episode__show=OuterRef("pk"))))
        .exclude(_rated(show_type))
        .exclude(Exists(rated_episodes))
        .exclude(_artwork_preference(MediaArtwork.MediaType.TV))
    )


def remove_unused_titles(*, now=None) -> dict[str, int]:
    cutoff = (now or timezone.now()) - GRACE_PERIOD
    return {
        "movies": _remove(Movie, unused_movies, cutoff, MediaArtwork.MediaType.MOVIE),
        "shows": _remove(Show, unused_shows, cutoff, MediaArtwork.MediaType.TV),
    }


def _remove(model, unused, cutoff, artwork_media_type) -> int:
    content_type = ContentType.objects.get_for_model(model)
    removed = 0
    ids = list(unused(cutoff).values_list("id", flat=True))
    for start in range(0, len(ids), BATCH_SIZE):
        with transaction.atomic():
            # Re-applying the filter keeps anything tracked since the scan.
            rows = list(
                unused(cutoff)
                .filter(id__in=ids[start : start + BATCH_SIZE])
                .only("id", "provider", "external_id")
            )
            if not rows:
                continue
            row_ids = [row.id for row in rows]
            # Generic relations without a GenericRelation do not cascade.
            for info_model in (SimklMediaInfo, MdblistMediaInfo):
                info_model.objects.filter(content_type=content_type, object_id__in=row_ids).delete()
            for row in rows:
                MediaArtwork.objects.filter(
                    provider=row.provider,
                    media_type=artwork_media_type,
                    external_id=row.external_id,
                ).delete()
            model.objects.filter(id__in=row_ids).delete()
            removed += len(rows)
    return removed


def _rated(content_type):
    return Exists(
        MediaRating.objects.filter(content_type=content_type, object_id=OuterRef("pk"))
    )


def _artwork_preference(media_type):
    return Exists(
        UserMediaArtworkPreference.objects.filter(
            provider=OuterRef("provider"),
            media_type=media_type,
            external_id=OuterRef("external_id"),
        )
    )
