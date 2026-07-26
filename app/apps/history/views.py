from django.conf import settings
from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils.translation import gettext as _
from django.views.decorators.http import require_http_methods

from apps.catalog.localization import LocalizedRecord, metadata_language_for_user
from apps.common.decorators.htmx import only_htmx
from apps.common.decorators.user import htmx_login_required
from apps.common.htmx import is_htmx_fragment_request
from apps.history.services import get_history_page
from apps.movies.models import UserMovie
from apps.movies.services import unmark_seen
from apps.tv.models import UserEpisode
from apps.tv.services import remove_episode_history


@htmx_login_required
@require_http_methods(["GET"])
def history_page(request):
    if not is_htmx_fragment_request(request):
        return render(request, "history/pages/index.html")

    return render(
        request,
        "history/fragments/content.html",
        _history_context(request),
    )


@only_htmx
@htmx_login_required
@require_http_methods(["DELETE"])
def undo_movie(request, movie_id):
    if settings.DEMO and not request.user.is_superuser:
        return HttpResponseForbidden("Demo mode is read-only.")

    user_movie = get_object_or_404(
        UserMovie.objects.select_related("movie"),
        user=request.user,
        movie_id=movie_id,
        is_seen=True,
    )
    unmark_seen(request.user, user_movie.movie)
    return _history_fragment_response(request)


@only_htmx
@htmx_login_required
@require_http_methods(["DELETE"])
def undo_episode(request, episode_id):
    if settings.DEMO and not request.user.is_superuser:
        return HttpResponseForbidden("Demo mode is read-only.")

    user_episode = get_object_or_404(
        UserEpisode.objects.select_related("episode__show"),
        user=request.user,
        episode_id=episode_id,
    )
    remove_episode_history(request.user, user_episode.episode)
    return _history_fragment_response(request)


def _history_fragment_response(request):
    return render(
        request,
        "history/fragments/content.html",
        _history_context(request),
    )


def _history_context(request):
    page = get_history_page(request.user, request.GET.get("page", 1))
    return {
        "page_obj": page,
        "page_range": page.paginator.get_elided_page_range(
            page.number,
            on_each_side=2,
            on_ends=1,
        ),
        "entries": [
            _history_entry_context(request, entry)
            for entry in page.object_list
        ],
    }


def _history_entry_context(request, entry):
    if entry.kind == "movie":
        movie = entry.record.movie
        localized_movie = LocalizedRecord(
            movie,
            metadata_language_for_user(request.user, movie.provider),
        )
        return {
            "kind": "movie",
            "title": localized_movie.title,
            "media_label": _("Movie"),
            "poster_url": movie.poster_url,
            "icon": "fa-film",
            "detail_url": _provider_url(
                reverse("movie-detail", kwargs={"external_id": movie.external_id}),
                movie.provider,
                "tmdb",
            ),
            "undo_url": reverse(
                "history-undo-movie",
                kwargs={"movie_id": movie.id},
            ),
            "undo_label": _("Mark movie unwatched"),
            "watched_at": entry.watched_at,
            "record_id": movie.id,
        }

    episode = entry.record.episode
    show = episode.show
    language = metadata_language_for_user(request.user, show.provider)
    localized_show = LocalizedRecord(show, language)
    localized_episode = LocalizedRecord(episode, language)
    episode_label = f"S{episode.season_number:02d}E{episode.episode_number:02d}"
    return {
        "kind": "episode",
        "title": localized_show.name,
        "media_label": _("Episode"),
        "episode_name": localized_episode.name,
        "episode_label": episode_label,
        "poster_url": show.poster_url,
        "icon": "fa-tv",
        "detail_url": _provider_url(
            reverse(
                "tv-episode-detail",
                kwargs={
                    "external_id": show.external_id,
                    "episode_id": episode.id,
                },
            ),
            show.provider,
            "tvdb",
        ),
        "undo_url": reverse(
            "history-undo-episode",
            kwargs={"episode_id": episode.id},
        ),
        "undo_label": _("Mark episode unwatched"),
        "watched_at": entry.watched_at,
        "record_id": episode.id,
    }


def _provider_url(url, provider, default_provider):
    if provider != default_provider:
        return f"{url}?provider={provider}"
    return url
