from datetime import time

from django.shortcuts import render
from django.urls import reverse
from django.utils.dates import WEEKDAYS, WEEKDAYS_ABBR
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_http_methods

from apps.catalog.artwork import localized_media_records
from apps.common.decorators.user import htmx_login_required
from apps.common.htmx import is_htmx_fragment_request
from apps.stats.services import get_user_stats


# Tone classes are spelled out in full so Tailwind picks them up.
SHOW_CONDITIONS = (
    ("watching", _("Watching"), "play", "bg-info/10 text-info"),
    ("completed", _("Completed"), "check", "bg-success/10 text-success"),
    ("paused", _("Paused"), "pause", "bg-warning/10 text-warning"),
    ("dropped", _("Dropped"), "circle-minus", "bg-error/10 text-error"),
)
HEATMAP_HOUR_LABELS = (0, 6, 12, 18)


@htmx_login_required
@require_http_methods(["GET"])
def stats_page(request):
    if not is_htmx_fragment_request(request):
        return render(request, "stats/pages/index.html")

    return render(
        request,
        "stats/fragments/content.html",
        _stats_context(request),
    )


def _stats_context(request):
    user = request.user
    stats = get_user_stats(user)

    shows = [entry.show for entry in stats.top_shows]
    if stats.binge:
        shows.append(stats.binge.show)
    localized_shows = {
        record.source.pk: record
        for record in localized_media_records(shows, user)
    }
    top_minutes = stats.top_shows[0].minutes if stats.top_shows else 0

    return {
        "stats": stats,
        "watched_hours": stats.watched_minutes // 60,
        "year_minutes": sum(month.minutes for month in stats.months),
        "heatmap": [
            (WEEKDAYS_ABBR[weekday - 1], cells)
            for weekday, cells in stats.heatmap
        ],
        "heatmap_hours": [time(hour) for hour in HEATMAP_HOUR_LABELS],
        "peak_weekday": (
            WEEKDAYS[stats.peak_weekday - 1] if stats.peak_weekday else None
        ),
        "top_shows": [
            {
                "title": localized_shows[entry.show.pk].name,
                "poster_url": localized_shows[entry.show.pk].poster_url,
                "detail_url": _show_url(entry.show),
                "minutes": entry.minutes,
                "episodes": entry.episodes,
                "percent": (
                    round(entry.minutes * 100 / top_minutes) if top_minutes else 0
                ),
            }
            for entry in stats.top_shows
        ],
        "tracked_shows": sum(stats.show_conditions.values()),
        "show_conditions": [
            {
                "label": label,
                "icon": icon,
                "tone": tone,
                "count": stats.show_conditions.get(key, 0),
            }
            for key, label, icon, tone in SHOW_CONDITIONS
        ],
        "binge": (
            {
                "title": localized_shows[stats.binge.show.pk].name,
                "detail_url": _show_url(stats.binge.show),
                "day": stats.binge.day,
                "episodes": stats.binge.episodes,
            }
            if stats.binge
            else None
        ),
        "longest_movie": _longest_movie_context(user, stats.longest_movie),
    }


def _longest_movie_context(user, movie):
    if movie is None:
        return None
    localized_movie = localized_media_records([movie], user)[0]
    return {
        "title": localized_movie.title,
        "runtime": movie.runtime,
        "detail_url": _provider_url(
            reverse("movie-detail", kwargs={"external_id": movie.external_id}),
            movie.provider,
            "tmdb",
        ),
    }


def _show_url(show):
    return _provider_url(
        reverse("tv-detail", kwargs={"external_id": show.external_id}),
        show.provider,
        "tvdb",
    )


def _provider_url(url, provider, default_provider):
    if provider != default_provider:
        return f"{url}?provider={provider}"
    return url
