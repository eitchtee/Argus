from collections import defaultdict
from decimal import Decimal, InvalidOperation

from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ObjectDoesNotExist
from django.urls import reverse

from apps.catalog.models import MediaRating

MIN_SCORE = Decimal("0.5")
MAX_SCORE = Decimal("5.0")
HALF_STEP = Decimal("0.5")
SCORE_QUANTUM = Decimal("0.1")


def parse_score(raw) -> Decimal:
    if raw is None:
        raise ValueError("A rating score is required.")
    try:
        score = Decimal(str(raw).strip()).quantize(SCORE_QUANTUM)
    except (InvalidOperation, ArithmeticError) as exc:
        raise ValueError("Invalid rating score.") from exc
    if score < MIN_SCORE or score > MAX_SCORE:
        raise ValueError("Rating must be between 0.5 and 5.")
    if score % HALF_STEP != 0:
        raise ValueError("Rating must use half star increments.")
    return score


def resolve_media(media_type: str, *, external_id: str, provider: str | None):
    media_type = (media_type or "").strip().lower()
    external_id = (external_id or "").strip()
    if not external_id:
        raise ObjectDoesNotExist("Media not found.")

    if media_type == MediaRating.MediaType.MOVIE:
        return _movie_model().objects.get(
            provider=provider or "tmdb",
            external_id=external_id,
        )

    if media_type == MediaRating.MediaType.SHOW:
        from apps.catalog.models import SyncStatus
        from apps.tv.models import Show

        # Shows are rateable from the very first visit, even before they
        # are tracked, so a minimal stub is created on demand and the
        # regular import pipeline fills in the metadata afterwards.
        show, _created = Show.objects.get_or_create(
            provider=provider or "tvdb",
            external_id=external_id,
            defaults={
                "name": external_id,
                "sync_status": SyncStatus.PENDING,
            },
        )
        return show

    if media_type == MediaRating.MediaType.EPISODE:
        try:
            episode_pk = int(external_id)
        except ValueError as exc:
            raise ObjectDoesNotExist("Media not found.") from exc
        return _episode_model().objects.get(id=episode_pk)

    raise ValueError("Unsupported media type.")


def format_score(score) -> str | None:
    """Render a score as a locale-independent dotted string for templates."""
    return str(score) if score is not None else None


def build_rating_url(media_type: str, external_id, provider: str | None = None) -> str:
    media_type = (media_type or "").strip().lower()
    url = reverse(
        "media-rating",
        kwargs={"media_type": media_type, "external_id": str(external_id)},
    )
    if media_type == MediaRating.MediaType.EPISODE:
        return url
    default_provider = (
        "tmdb" if media_type == MediaRating.MediaType.MOVIE else "tvdb"
    )
    if provider != default_provider:
        url = f"{url}?provider={provider or default_provider}"
    return url


def content_filter(media) -> dict:
    content_type = ContentType.objects.get_for_model(type(media))
    return {
        "content_type": content_type,
        "object_id": media.pk,
    }


def is_rateable(user, media) -> bool:
    if isinstance(media, _movie_model()):
        return _movie_model().objects.filter(
            pk=media.pk,
            user_states__user=user,
            user_states__is_seen=True,
        ).exists()

    if isinstance(media, _show_model()):
        # Shows are rateable from the beginning; no watched state required.
        return True

    if isinstance(media, _episode_model()):
        return _episode_model().objects.filter(
            pk=media.pk,
            user_states__user=user,
        ).exists()

    return False


def rate_media(user, media_type: str, media, score: Decimal) -> MediaRating:
    media_type = (media_type or "").strip().lower()
    if media_type not in MediaRating.MediaType.values:
        raise ValueError("Unsupported media type.")
    if not is_rateable(user, media):
        raise ValueError("Only watched media can be rated.")

    rating, _created = MediaRating.objects.update_or_create(
        user=user,
        **content_filter(media),
        defaults={
            "media_type": media_type,
            "score": score,
        },
    )
    return rating


def clear_rating(user, media) -> int:
    return MediaRating.objects.filter(
        user=user,
        **content_filter(media),
    ).delete()[0]


def get_user_rating(user, media) -> MediaRating | None:
    if user is None or getattr(user, "pk", None) is None:
        return None
    return MediaRating.objects.filter(user=user, **content_filter(media)).first()


def get_user_score(user, media) -> Decimal | None:
    rating = get_user_rating(user, media)
    return rating.score if rating else None


def attach_user_scores(user, media_items) -> None:
    """Stamp ``user_rating`` onto each media object with the viewer's score.

    Poster cards read the attribute to badge rated items; ``None`` means
    unrated. Ratings are fetched in one query per media type instead of one
    per card.
    """
    items = list(media_items)
    if user is None or getattr(user, "pk", None) is None or not items:
        return

    grouped = defaultdict(list)
    for media in items:
        content_type = ContentType.objects.get_for_model(type(media))
        grouped[content_type].append(media)

    for content_type, group in grouped.items():
        scores = dict(
            MediaRating.objects.filter(
                user=user,
                content_type=content_type,
                object_id__in=[media.pk for media in group],
            ).values_list("object_id", "score")
        )
        for media in group:
            media.user_rating = scores.get(media.pk)


def transfer_rating(user, *, source, target) -> None:
    """Move a user's rating from one media object onto another."""
    source_rating = get_user_rating(user, source)
    if source_rating is None:
        return
    target_content_type = ContentType.objects.get_for_model(type(target))
    existing_target = MediaRating.objects.filter(
        user=user,
        content_type=target_content_type,
        object_id=target.pk,
    ).first()
    source_rating.delete()
    if existing_target is not None:
        existing_target.score = source_rating.score
        existing_target.save(update_fields=["score", "updated_at"])
    else:
        MediaRating.objects.create(
            user=user,
            media_type=source_rating.media_type,
            content_type=target_content_type,
            object_id=target.pk,
            score=source_rating.score,
        )


def delete_ratings_for(user, *media_objects) -> int:
    deleted = 0
    for media in media_objects:
        deleted += MediaRating.objects.filter(
            user=user,
            **content_filter(media),
        ).delete()[0]
    return deleted


def delete_episode_ratings_for_show(user, show) -> int:
    episode_ids = list(show.episodes.values_list("id", flat=True))
    if not episode_ids:
        return 0
    return MediaRating.objects.filter(
        user=user,
        content_type=ContentType.objects.get_for_model(_episode_model()),
        object_id__in=episode_ids,
    ).delete()[0]


def _movie_model():
    from apps.movies.models import Movie

    return Movie


def _show_model():
    from apps.tv.models import Show

    return Show


def _episode_model():
    from apps.tv.models import Episode

    return Episode
