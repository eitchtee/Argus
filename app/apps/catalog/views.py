from django.conf import settings
from django.http import HttpResponseForbidden
from django.shortcuts import render
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_http_methods

from apps.catalog.forms import SearchForm
from apps.catalog.localization import metadata_language_for_user
from apps.catalog.providers.exceptions import ProviderError
from apps.catalog.services import search as catalog_search
from apps.catalog.tracking import tracked_keys
from apps.common.decorators.htmx import only_htmx
from apps.common.decorators.user import htmx_login_required

SEARCH_RESULT_PAGE_SIZE = 20


@htmx_login_required
@require_http_methods(["GET"])
def search_page(request):
    query, media_type, page = _params(request.GET)
    context = _search_context(request, query, media_type, page)
    context["form"] = SearchForm(initial={"q": query, "type": media_type})
    return render(request, "catalog/pages/search.html", context)


@only_htmx
@htmx_login_required
@require_http_methods(["GET"])
def search_results(request):
    query, media_type, page = _params(request.GET)
    context = _search_context(request, query, media_type, page)
    return render(request, "catalog/fragments/results.html", context)


@only_htmx
@htmx_login_required
@require_http_methods(["POST"])
def track(request):
    if settings.DEMO and not request.user.is_superuser:
        return HttpResponseForbidden("Demo mode is read-only.")

    query = request.POST.get("q", "").strip()
    media_type = request.POST.get("type", "movie").strip()
    external_id = request.POST.get("external_id", "").strip()
    page = _parse_page(request.POST.get("page", "1"))

    error = None
    if media_type not in {"movie", "tv"} or not external_id:
        error = _("Invalid request.")
    else:
        try:
            if media_type == "movie":
                from apps.movies.services import track_movie

                track_movie(request.user, "tmdb", external_id)
            else:
                from apps.tv.services import track_show

                track_show(request.user, external_id)
        except (ValueError, ProviderError) as exc:
            error = str(exc) or _("Provider error.")

    item = _find_tracked_item(request, query, media_type, page, external_id, error)
    context = {
        "media_type": media_type,
        "query": query,
        "page": page,
        "item": item,
        "error": error if item is None else None,
    }
    return render(request, "catalog/fragments/result_card.html", context)


def _find_tracked_item(request, query, media_type, page, external_id, error):
    """Re-render just the tracked card in place, instead of replacing the whole
    (potentially infinite-scrolled) results list."""
    if media_type not in {"movie", "tv"} or not query:
        return None

    try:
        provider = "tmdb" if media_type == "movie" else "tvdb"
        language = metadata_language_for_user(request.user, provider)
        raw_results = catalog_search(
            query,
            media_type=media_type,
            language=language,
            page=page,
        )
    except ValueError:
        return None

    tracked = tracked_keys(request.user, media_type, raw_results)
    for result in raw_results:
        if result.external_id == external_id:
            return {
                "provider": result.provider,
                "external_id": result.external_id,
                "title": result.title,
                "year": result.year,
                "poster_url": result.poster_url,
                "overview": result.overview,
                "already_tracked": error is None or (result.provider, result.external_id) in tracked,
            }
    return None


def _params(params):
    query = params.get("q", "").strip()
    media_type = params.get("type", "movie").strip()
    if media_type not in {"movie", "tv"}:
        media_type = "movie"
    page = _parse_page(params.get("page", "1"))
    return query, media_type, page


def _parse_page(value):
    try:
        page = int(value)
    except (TypeError, ValueError):
        return 1
    return page if page >= 1 else 1


def _search_context(request, query, media_type, page):
    context = {
        "query": query,
        "media_type": media_type,
        "page": page,
        "results": None,
        "error": None,
    }

    if not query or media_type not in {"movie", "tv"}:
        return context

    try:
        provider = "tmdb" if media_type == "movie" else "tvdb"
        language = metadata_language_for_user(request.user, provider)
        raw_results = catalog_search(
            query,
            media_type=media_type,
            language=language,
            page=page,
        )
    except ValueError:
        return context

    tracked = tracked_keys(request.user, media_type, raw_results)
    context["results"] = [
        {
            "provider": r.provider,
            "external_id": r.external_id,
            "title": r.title,
            "year": r.year,
            "poster_url": r.poster_url,
            "overview": r.overview,
            "already_tracked": (r.provider, r.external_id) in tracked,
        }
        for r in raw_results
    ]
    return context
