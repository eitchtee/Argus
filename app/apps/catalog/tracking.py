from dataclasses import dataclass

from django.apps import apps

from apps.catalog.providers.base import SearchResultDTO


@dataclass(frozen=True)
class TrackingMatch:
    provider: str
    external_id: str
    same_provider: bool


def identity_keys(
    provider: str,
    external_id: str,
    *,
    tmdb_id: str | None = None,
    tvdb_id: str | None = None,
    imdb_id: str | None = None,
) -> set[tuple[str, str]]:
    keys = {(provider, str(external_id))}
    if tmdb_id:
        keys.add(("tmdb", str(tmdb_id)))
    if tvdb_id:
        keys.add(("tvdb", str(tvdb_id)))
    if imdb_id:
        keys.add(("imdb", str(imdb_id)))
    return keys


def find_tracking_match(
    user,
    media_type: str,
    *,
    provider: str,
    external_id: str,
    tmdb_id: str | None = None,
    tvdb_id: str | None = None,
    imdb_id: str | None = None,
) -> TrackingMatch | None:
    records = _tracked_records(user, media_type)
    return _find_tracking_match(
        records,
        provider=provider,
        external_id=external_id,
        tmdb_id=tmdb_id,
        tvdb_id=tvdb_id,
        imdb_id=imdb_id,
    )


def tracking_matches(
    user,
    media_type: str,
    results: list[SearchResultDTO],
) -> dict[tuple[str, str], TrackingMatch | None]:
    records = _tracked_records(user, media_type)
    return {
        (result.provider, result.external_id): _find_tracking_match(
            records,
            provider=result.provider,
            external_id=result.external_id,
            tmdb_id=getattr(result, "tmdb_id", None),
            tvdb_id=getattr(result, "tvdb_id", None),
            imdb_id=getattr(result, "imdb_id", None),
        )
        for result in results
    }


def tracked_keys(user, media_type: str, results: list[SearchResultDTO]) -> set[tuple[str, str]]:
    matches = tracking_matches(user, media_type, results)
    return {
        key
        for key, match in matches.items()
        if match is not None and match.same_provider
    }


def _find_tracking_match(
    records,
    *,
    provider: str,
    external_id: str,
    tmdb_id: str | None,
    tvdb_id: str | None,
    imdb_id: str | None,
) -> TrackingMatch | None:
    exact = next(
        (
            record
            for record in records
            if record.provider == provider and record.external_id == str(external_id)
        ),
        None,
    )
    if exact is not None:
        return TrackingMatch(
            provider=exact.provider,
            external_id=exact.external_id,
            same_provider=True,
        )

    requested_keys = identity_keys(
        provider,
        external_id,
        tmdb_id=tmdb_id,
        tvdb_id=tvdb_id,
        imdb_id=imdb_id,
    )
    for record in records:
        if requested_keys.intersection(_record_identity_keys(record)):
            return TrackingMatch(
                provider=record.provider,
                external_id=record.external_id,
                same_provider=record.provider == provider,
            )
    return None


def _record_identity_keys(record) -> set[tuple[str, str]]:
    return identity_keys(
        record.provider,
        record.external_id,
        tmdb_id=getattr(record, "tmdb_id", None),
        tvdb_id=getattr(record, "tvdb_id", None),
        imdb_id=getattr(record, "imdb_id", None),
    )


def _tracked_records(user, media_type: str):
    model_name = {"movie": ("movies", "Movie"), "tv": ("tv", "Show")}.get(media_type)
    if model_name is None:
        return []

    try:
        model = apps.get_model(*model_name)
    except LookupError:
        return []

    return list(
        model.objects.filter(user_states__user=user)
        .distinct()
        .order_by("id")
    )
