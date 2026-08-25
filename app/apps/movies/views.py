from django.conf import settings
from django.contrib import messages
from django.http import HttpResponse, HttpResponseBadRequest, HttpResponseForbidden
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_http_methods

from apps.catalog.artwork import (
    localized_media_records,
    media_artwork_overrides,
    media_language_for_user,
    use_original_title_for_media,
)
from apps.catalog.models import SyncStatus
from apps.catalog.providers.tmdb import build_backdrop_url, build_poster_url
from apps.catalog.providers.exceptions import ProviderError
from apps.catalog.links import build_external_links
from apps.catalog.localization import (
    PROVIDER_DEFAULT_LANGUAGES,
    metadata_language_for_user,
    resolve_field,
    resolve_from_map,
    resolve_title,
)
from apps.catalog.ratings import (
    attach_user_scores,
    build_rating_url,
    format_score,
    get_user_score,
)
from apps.catalog.services import get_movie_detail
from apps.catalog.services import SUPPORTED_PROVIDERS
from apps.catalog.tracking import find_tracking_match
from apps.common.decorators.htmx import only_htmx
from apps.common.decorators.user import htmx_login_required
from apps.common.htmx import is_htmx_fragment_request
from apps.movies.models import Movie, UserMovie
from apps.movies.services import (
    get_watched_movies,
    get_watchlist_movies,
    delete_movie_data,
    mark_seen,
    normalize_movie_status,
    remove_from_watchlist,
    switch_movie_provider,
    queue_track_movie,
    refresh_movie,
    unmark_seen,
)


@htmx_login_required
@require_http_methods(["GET"])
def movie_detail(request, external_id):
    provider = _provider_from_request(request, "tmdb")
    detail_content_url = reverse(
        "movie-detail-content",
        kwargs={"external_id": external_id},
    )
    if provider != "tmdb":
        detail_content_url = f"{detail_content_url}?provider={provider}"
    return render(
        request,
        "movies/pages/detail.html",
        {"detail_content_url": detail_content_url},
    )


@only_htmx
@htmx_login_required
@require_http_methods(["GET"])
def movie_detail_content(request, external_id):
    provider = _provider_from_request(request, "tmdb")
    context = {
        "movie": _build_movie_context(request.user, external_id, provider),
    }
    return render(request, "movies/fragments/detail.html", context)


@htmx_login_required
@require_http_methods(["GET"])
def movie_watchlist(request):
    if not is_htmx_fragment_request(request):
        return render(request, "movies/pages/watchlist.html")

    return render(
        request,
        "movies/fragments/watchlist.html",
        {
            "movies": localized_media_records(
                get_watchlist_movies(request.user),
                request.user,
            ),
        },
    )


@htmx_login_required
@require_http_methods(["GET"])
def movie_watched_list(request):
    if not is_htmx_fragment_request(request):
        return render(request, "movies/pages/watched.html")

    records = localized_media_records(
        get_watched_movies(request.user),
        request.user,
    )
    attach_user_scores(request.user, [record.source for record in records])
    return render(
        request,
        "movies/fragments/watched.html",
        {"movies": records},
    )


@only_htmx
@htmx_login_required
@require_http_methods(["POST", "DELETE"])
def movie_track(request, external_id):
    if settings.DEMO and not request.user.is_superuser:
        return HttpResponseForbidden("Demo mode is read-only.")

    provider = _provider_from_request(request, "tmdb")
    if request.method == "POST":
        try:
            user_movie = queue_track_movie(request.user, provider, external_id)
        except ValueError as exc:
            return HttpResponseBadRequest(str(exc))
        movie_state = {
            "external_id": user_movie.movie.external_id,
            "provider": user_movie.movie.provider,
            "can_customize": True,
            "on_watchlist": user_movie.on_watchlist,
            "is_seen": user_movie.is_seen,
            "user_rating": format_score(get_user_score(request.user, user_movie.movie)),
            "rating_url": build_rating_url(
                "movie", user_movie.movie.external_id, user_movie.movie.provider
            ),
        }
    else:
        movie = Movie.objects.filter(provider=provider, external_id=external_id).first()
        is_seen = False
        if movie is not None:
            user_movie = remove_from_watchlist(request.user, movie)
            if user_movie is not None:
                is_seen = user_movie.is_seen
        movie_state = {
            "external_id": external_id,
            "provider": provider,
            "can_customize": movie is not None,
            "on_watchlist": False,
            "is_seen": is_seen,
            "user_rating": format_score(get_user_score(request.user, movie)) if movie else None,
            "rating_url": build_rating_url("movie", external_id, provider),
        }

    return render(
        request,
        "movies/fragments/actions.html",
        {"movie": movie_state, "rating_oob": True},
    )


@only_htmx
@htmx_login_required
@require_http_methods(["POST"])
def movie_refresh(request, external_id):
    if settings.DEMO and not request.user.is_superuser:
        return HttpResponseForbidden("Demo mode is read-only.")

    provider = _provider_from_request(request, "tmdb")
    movie = get_object_or_404(Movie, provider=provider, external_id=external_id)
    try:
        refresh_movie(request.user, movie)
    except ValueError as exc:
        return HttpResponseBadRequest(str(exc))
    messages.success(request, _("Metadata refresh queued."))
    return HttpResponse(status=204, headers={"HX-Trigger": "toast"})


@only_htmx
@htmx_login_required
@require_http_methods(["POST"])
def movie_switch(request, external_id):
    if settings.DEMO and not request.user.is_superuser:
        return HttpResponseForbidden("Demo mode is read-only.")

    target_provider = request.GET.get("provider", "").strip().lower()
    source_provider = request.GET.get("from_provider", "").strip().lower()
    source_external_id = request.GET.get("from_external_id", "").strip()
    target_imdb_id = request.GET.get("target_imdb_id", "").strip() or None
    if (
        target_provider not in SUPPORTED_PROVIDERS
        or source_provider not in SUPPORTED_PROVIDERS
        or not source_external_id
        or target_provider == source_provider
    ):
        return HttpResponseBadRequest("Invalid provider switch request.")

    try:
        switch_kwargs = {
            "source_provider": source_provider,
            "source_external_id": source_external_id,
            "target_provider": target_provider,
            "target_external_id": external_id,
        }
        if target_imdb_id:
            switch_kwargs["target_imdb_id"] = target_imdb_id
        switch_movie_provider(
            request.user,
            **switch_kwargs,
        )
    except ValueError as exc:
        return HttpResponseBadRequest(str(exc))

    return _redirect_to_movie_detail(external_id, target_provider)


@only_htmx
@htmx_login_required
@require_http_methods(["POST", "DELETE"])
def movie_watched(request, external_id):
    if settings.DEMO and not request.user.is_superuser:
        return HttpResponseForbidden("Demo mode is read-only.")

    movie_state = _toggle_movie_watched(request, external_id)
    return render(
        request,
        "movies/fragments/actions.html",
        {"movie": movie_state, "rating_oob": True},
    )


@only_htmx
@htmx_login_required
@require_http_methods(["POST", "DELETE"])
def movie_poster_watched(request, external_id):
    if settings.DEMO and not request.user.is_superuser:
        return HttpResponseForbidden("Demo mode is read-only.")

    movie_state = _toggle_movie_watched(request, external_id)
    return render(
        request,
        "movies/fragments/poster_watched_button.html",
        {"movie": movie_state, "watched": movie_state["is_seen"]},
    )


@only_htmx
@htmx_login_required
@require_http_methods(["POST", "DELETE"])
def movie_poster_watchlist_remove(request, external_id):
    if settings.DEMO and not request.user.is_superuser:
        return HttpResponseForbidden("Demo mode is read-only.")

    provider = _provider_from_request(request, "tmdb")
    movie = Movie.objects.filter(provider=provider, external_id=external_id).first()
    if movie is not None:
        remove_from_watchlist(request.user, movie)

    return HttpResponse()


def _toggle_movie_watched(request, external_id):
    provider = _provider_from_request(request, "tmdb")
    if request.method == "POST":
        movie, _created = Movie.objects.get_or_create(
            provider=provider,
            external_id=external_id,
            defaults={
                "title": external_id,
                "sync_status": SyncStatus.PENDING,
            },
        )
        user_movie = mark_seen(request.user, movie)
        if movie.sync_status != SyncStatus.OK or movie.last_synced_at is None:
            refresh_movie(request.user, movie)
    else:
        movie = Movie.objects.filter(
            provider=provider,
            external_id=external_id,
        ).first()
        if movie is None:
            return {
                "external_id": external_id,
                "provider": provider,
                "can_customize": False,
                "on_watchlist": False,
                "is_seen": False,
                "user_rating": None,
                "rating_url": build_rating_url("movie", external_id, provider),
            }
        user_movie = unmark_seen(request.user, movie)

    return {
        "external_id": movie.external_id,
        "provider": movie.provider,
        "can_customize": True,
        "on_watchlist": user_movie.on_watchlist,
        "is_seen": user_movie.is_seen,
        "user_rating": format_score(get_user_score(request.user, movie)),
        "rating_url": build_rating_url("movie", movie.external_id, movie.provider),
    }


@only_htmx
@htmx_login_required
@require_http_methods(["POST"])
def movie_delete(request, external_id):
    if settings.DEMO and not request.user.is_superuser:
        return HttpResponseForbidden("Demo mode is read-only.")

    provider = _provider_from_request(request, "tmdb")
    movie = Movie.objects.filter(provider=provider, external_id=external_id).first()
    if movie is not None:
        delete_movie_data(request.user, movie)

    return render(
        request,
        "movies/fragments/actions.html",
        {
            "movie": {
                "external_id": external_id,
                "provider": provider,
                "can_customize": movie is not None,
                "on_watchlist": False,
                "is_seen": False,
                "user_rating": None,
                "rating_url": build_rating_url("movie", external_id, provider),
            },
            "rating_oob": True,
        },
    )


def _build_movie_context(user, external_id, provider="tmdb"):
    language = metadata_language_for_user(user, provider)
    movie = Movie.objects.filter(provider=provider, external_id=external_id).first()

    if movie is not None:
        language = media_language_for_user(user, movie)
        tracking_state = _refresh_movie_identity(user, movie, language)
        user_movie = UserMovie.objects.filter(user=user, movie=movie).first()
        title = resolve_title(
            movie,
            language,
            use_original_title=use_original_title_for_media(user, movie),
        )
        return {
            "external_id": movie.external_id,
            "provider": movie.provider,
            "provider_label": movie.provider.upper(),
            "title": title,
            "year": movie.release_date.year if movie.release_date else None,
            "release_date": movie.release_date,
            "tagline": resolve_field(movie, "tagline", language),
            "overview": resolve_field(movie, "overview", language),
            "runtime": movie.runtime,
            "status": movie.status,
            "normalized_status": movie.normalized_status,
            "vote_average": movie.vote_average,
            "director": movie.director,
            "trailer_url": movie.trailer_url,
            "imdb_id": movie.imdb_id,
            "cast": movie.cast,
            "genres": [resolve_field(genre, "name", language) for genre in movie.genres.all()],
            **media_artwork_overrides(user, movie, language=language),
            "can_customize": True,
            "tmdb_id": movie.tmdb_id,
            "tvdb_id": movie.tvdb_id,
            "trakt_id": movie.trakt_id,
            "external_links": build_external_links(
                "movie",
                provider=movie.provider,
                external_id=movie.external_id,
                title=title,
                imdb_id=movie.imdb_id,
                tmdb_id=movie.tmdb_id,
                tvdb_id=movie.tvdb_id,
                trakt_id=movie.trakt_id,
            ),
            "on_watchlist": user_movie.on_watchlist if user_movie else False,
            "is_seen": user_movie.is_seen if user_movie else False,
            "user_rating": format_score(get_user_score(user, movie)),
            "rating_url": build_rating_url("movie", movie.external_id, movie.provider),
            **tracking_state,
        }

    detail = get_movie_detail(
        external_id,
        language=language,
        provider=provider,
    )
    default_language = PROVIDER_DEFAULT_LANGUAGES[provider]
    title = resolve_title(detail, language)
    return {
        "external_id": detail.external_id,
        "provider": provider,
        "provider_label": provider.upper(),
        "title": title,
        "year": _year_from_iso_date(detail.release_date),
        "release_date": _parse_iso_date(detail.release_date),
        "tagline": resolve_from_map(
            detail.translations, "tagline", language, default_language, detail.tagline
        ),
        "overview": resolve_from_map(
            detail.translations, "overview", language, default_language, detail.overview
        ),
        "runtime": detail.runtime,
        "status": detail.status,
        "normalized_status": normalize_movie_status(detail.status),
        "vote_average": detail.vote_average,
        "director": detail.director,
        "trailer_url": detail.trailer_url,
        "imdb_id": detail.imdb_id,
        "cast": [
            {"name": member.name, "character": member.character, "photo_url": member.photo_url}
            for member in detail.cast
        ],
        "genres": [
            resolve_from_map(
                genre.translations,
                "name",
                language,
                default_language,
                genre.name,
            )
            for genre in detail.genres
        ],
        "poster_url": build_poster_url(detail.poster_path),
        "backdrop_url": build_backdrop_url(detail.backdrop_path),
        "can_customize": False,
        "tmdb_id": detail.tmdb_id,
        "tvdb_id": detail.tvdb_id,
        "trakt_id": getattr(detail, "trakt_id", None),
        "external_links": build_external_links(
            "movie",
            provider=provider,
            external_id=detail.external_id,
            title=title,
            imdb_id=detail.imdb_id,
            tmdb_id=detail.tmdb_id,
            tvdb_id=detail.tvdb_id,
            trakt_id=getattr(detail, "trakt_id", None),
        ),
        "on_watchlist": False,
        "is_seen": False,
        "user_rating": None,
        "rating_url": build_rating_url("movie", detail.external_id, provider),
        **_tracking_state_from_ids(
            user,
            "movie",
            provider=detail.provider,
            external_id=detail.external_id,
            tmdb_id=detail.tmdb_id,
            tvdb_id=detail.tvdb_id,
            imdb_id=detail.imdb_id,
        ),
    }


def _movie_tracking_state(user, movie):
    return _tracking_state_from_ids(
        user,
        "movie",
        provider=movie.provider,
        external_id=movie.external_id,
        tmdb_id=movie.tmdb_id,
        tvdb_id=movie.tvdb_id,
        imdb_id=movie.imdb_id,
    )


def _refresh_movie_identity(user, movie, language):
    state = _movie_tracking_state(user, movie)
    if state["tracked_on_other_provider"]:
        return state
    if not Movie.objects.filter(user_states__user=user).exclude(
        provider=movie.provider
    ).exists():
        return state
    if not _movie_identity_is_incomplete(movie):
        return state

    try:
        detail = get_movie_detail(
            movie.external_id,
            language=language,
            provider=movie.provider,
        )
    except ProviderError:
        return state

    fields = {}
    for field in ("imdb_id", "tmdb_id", "tvdb_id"):
        value = getattr(detail, field, None)
        if value and not getattr(movie, field):
            fields[field] = value

    if fields:
        for field, value in fields.items():
            setattr(movie, field, value)
        movie.save(update_fields=list(fields))
        state = _movie_tracking_state(user, movie)

    return state


def _movie_identity_is_incomplete(movie):
    return not movie.imdb_id or (
        movie.provider == "tmdb" and not movie.tvdb_id
    ) or (
        movie.provider == "tvdb" and not movie.tmdb_id
    )


def _tracking_state_from_ids(
    user,
    media_type,
    *,
    provider,
    external_id,
    tmdb_id=None,
    tvdb_id=None,
    imdb_id=None,
):
    match = find_tracking_match(
        user,
        media_type,
        provider=provider,
        external_id=external_id,
        tmdb_id=tmdb_id,
        tvdb_id=tvdb_id,
        imdb_id=imdb_id,
    )
    other_provider = bool(match and not match.same_provider)
    return {
        "tracked_on_other_provider": other_provider,
        "tracked_provider": match.provider if other_provider else None,
        "tracked_external_id": match.external_id if other_provider else None,
    }


def _redirect_to_movie_detail(external_id, provider="tmdb"):
    response = HttpResponse()
    location = reverse("movie-detail", kwargs={"external_id": external_id})
    if provider != "tmdb":
        location = f"{location}?provider={provider}"
    response["HX-Redirect"] = location
    return response


def _provider_from_request(request, default):
    provider = request.GET.get("provider", default).strip().lower()
    return provider if provider in SUPPORTED_PROVIDERS else default


def _year_from_iso_date(value):
    if not value:
        return None

    try:
        return int(value[:4])
    except ValueError:
        return None


def _parse_iso_date(value):
    if not value:
        return None

    from datetime import date

    return date.fromisoformat(value)
