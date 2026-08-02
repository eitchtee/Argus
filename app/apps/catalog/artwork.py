from collections.abc import Iterable
from urllib.parse import urljoin

from django.db import transaction
from django.db.models import Q

from apps.catalog.localization import metadata_language_for_user
from apps.catalog.languages import language_codes_match
from apps.catalog.models import MediaArtwork, UserMediaArtworkPreference
from apps.catalog.providers.base import ArtworkDTO, DetailDTO
from apps.catalog.providers.tmdb import build_backdrop_url, build_poster_url


def sync_media_artworks(detail: DetailDTO, *, media_type: str) -> None:
    """Sync provider artwork, preserving the catalog on incomplete responses.

    ``None`` means the provider did not supply an artwork collection. Existing
    rows are preserved, while legacy poster and backdrop paths are still
    allowed to seed missing default rows. An empty list is a complete response
    and removes rows that no longer exist upstream.
    """
    complete_response = detail.artworks is not None
    incoming = _prepare_incoming_artworks(detail, media_type=media_type)
    if not incoming:
        return

    identity = {
        "provider": detail.provider,
        "media_type": media_type,
        "external_id": str(detail.external_id),
    }

    with transaction.atomic():
        retained_keys = set()
        for artwork in incoming:
            retained_keys.add((artwork.kind, artwork.image_url))
            row, _created = MediaArtwork.objects.get_or_create(
                **identity,
                kind=artwork.kind,
                image_url=artwork.image_url,
                defaults=_artwork_defaults(artwork),
            )
            changed = []
            for field, value in _artwork_defaults(artwork).items():
                if getattr(row, field) != value:
                    setattr(row, field, value)
                    changed.append(field)
            if changed:
                row.save(update_fields=[*changed, "updated_at"])

        if complete_response:
            retained = Q(pk__in=[])
            for kind, image_url in retained_keys:
                retained |= Q(kind=kind, image_url=image_url)
            MediaArtwork.objects.filter(**identity).exclude(retained).delete()


def media_language_for_user(user, media) -> str:
    preference = _get_preference(user, **_media_identity(media))
    if preference is not None and preference.language:
        return preference.language
    return metadata_language_for_user(user, media.provider)


_PREFERENCE_UNSET = object()


def use_original_title_for_media(user, media, *, preference=_PREFERENCE_UNSET) -> bool:
    if preference is _PREFERENCE_UNSET:
        preference = _get_preference(user, **_media_identity(media))
    return bool(preference and preference.use_original_title)


def original_title_preference_keys(user, media_items) -> set[tuple[str, str, str]]:
    identities = {}
    for media in media_items:
        identity = _media_identity(media)
        identities[_identity_key(identity)] = identity
    if not identities or not _is_authenticated(user):
        return set()

    identity_filter = _identity_filter(identities.values())
    return {
        _identity_key(preference)
        for preference in UserMediaArtworkPreference.objects.filter(user=user)
        .filter(identity_filter)
        .only("provider", "media_type", "external_id", "use_original_title")
        if preference.use_original_title
    }


def resolve_media_artwork(
    user,
    media,
    kind: str,
    *,
    language: str | None = None,
) -> str | None:
    return media_artwork_overrides(
        user,
        media,
        language=language,
    ).get(_artwork_override_key(kind))


def media_artwork_overrides(user, media, *, language: str | None = None) -> dict[str, str | None]:
    identity = _media_identity(media)
    preference = _get_preference(
        user,
        select_related_artwork=True,
        **identity,
    )
    candidates = list(MediaArtwork.objects.filter(**identity))
    requested_language = _requested_language(user, media, preference, language)
    return _artwork_overrides_from_values(
        media,
        identity,
        preference,
        candidates,
        requested_language,
    )


def save_media_artwork_preferences(
    user,
    *,
    media,
    language: str | None,
    use_original_title: bool,
    poster_artwork_id: int | None,
    background_artwork_id: int | None,
):
    identity = {
        "provider": media.provider,
        "media_type": _media_type_for(media),
        "external_id": str(media.external_id),
    }
    poster = _get_selected_for_save(
        poster_artwork_id,
        identity=identity,
        kind=MediaArtwork.Kind.POSTER,
    )
    background = _get_selected_for_save(
        background_artwork_id,
        identity=identity,
        kind=MediaArtwork.Kind.BACKGROUND,
    )

    values = {
        "language": language or None,
        "use_original_title": use_original_title,
        "poster_artwork": poster,
        "background_artwork": background,
    }
    if not any(values.values()):
        UserMediaArtworkPreference.objects.filter(user=user, **identity).delete()
        return None

    preference, _created = UserMediaArtworkPreference.objects.update_or_create(
        user=user,
        **identity,
        defaults=values,
    )
    return preference


def localized_media_record(media, user, *, artwork_context=None):
    from apps.catalog.localization import LocalizedRecord

    identity = _media_identity(media)
    identity_key = _identity_key(identity)
    if artwork_context is None:
        language = media_language_for_user(user, media)
        overrides = media_artwork_overrides(user, media, language=language)
        preference = _get_preference(user, **identity)
    else:
        preference = artwork_context["preferences"].get(identity_key)
        language = artwork_context["languages"][identity_key]
        overrides = _artwork_overrides_from_values(
            media,
            identity,
            preference,
            artwork_context["artworks"].get(identity_key, []),
            language,
        )
    return LocalizedRecord(
        media,
        language,
        overrides=overrides,
        use_original_title=bool(preference and preference.use_original_title),
    )


def localized_media_records(media_items, user):
    media_list = list(media_items)
    artwork_context = _build_artwork_context(user, media_list)
    return [
        localized_media_record(media, user, artwork_context=artwork_context)
        for media in media_list
    ]


def _build_artwork_context(user, media_items):
    identities = {}
    for media in media_items:
        identity = _media_identity(media)
        identities[_identity_key(identity)] = identity

    preferences = {}
    artworks = {}
    identity_filter = _identity_filter(identities.values())
    if identity_filter is not None and _is_authenticated(user):
        preferences = {
            _identity_key(preference): preference
            for preference in UserMediaArtworkPreference.objects.filter(
                user=user,
            )
            .filter(identity_filter)
            .select_related("poster_artwork", "background_artwork")
        }
    if identity_filter is not None:
        for artwork in MediaArtwork.objects.filter(identity_filter):
            artworks.setdefault(_identity_key(artwork), []).append(artwork)

    languages = {}
    for identity_key, identity in identities.items():
        preference = preferences.get(identity_key)
        languages[identity_key] = _requested_language(
            user,
            identity,
            preference,
            None,
        )

    return {
        "languages": languages,
        "preferences": preferences,
        "artworks": artworks,
    }


def _artwork_overrides_from_values(
    media,
    identity,
    preference,
    candidates,
    requested_language,
):
    return {
        "poster_url": _resolve_artwork_from_values(
            media,
            identity,
            preference,
            candidates,
            MediaArtwork.Kind.POSTER,
            requested_language,
        ),
        "backdrop_url": _resolve_artwork_from_values(
            media,
            identity,
            preference,
            candidates,
            MediaArtwork.Kind.BACKGROUND,
            requested_language,
        ),
    }


def _resolve_artwork_from_values(
    media,
    identity,
    preference,
    candidates,
    kind,
    requested_language,
):
    selected = _selected_artwork(preference, kind)
    if selected is not None and _matches_identity(selected, identity, kind):
        return selected.image_url

    candidates = [artwork for artwork in candidates if artwork.kind == kind]
    defaults = [artwork for artwork in candidates if artwork.is_default]
    if kind == MediaArtwork.Kind.BACKGROUND and defaults:
        return _best_artwork(defaults).image_url

    localized = [
        artwork
        for artwork in candidates
        if _language_matches(artwork.language, requested_language)
    ]
    if localized:
        return _best_artwork(localized).image_url

    if defaults:
        return _best_artwork(defaults).image_url
    if candidates:
        return _best_artwork(candidates).image_url

    return _legacy_artwork_url(media, kind)


def _prepare_incoming_artworks(
    detail: DetailDTO,
    *,
    media_type: str,
) -> list[ArtworkDTO]:
    by_key: dict[tuple[str, str], ArtworkDTO] = {}
    for artwork in detail.artworks or []:
        image_url = artwork.image_url
        if not image_url:
            continue
        kind = artwork.kind
        if kind not in MediaArtwork.Kind.values:
            continue
        key = (kind, image_url)
        previous = by_key.get(key)
        by_key[key] = _merge_artwork(previous, artwork)

    legacy = {
        MediaArtwork.Kind.POSTER: _legacy_path_url(
            detail.provider,
            MediaArtwork.Kind.POSTER,
            detail.poster_path,
        ),
        MediaArtwork.Kind.BACKGROUND: _legacy_path_url(
            detail.provider,
            MediaArtwork.Kind.BACKGROUND,
            detail.backdrop_path,
        ),
    }
    for kind, image_url in legacy.items():
        if not image_url:
            continue
        if any(
            artwork.kind == kind and artwork.is_default
            for artwork in by_key.values()
        ):
            continue
        key = (kind, image_url)
        if key not in by_key:
            by_key[key] = ArtworkDTO(
                kind=kind,
                image_url=image_url,
                is_default=True,
            )
        elif not by_key[key].is_default:
            by_key[key] = _replace_artwork(by_key[key], is_default=True)

    return list(by_key.values())


def _merge_artwork(previous: ArtworkDTO | None, current: ArtworkDTO) -> ArtworkDTO:
    if previous is None:
        return current
    return _replace_artwork(
        previous,
        language=previous.language or current.language,
        width=previous.width or current.width,
        height=previous.height or current.height,
        score=previous.score if previous.score is not None else current.score,
        remote_id=previous.remote_id or current.remote_id,
        is_default=previous.is_default or current.is_default,
    )


def _replace_artwork(artwork: ArtworkDTO, **changes) -> ArtworkDTO:
    values = {
        "kind": artwork.kind,
        "image_url": artwork.image_url,
        "language": artwork.language,
        "width": artwork.width,
        "height": artwork.height,
        "score": artwork.score,
        "remote_id": artwork.remote_id,
        "is_default": artwork.is_default,
    }
    values.update(changes)
    return ArtworkDTO(**values)


def _artwork_defaults(artwork: ArtworkDTO) -> dict:
    return {
        "language": artwork.language,
        "width": artwork.width,
        "height": artwork.height,
        "score": artwork.score,
        "remote_id": artwork.remote_id,
        "is_default": artwork.is_default,
    }


def _get_preference(user, *, select_related_artwork=False, **identity):
    if not _is_authenticated(user):
        return None
    preferences = UserMediaArtworkPreference.objects.filter(user=user, **identity)
    if select_related_artwork:
        preferences = preferences.select_related(
            "poster_artwork",
            "background_artwork",
        )
    return preferences.first()


def _get_selected_for_save(artwork_id, *, identity: dict, kind: str):
    if not artwork_id:
        return None
    try:
        artwork_id = int(artwork_id)
    except (TypeError, ValueError) as exc:
        raise ValueError("Invalid artwork selection.") from exc
    artwork = MediaArtwork.objects.filter(
        id=artwork_id,
        kind=kind,
        **identity,
    ).first()
    if artwork is None:
        raise ValueError("Selected artwork is no longer available.")
    return artwork


def _media_type_for(media) -> str:
    from apps.movies.models import Movie

    return MediaArtwork.MediaType.MOVIE if isinstance(media, Movie) else MediaArtwork.MediaType.TV


def _media_identity(media) -> dict[str, str]:
    return {
        "provider": media.provider,
        "media_type": _media_type_for(media),
        "external_id": str(media.external_id),
    }


def _identity_key(identity) -> tuple[str, str, str]:
    if isinstance(identity, dict):
        provider = identity["provider"]
        media_type = identity["media_type"]
        external_id = identity["external_id"]
    else:
        provider = identity.provider
        media_type = identity.media_type
        external_id = identity.external_id
    return (
        provider,
        media_type,
        str(external_id),
    )


def media_identity_key(media) -> tuple[str, str, str]:
    return _identity_key(_media_identity(media))


def _identity_filter(identities):
    identities = list(identities)
    if not identities:
        return None
    grouped = {}
    for identity in identities:
        grouped.setdefault(
            (identity["provider"], identity["media_type"]),
            set(),
        ).add(str(identity["external_id"]))
    query = Q(pk__in=[])
    for (provider, media_type), external_ids in grouped.items():
        query |= Q(
            provider=provider,
            media_type=media_type,
            external_id__in=external_ids,
        )
    return query


def _is_authenticated(user) -> bool:
    return user is not None and getattr(user, "is_authenticated", True)


def _requested_language(user, media, preference, language):
    if language:
        return language
    if preference is not None and preference.language:
        return preference.language
    provider = media["provider"] if isinstance(media, dict) else media.provider
    return metadata_language_for_user(user, provider)


def _artwork_override_key(kind: str) -> str:
    return {
        MediaArtwork.Kind.POSTER: "poster_url",
        MediaArtwork.Kind.BACKGROUND: "backdrop_url",
    }[kind]


def _selected_artwork(preference, kind: str):
    if preference is None:
        return None
    if kind == MediaArtwork.Kind.POSTER:
        return preference.poster_artwork
    if kind == MediaArtwork.Kind.BACKGROUND:
        return preference.background_artwork
    return None


def _matches_identity(artwork, identity: dict, kind: str) -> bool:
    return all(
        getattr(artwork, field) == value
        for field, value in identity.items()
    ) and artwork.kind == kind


def _language_matches(artwork_language: str | None, requested_language: str | None) -> bool:
    if not artwork_language or not requested_language:
        return not artwork_language and not requested_language
    return language_codes_match(artwork_language, requested_language)


def _best_artwork(artworks: Iterable[MediaArtwork]) -> MediaArtwork:
    return sorted(
        artworks,
        key=lambda artwork: (
            not artwork.is_default,
            -(artwork.score if artwork.score is not None else -1),
            -((artwork.width or 0) * (artwork.height or 0)),
            artwork.id,
        ),
    )[0]


def _legacy_artwork_url(media, kind: str) -> str | None:
    path = (
        media.poster_path
        if kind == MediaArtwork.Kind.POSTER
        else media.backdrop_path
    )
    return _legacy_path_url(media.provider, kind, path)


def _legacy_path_url(provider: str, kind: str, path: str | None) -> str | None:
    if not path:
        return None
    if provider == "tmdb":
        builder = build_poster_url if kind == MediaArtwork.Kind.POSTER else build_backdrop_url
        return builder(path)
    if path.startswith(("http://", "https://")):
        return path
    return urljoin("https://artworks.thetvdb.com/", path.lstrip("/"))
