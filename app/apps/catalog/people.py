"""Person pages: read straight from TMDB (cached), nothing is stored."""

from collections import Counter

from django.contrib import messages
from django.http import Http404
from django.utils.dateparse import parse_date
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.translation import gettext as _
from django.views.decorators.http import require_http_methods

from apps.catalog.localization import metadata_language_for_user
from apps.catalog.providers.exceptions import ProviderError
from apps.catalog.services import find_person_id, find_tvdb_id, get_person
from apps.catalog.views import render_detail_unavailable
from apps.common.decorators.htmx import only_htmx
from apps.common.decorators.user import htmx_login_required
from apps.movies.models import UserMovie
from apps.tv.models import UserShow

DEFAULT_PROVIDERS = {"movie": "tmdb", "tv": "tvdb"}


@htmx_login_required
@require_http_methods(["GET"])
def person_detail(request, person_id):
    return render(
        request,
        "catalog/pages/person.html",
        {"content_url": reverse("person-detail-content", kwargs={"person_id": person_id})},
    )


@only_htmx
@htmx_login_required
@require_http_methods(["GET"])
def person_detail_content(request, person_id):
    try:
        person = get_person(
            person_id,
            language=metadata_language_for_user(request.user, "tmdb"),
        )
    except ProviderError as exc:
        return render_detail_unavailable(request, "tmdb", exc)
    credits = _credit_entries(request.user, person.credits)
    movies = [credit for credit in credits if credit["media_type"] == "movie"]
    shows = [credit for credit in credits if credit["media_type"] == "tv"]
    return render(
        request,
        "catalog/fragments/person.html",
        {
            "person": person,
            "birthday": parse_date(person.birthday or ""),
            "deathday": parse_date(person.deathday or ""),
            "movies": movies,
            "movie_jobs": _job_counts(movies),
            "shows": shows,
            "show_jobs": _job_counts(shows),
        },
    )


@htmx_login_required
@require_http_methods(["GET"])
def person_lookup(request):
    """Credits stored before people had ids, and TVDB credits, link here."""
    tvdb_id = request.GET.get("tvdb", "").strip() or None
    name = request.GET.get("name", "").strip() or None
    if not (tvdb_id or name):
        raise Http404
    try:
        person_id = find_person_id(tvdb_id=tvdb_id, name=name)
    except ProviderError:
        person_id = None
    if not person_id:
        raise Http404
    return redirect("person-detail", person_id=person_id)


@htmx_login_required
@require_http_methods(["GET"])
def person_title_redirect(request, media_type, tmdb_id):
    """Open a TMDB credit with the provider picked on the person page."""
    if media_type not in DEFAULT_PROVIDERS:
        raise Http404
    provider = request.GET.get("provider") or DEFAULT_PROVIDERS[media_type]
    detail_name = "movie-detail" if media_type == "movie" else "tv-detail"
    if provider == "tvdb":
        try:
            tvdb_id = find_tvdb_id(
                tmdb_id,
                media_type,
                language=metadata_language_for_user(request.user, "tmdb"),
            )
        except ProviderError:
            tvdb_id = None
        if tvdb_id:
            url = reverse(detail_name, kwargs={"external_id": tvdb_id})
            return redirect(url if media_type == "tv" else f"{url}?provider=tvdb")
        messages.info(request, _("TVDB doesn't have this title, so it opened from TMDB."))
    url = reverse(detail_name, kwargs={"external_id": tmdb_id})
    return redirect(f"{url}?provider=tmdb" if media_type == "tv" else url)


def _credit_entries(user, credits):
    movie_ids = [credit.tmdb_id for credit in credits if credit.media_type == "movie"]
    show_ids = [credit.tmdb_id for credit in credits if credit.media_type == "tv"]
    movie_states = {
        tmdb_id: "watched" if is_seen else "tracked"
        for tmdb_id, is_seen in UserMovie.objects.filter(
            user=user, movie__tmdb_id__in=movie_ids
        ).values_list("movie__tmdb_id", "is_seen")
    }
    show_states = dict.fromkeys(
        UserShow.objects.filter(user=user, show__tmdb_id__in=show_ids).values_list(
            "show__tmdb_id", flat=True
        ),
        "tracked",
    )
    return [
        {
            "media_type": credit.media_type,
            "title": credit.title,
            "year": (credit.release_date or "")[:4],
            "roles": ", ".join(credit.roles),
            "jobs": credit.jobs,
            "poster_url": credit.poster_url,
            "href": reverse(
                "person-title",
                kwargs={"media_type": credit.media_type, "tmdb_id": credit.tmdb_id},
            ),
            "state": (movie_states if credit.media_type == "movie" else show_states).get(
                credit.tmdb_id
            ),
        }
        for credit in credits
    ]


def _job_counts(credits):
    """Every role in a section, most common first, for the filter chips."""
    return Counter(job for credit in credits for job in credit["jobs"]).most_common()
