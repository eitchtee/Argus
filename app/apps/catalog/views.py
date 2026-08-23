from django.conf import settings
from django.http import HttpResponse, HttpResponseBadRequest, HttpResponseForbidden
from django.shortcuts import render
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_http_methods

from apps.catalog.artwork import (
    media_language_for_user,
    save_media_artwork_preferences,
    use_original_title_for_media,
)
from apps.catalog.forms import MediaArtworkPreferenceForm, SearchForm
from apps.catalog.languages import language_display_name
from apps.catalog.localization import metadata_language_for_user, resolve_title
from apps.catalog.models import MediaArtwork, SyncStatus, UserMediaArtworkPreference
from apps.catalog.providers.exceptions import ProviderError
from apps.catalog.services import (
    SEARCH_TYPE_PROVIDERS,
    SUPPORTED_PROVIDERS,
    search as catalog_search,
)
from apps.catalog.tracking import tracking_matches
from apps.common.decorators.htmx import only_htmx
from apps.common.decorators.user import htmx_login_required

SEARCH_RESULT_PAGE_SIZE = 20
TVDB_SEARCH_RESULT_PAGE_SIZE = 1


@htmx_login_required
@require_http_methods(["GET"])
def search_page(request):
    query, media_type, provider, page = _params(request.GET)
    return render(
        request,
        "catalog/pages/search.html",
        {
            "query": query,
            "media_type": media_type,
            "provider": provider,
            "page": page,
            "form": SearchForm(
        initial={"q": query, "type": media_type, "provider": provider}
            ),
        },
    )


@only_htmx
@htmx_login_required
@require_http_methods(["GET"])
def search_results(request):
    query, media_type, provider, page = _params(request.GET)
    context = _search_context(request, query, media_type, provider, page)
    return render(request, "catalog/fragments/results.html", context)


@only_htmx
@htmx_login_required
@require_http_methods(["POST"])
def track(request):
    if settings.DEMO and not request.user.is_superuser:
        return HttpResponseForbidden("Demo mode is read-only.")

    query = request.POST.get("q", "").strip()
    media_type = request.POST.get("type", "movie").strip()
    provider = request.POST.get("provider", "").strip().lower()
    external_id = request.POST.get("external_id", "").strip()
    page = _parse_page(request.POST.get("page", "1"))

    error = None
    if media_type not in {"movie", "tv"} or not external_id:
        error = _("Invalid request.")
    elif not provider:
        provider = SEARCH_TYPE_PROVIDERS[media_type]
    if error is None and provider not in SUPPORTED_PROVIDERS:
        error = _("Invalid request.")

    if error is None:
        try:
            if media_type == "movie":
                from apps.movies.services import queue_track_movie

                queue_track_movie(request.user, provider, external_id)
            else:
                from apps.tv.services import queue_track_show

                queue_track_show(request.user, external_id, provider=provider)
        except (ValueError, ProviderError) as exc:
            error = str(exc) or _("Provider error.")

    item = _find_tracked_item(
        request,
        query,
        media_type,
        provider,
        page,
        external_id,
        error,
    )
    context = {
        "media_type": media_type,
        "provider": provider,
        "query": query,
        "page": page,
        "item": item,
        "error": error if item is None else None,
    }
    return render(request, "catalog/fragments/result_card.html", context)


@only_htmx
@htmx_login_required
@require_http_methods(["POST"])
def switch(request):
    if settings.DEMO and not request.user.is_superuser:
        return HttpResponseForbidden("Demo mode is read-only.")

    query = _request_param(request, "q")
    media_type = _request_param(request, "type", "movie")
    target_provider = _request_param(request, "provider").lower()
    target_external_id = _request_param(request, "external_id")
    source_provider = _request_param(request, "from_provider").lower()
    source_external_id = _request_param(request, "from_external_id")
    target_imdb_id = _request_param(request, "target_imdb_id") or None
    page = _parse_page(_request_param(request, "page", "1"))

    error = None
    if (
        media_type not in {"movie", "tv"}
        or not target_external_id
        or not source_external_id
        or source_provider not in SUPPORTED_PROVIDERS
        or target_provider not in SUPPORTED_PROVIDERS
        or source_provider == target_provider
    ):
        error = _("Invalid request.")

    if error is None:
        try:
            if media_type == "movie":
                from apps.movies.services import switch_movie_provider

                switch_kwargs = {
                    "source_provider": source_provider,
                    "source_external_id": source_external_id,
                    "target_provider": target_provider,
                    "target_external_id": target_external_id,
                }
                if target_imdb_id:
                    switch_kwargs["target_imdb_id"] = target_imdb_id
                switch_movie_provider(request.user, **switch_kwargs)
            else:
                from apps.tv.services import switch_show_provider

                switch_kwargs = {
                    "source_provider": source_provider,
                    "source_external_id": source_external_id,
                    "target_provider": target_provider,
                    "target_external_id": target_external_id,
                }
                if target_imdb_id:
                    switch_kwargs["target_imdb_id"] = target_imdb_id
                switch_show_provider(request.user, **switch_kwargs)
        except ValueError as exc:
            error = str(exc) or _("Provider error.")

    item = _find_tracked_item(
        request,
        query,
        media_type,
        target_provider,
        page,
        target_external_id,
        error,
    )
    return render(
        request,
        "catalog/fragments/result_card.html",
        {
            "media_type": media_type,
            "provider": target_provider,
            "query": query,
            "page": page,
            "item": item,
            "error": error,
        },
    )


@only_htmx
@htmx_login_required
@require_http_methods(["POST", "DELETE"])
def watched(request):
    if settings.DEMO and not request.user.is_superuser:
        return HttpResponseForbidden("Demo mode is read-only.")

    query = _request_param(request, "q")
    media_type = _request_param(request, "type", "movie")
    provider = _request_param(request, "provider").lower()
    external_id = _request_param(request, "external_id")
    page = _parse_page(_request_param(request, "page", "1"))

    error = None
    if media_type != "movie" or not external_id:
        error = _("Invalid request.")
    elif not provider:
        provider = SEARCH_TYPE_PROVIDERS["movie"]
    elif provider not in SUPPORTED_PROVIDERS:
        error = _("Invalid request.")

    if error is None:
        try:
            from apps.movies.models import Movie
            from apps.movies.services import mark_seen, refresh_movie, unmark_seen

            if request.method == "POST":
                movie, _created = Movie.objects.get_or_create(
                    provider=provider,
                    external_id=external_id,
                    defaults={
                        "title": external_id,
                        "sync_status": SyncStatus.PENDING,
                    },
                )
                mark_seen(request.user, movie)
                if movie.sync_status != SyncStatus.OK or movie.last_synced_at is None:
                    refresh_movie(request.user, movie)
            else:
                movie = Movie.objects.filter(
                    provider=provider,
                    external_id=external_id,
                ).first()
                if movie is not None:
                    unmark_seen(request.user, movie)
        except ValueError as exc:
            error = str(exc) or _("Provider error.")

    item = _find_tracked_item(
        request,
        query,
        media_type,
        provider,
        page,
        external_id,
        error,
    )
    return render(
        request,
        "catalog/fragments/result_card.html",
        {
            "media_type": media_type,
            "provider": provider,
            "query": query,
            "page": page,
            "item": item,
            "error": error if item is None else None,
        },
    )


@only_htmx
@htmx_login_required
@require_http_methods(["GET", "POST"])
def media_artwork_preferences(request, media_type, external_id):
    provider = request.GET.get("provider", request.POST.get("provider", "")).strip().lower()
    if media_type not in MediaArtwork.MediaType.values:
        return HttpResponseBadRequest("Invalid media artwork request.")
    provider = provider or SEARCH_TYPE_PROVIDERS[media_type]
    if provider not in SUPPORTED_PROVIDERS:
        return HttpResponseBadRequest("Invalid media artwork request.")

    media = _get_media_item(media_type, provider, external_id)
    if media is None:
        return HttpResponseBadRequest("Media item is not available for customization.")

    identity = {
        "provider": provider,
        "media_type": media_type,
        "external_id": str(external_id),
    }
    preference = UserMediaArtworkPreference.objects.filter(
        user=request.user,
        **identity,
    ).select_related("poster_artwork", "background_artwork").first()
    artworks = list(MediaArtwork.objects.filter(**identity))
    if request.method == "POST":
        if settings.DEMO and not request.user.is_superuser:
            return HttpResponseForbidden("Demo mode is read-only.")
        form = MediaArtworkPreferenceForm(
            media=media,
            user=request.user,
            artworks=artworks,
            preference=preference,
            data=request.POST,
        )
        if form.is_valid():
            try:
                save_media_artwork_preferences(
                    request.user,
                    media=media,
                    language=form.cleaned_data["language"],
                    use_original_title=form.cleaned_data["use_original_title"],
                    poster_artwork_id=(
                        form.cleaned_data["poster_artwork_id"].id
                        if form.cleaned_data["poster_artwork_id"]
                        else None
                    ),
                    background_artwork_id=(
                        form.cleaned_data["background_artwork_id"].id
                        if form.cleaned_data["background_artwork_id"]
                        else None
                    ),
                )
            except ValueError as exc:
                form.add_error(None, str(exc))
            else:
                return HttpResponse(status=204, headers={"HX-Refresh": "true"})
    else:
        form = MediaArtworkPreferenceForm(
            media=media,
            user=request.user,
            artworks=artworks,
            preference=preference,
        )

    effective_language = media_language_for_user(request.user, media)

    return render(
        request,
        "catalog/fragments/media_artwork_preferences.html",
        {
            "media": media,
            "media_type": media_type,
            "provider": provider,
            "preference": preference,
            "media_title": resolve_title(
                media,
                effective_language,
                use_original_title=use_original_title_for_media(request.user, media),
            ),
            "effective_language": language_display_name(effective_language),
            "poster_artworks": [
                artwork for artwork in artworks if artwork.kind == MediaArtwork.Kind.POSTER
            ],
            "background_artworks": [
                artwork
                for artwork in artworks
                if artwork.kind == MediaArtwork.Kind.BACKGROUND
            ],
            "form": form,
        },
    )


def _get_media_item(media_type, provider, external_id):
    if media_type == MediaArtwork.MediaType.MOVIE:
        from apps.movies.models import Movie

        return Movie.objects.filter(provider=provider, external_id=external_id).first()
    from apps.tv.models import Show

    return Show.objects.filter(provider=provider, external_id=external_id).first()


def _find_tracked_item(
    request,
    query,
    media_type,
    provider,
    page,
    external_id,
    error,
):
    """Re-render just the tracked card in place, instead of replacing the whole
    (potentially infinite-scrolled) results list."""
    if (
        media_type not in {"movie", "tv"}
        or provider not in SUPPORTED_PROVIDERS
        or not query
    ):
        return None

    try:
        language = metadata_language_for_user(request.user, provider)
        raw_results = catalog_search(
            query,
            media_type=media_type,
            language=language,
            page=page,
            provider=provider,
        )
    except (ValueError, ProviderError):
        return None

    matches = tracking_matches(request.user, media_type, raw_results)
    seen_states = (
        _movie_seen_states(request.user, raw_results)
        if media_type == "movie"
        else {}
    )
    for result in raw_results:
        if result.external_id == external_id:
            match = matches[(result.provider, result.external_id)]
            return _result_context(
                result,
                match,
                is_seen=seen_states.get((result.provider, result.external_id), False),
                already_tracked=error is None or bool(match and match.same_provider),
            )
    return None


def _params(params):
    query = params.get("q", "").strip()
    media_type = params.get("type", "movie").strip()
    if media_type not in {"movie", "tv"}:
        media_type = "movie"
    provider = params.get("provider", "").strip().lower()
    if provider not in SUPPORTED_PROVIDERS:
        provider = SEARCH_TYPE_PROVIDERS[media_type]
    page = _parse_page(params.get("page", "1"))
    return query, media_type, provider, page


def _parse_page(value):
    try:
        page = int(value)
    except (TypeError, ValueError):
        return 1
    return page if page >= 1 else 1


def _search_context(request, query, media_type, provider, page):
    context = {
        "query": query,
        "media_type": media_type,
        "provider": provider,
        "page": page,
        "search_page_size": (
            TVDB_SEARCH_RESULT_PAGE_SIZE
            if provider == "tvdb"
            else SEARCH_RESULT_PAGE_SIZE
        ),
        "results": None,
        "error": None,
    }

    if not query or media_type not in {"movie", "tv"}:
        return context

    try:
        language = metadata_language_for_user(request.user, provider)
        raw_results = catalog_search(
            query,
            media_type=media_type,
            language=language,
            page=page,
            provider=provider,
        )
    except ValueError:
        return context

    matches = tracking_matches(request.user, media_type, raw_results)
    seen_states = (
        _movie_seen_states(request.user, raw_results)
        if media_type == "movie"
        else {}
    )
    context["results"] = [
        _result_context(
            result,
            matches[(result.provider, result.external_id)],
            is_seen=seen_states.get((result.provider, result.external_id), False),
        )
        for result in raw_results
    ]
    return context


def _result_context(result, match, *, is_seen=False, already_tracked=None):
    if already_tracked is None:
        already_tracked = bool(match and match.same_provider)
    return {
        "provider": result.provider,
        "external_id": result.external_id,
        "title": result.title,
        "year": result.year,
        "poster_url": result.poster_url,
        "overview": result.overview,
        "already_tracked": already_tracked,
        "is_seen": is_seen,
        "tracked_on_other_provider": bool(match and not match.same_provider),
        "tracked_provider": (
            match.provider if match and not match.same_provider else None
        ),
        "tracked_external_id": (
            match.external_id if match and not match.same_provider else None
        ),
    }


def _movie_seen_states(user, results):
    if not results:
        return {}

    from apps.movies.models import UserMovie

    provider = results[0].provider
    external_ids = [result.external_id for result in results]
    return {
        (movie_provider, external_id): is_seen
        for movie_provider, external_id, is_seen in UserMovie.objects.filter(
            user=user,
            movie__provider=provider,
            movie__external_id__in=external_ids,
        ).values_list("movie__provider", "movie__external_id", "is_seen")
    }


def _request_param(request, name, default=""):
    return request.POST.get(name, request.GET.get(name, default)).strip()
