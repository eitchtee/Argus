from django.conf import settings
from django.contrib import messages
from django.http import HttpResponse, HttpResponseBadRequest, HttpResponseForbidden
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_http_methods

from apps.catalog.providers.tmdb import build_backdrop_url, build_poster_url
from apps.catalog.providers.exceptions import ProviderError
from apps.catalog.localization import (
    LocalizedRecord,
    PROVIDER_DEFAULT_LANGUAGES,
    metadata_language_for_user,
    resolve_field,
    resolve_from_map,
)
from apps.catalog.services import get_movie_detail
from apps.catalog.services import SUPPORTED_PROVIDERS
from apps.catalog.tracking import find_tracking_match
from apps.common.decorators.htmx import only_htmx
from apps.common.decorators.user import htmx_login_required
from apps.movies.models import Movie, UserMovie
from apps.movies.services import (
    get_watched_movies,
    get_watchlist_movies,
    delete_movie_data,
    import_movie,
    mark_seen,
    remove_from_watchlist,
    refresh_movie,
    switch_movie_provider,
    track_movie,
    unmark_seen,
)
from apps.movies.tasks import hydrate_movie_translations


@htmx_login_required
@require_http_methods(["GET"])
def movie_detail(request, external_id):
    provider = _provider_from_request(request, "tmdb")
    context = {"movie": _build_movie_context(request.user, external_id, provider)}
    return render(request, "movies/pages/detail.html", context)


@htmx_login_required
@require_http_methods(["GET"])
def movie_watchlist(request):
    return render(
        request,
        "movies/pages/watchlist.html",
        {
            "movies": [
                LocalizedRecord(
                    movie,
                    metadata_language_for_user(request.user, movie.provider),
                )
                for movie in get_watchlist_movies(request.user)
            ]
        },
    )


@htmx_login_required
@require_http_methods(["GET"])
def movie_watched_list(request):
    return render(
        request,
        "movies/pages/watched.html",
        {
            "movies": [
                LocalizedRecord(
                    movie,
                    metadata_language_for_user(request.user, movie.provider),
                )
                for movie in get_watched_movies(request.user)
            ]
        },
    )


@only_htmx
@htmx_login_required
@require_http_methods(["POST", "DELETE"])
def movie_track(request, external_id):
    if settings.DEMO and not request.user.is_superuser:
        return HttpResponseForbidden("Demo mode is read-only.")

    provider = _provider_from_request(request, "tmdb")
    if request.method == "POST":
        user_movie = track_movie(request.user, provider, external_id)
        movie_state = {
            "external_id": user_movie.movie.external_id,
            "provider": user_movie.movie.provider,
            "on_watchlist": user_movie.on_watchlist,
            "is_seen": user_movie.is_seen,
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
            "on_watchlist": False,
            "is_seen": is_seen,
        }

    return render(request, "movies/fragments/actions.html", {"movie": movie_state})


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
    return HttpResponse(status=204)


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

    provider = _provider_from_request(request, "tmdb")
    movie = import_movie(
        provider,
        external_id,
        language=metadata_language_for_user(request.user, provider),
    )
    hydrate_movie_translations.defer(movie_id=movie.id)
    if request.method == "POST":
        user_movie = mark_seen(request.user, movie)
    else:
        user_movie = unmark_seen(request.user, movie)

    movie_state = {
        "external_id": movie.external_id,
        "provider": movie.provider,
        "on_watchlist": user_movie.on_watchlist,
        "is_seen": user_movie.is_seen,
    }
    return render(request, "movies/fragments/actions.html", {"movie": movie_state})


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
                "on_watchlist": False,
                "is_seen": False,
            }
        },
    )


def _build_movie_context(user, external_id, provider="tmdb"):
    language = metadata_language_for_user(user, provider)
    movie = Movie.objects.filter(provider=provider, external_id=external_id).first()

    if movie is not None:
        tracking_state = _refresh_movie_identity(user, movie, language)
        user_movie = UserMovie.objects.filter(user=user, movie=movie).first()
        return {
            "external_id": movie.external_id,
            "provider": movie.provider,
            "title": resolve_field(movie, "title", language),
            "year": movie.release_date.year if movie.release_date else None,
            "release_date": movie.release_date,
            "tagline": resolve_field(movie, "tagline", language),
            "overview": resolve_field(movie, "overview", language),
            "runtime": movie.runtime,
            "status": movie.status,
            "vote_average": movie.vote_average,
            "director": movie.director,
            "trailer_url": movie.trailer_url,
            "imdb_id": movie.imdb_id,
            "cast": movie.cast,
            "genres": [resolve_field(genre, "name", language) for genre in movie.genres.all()],
            "poster_url": movie.poster_url,
            "backdrop_url": movie.backdrop_url,
            "on_watchlist": user_movie.on_watchlist if user_movie else False,
            "is_seen": user_movie.is_seen if user_movie else False,
            **tracking_state,
        }

    detail = get_movie_detail(
        external_id,
        language=language,
        provider=provider,
    )
    default_language = PROVIDER_DEFAULT_LANGUAGES[provider]
    return {
        "external_id": detail.external_id,
        "provider": provider,
        "title": resolve_from_map(
            detail.translations, "title", language, default_language, detail.title
        ),
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
        "on_watchlist": False,
        "is_seen": False,
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
