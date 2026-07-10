from django.conf import settings
from django.http import HttpResponseForbidden
from django.shortcuts import render
from django.views.decorators.http import require_http_methods

from apps.catalog.providers.tmdb import build_backdrop_url, build_poster_url
from apps.catalog.services import get_movie_detail
from apps.common.decorators.htmx import only_htmx
from apps.common.decorators.user import htmx_login_required
from apps.movies.models import Movie, UserMovie
from apps.movies.services import import_movie, mark_seen, remove_from_watchlist, track_movie, unmark_seen


@htmx_login_required
@require_http_methods(["GET"])
def movie_detail(request, external_id):
    context = {"movie": _build_movie_context(request.user, external_id)}
    return render(request, "movies/pages/detail.html", context)


@only_htmx
@htmx_login_required
@require_http_methods(["POST", "DELETE"])
def movie_track(request, external_id):
    if settings.DEMO and not request.user.is_superuser:
        return HttpResponseForbidden("Demo mode is read-only.")

    if request.method == "POST":
        user_movie = track_movie(request.user, "tmdb", external_id)
        movie_state = {
            "external_id": user_movie.movie.external_id,
            "on_watchlist": user_movie.on_watchlist,
            "is_seen": user_movie.is_seen,
        }
    else:
        movie = Movie.objects.filter(provider="tmdb", external_id=external_id).first()
        is_seen = False
        if movie is not None:
            user_movie = remove_from_watchlist(request.user, movie)
            if user_movie is not None:
                is_seen = user_movie.is_seen
        movie_state = {
            "external_id": external_id,
            "on_watchlist": False,
            "is_seen": is_seen,
        }

    return render(request, "movies/fragments/actions.html", {"movie": movie_state})


@only_htmx
@htmx_login_required
@require_http_methods(["POST", "DELETE"])
def movie_watched(request, external_id):
    if settings.DEMO and not request.user.is_superuser:
        return HttpResponseForbidden("Demo mode is read-only.")

    movie = import_movie("tmdb", external_id)
    if request.method == "POST":
        user_movie = mark_seen(request.user, movie)
    else:
        user_movie = unmark_seen(request.user, movie)

    movie_state = {
        "external_id": movie.external_id,
        "on_watchlist": user_movie.on_watchlist,
        "is_seen": user_movie.is_seen,
    }
    return render(request, "movies/fragments/actions.html", {"movie": movie_state})


def _build_movie_context(user, external_id):
    movie = Movie.objects.filter(provider="tmdb", external_id=external_id).first()

    if movie is not None:
        user_movie = UserMovie.objects.filter(user=user, movie=movie).first()
        return {
            "external_id": movie.external_id,
            "title": movie.title,
            "year": movie.release_date.year if movie.release_date else None,
            "release_date": movie.release_date,
            "tagline": movie.tagline,
            "overview": movie.overview,
            "runtime": movie.runtime,
            "status": movie.status,
            "vote_average": movie.vote_average,
            "director": movie.director,
            "trailer_url": movie.trailer_url,
            "imdb_id": movie.imdb_id,
            "cast": movie.cast,
            "genres": [genre.name for genre in movie.genres.all()],
            "poster_url": movie.poster_url,
            "backdrop_url": movie.backdrop_url,
            "on_watchlist": user_movie.on_watchlist if user_movie else False,
            "is_seen": user_movie.is_seen if user_movie else False,
        }

    detail = get_movie_detail(external_id)
    return {
        "external_id": detail.external_id,
        "title": detail.title,
        "year": _year_from_iso_date(detail.release_date),
        "release_date": _parse_iso_date(detail.release_date),
        "tagline": detail.tagline,
        "overview": detail.overview,
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
        "genres": [genre.name for genre in detail.genres],
        "poster_url": build_poster_url(detail.poster_path),
        "backdrop_url": build_backdrop_url(detail.backdrop_path),
        "on_watchlist": False,
        "is_seen": False,
    }


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
