from datetime import date, time

from django.conf import settings
from django.db.models import Q
from django.http import HttpResponse, HttpResponseBadRequest, HttpResponseForbidden
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from apps.catalog.services import get_show_detail, get_show_episodes
from apps.common.decorators.htmx import only_htmx
from apps.common.decorators.user import htmx_login_required
from apps.tv.models import Episode, Season, Show, UserEpisode, UserShow
from apps.tv.services import (
    delete_show_data,
    drop_show,
    get_upcoming_episodes,
    get_watchlist,
    get_watchlist_entry,
    mark_episode_watched,
    mark_season_watched,
    mark_show_watched,
    pause_show,
    track_show,
    unmark_episode_watched,
    unmark_season_watched,
    unmark_show_watched,
)



@htmx_login_required
@require_http_methods(["GET"])
def show_detail(request, external_id):
    context = {"show": _build_show_context(request.user, external_id)}
    return render(request, "tv/pages/detail.html", context)


@only_htmx
@htmx_login_required
@require_http_methods(["POST"])
def show_track(request, external_id):
    if settings.DEMO and not request.user.is_superuser:
        return HttpResponseForbidden("Demo mode is read-only.")

    track_show(request.user, external_id)
    return _redirect_to_show_detail(external_id)


@only_htmx
@htmx_login_required
@require_http_methods(["POST"])
def show_drop(request, external_id):
    if settings.DEMO and not request.user.is_superuser:
        return HttpResponseForbidden("Demo mode is read-only.")

    show = get_object_or_404(Show, provider="tvdb", external_id=external_id)
    drop_show(request.user, show)
    return _redirect_to_show_detail(external_id)


@only_htmx
@htmx_login_required
@require_http_methods(["POST"])
def show_pause(request, external_id):
    if settings.DEMO and not request.user.is_superuser:
        return HttpResponseForbidden("Demo mode is read-only.")

    show = get_object_or_404(Show, provider="tvdb", external_id=external_id)
    pause_show(request.user, show)
    return _redirect_to_show_detail(external_id)


@only_htmx
@htmx_login_required
@require_http_methods(["POST"])
def show_delete(request, external_id):
    if settings.DEMO and not request.user.is_superuser:
        return HttpResponseForbidden("Demo mode is read-only.")

    show = get_object_or_404(Show, provider="tvdb", external_id=external_id)
    delete_show_data(request.user, show)
    return _redirect_to_show_detail(external_id)


@only_htmx
@htmx_login_required
@require_http_methods(["POST", "DELETE"])
def show_watched(request, external_id):
    show = get_object_or_404(Show, provider="tvdb", external_id=external_id)

    try:
        if request.method == "POST":
            mark_show_watched(request.user, show)
        else:
            unmark_show_watched(request.user, show)
    except ValueError as exc:
        return HttpResponseBadRequest(str(exc))

    return _redirect_to_show_detail(external_id)


@only_htmx
@htmx_login_required
@require_http_methods(["POST", "DELETE"])
def season_watched(request, external_id, season_id):
    show = get_object_or_404(Show, provider="tvdb", external_id=external_id)
    season = get_object_or_404(Season, id=season_id, show=show)

    try:
        if request.method == "POST":
            mark_season_watched(request.user, season)
        else:
            unmark_season_watched(request.user, season)
    except ValueError as exc:
        return HttpResponseBadRequest(str(exc))

    context = {
        "season": _build_season_context(request.user, season),
        "show_external_id": external_id,
    }
    return render(request, "tv/fragments/season_inner.html", context)


@only_htmx
@htmx_login_required
@require_http_methods(["POST", "DELETE"])
def episode_watched(request, external_id, episode_id):
    show = get_object_or_404(Show, provider="tvdb", external_id=external_id)
    episode = get_object_or_404(Episode, id=episode_id, show=show)

    try:
        if request.method == "POST":
            mark_episode_watched(request.user, episode)
        else:
            unmark_episode_watched(request.user, episode)
    except ValueError as exc:
        return HttpResponseBadRequest(str(exc))

    context = {
        "season": _build_season_context(request.user, episode.season),
        "show_external_id": external_id,
    }
    return render(request, "tv/fragments/season_inner.html", context)


@htmx_login_required
@require_http_methods(["GET"])
def episode_detail(request, external_id, episode_id):
    show = get_object_or_404(Show, provider="tvdb", external_id=external_id)
    episode = get_object_or_404(Episode, id=episode_id, show=show)
    tracked = UserShow.objects.filter(
        user=request.user,
        show=show,
        status=UserShow.Status.TRACKED,
    ).exists()
    watched = tracked and UserEpisode.objects.filter(user=request.user, episode=episode).exists()

    context = {
        "episode": episode,
        "show": show,
        "tracked": tracked,
        "watched": watched,
        "previous_episode": _adjacent_episode(episode, direction="previous"),
        "next_episode": _adjacent_episode(episode, direction="next"),
    }
    return render(request, "tv/pages/episode_detail.html", context)


def _adjacent_episode(episode: Episode, *, direction: str) -> Episode | None:
    queryset = Episode.objects.filter(show=episode.show)
    if direction == "previous":
        queryset = queryset.filter(
            Q(season_number__lt=episode.season_number)
            | Q(season_number=episode.season_number, episode_number__lt=episode.episode_number)
        ).order_by("-season_number", "-episode_number")
    else:
        queryset = queryset.filter(
            Q(season_number__gt=episode.season_number)
            | Q(season_number=episode.season_number, episode_number__gt=episode.episode_number)
        ).order_by("season_number", "episode_number")
    return queryset.first()


@only_htmx
@htmx_login_required
@require_http_methods(["POST", "DELETE"])
def episode_detail_watched(request, external_id, episode_id):
    if settings.DEMO and not request.user.is_superuser:
        return HttpResponseForbidden("Demo mode is read-only.")

    show = get_object_or_404(Show, provider="tvdb", external_id=external_id)
    episode = get_object_or_404(Episode, id=episode_id, show=show)

    try:
        if request.method == "POST":
            mark_episode_watched(request.user, episode)
        else:
            unmark_episode_watched(request.user, episode)
    except ValueError as exc:
        return HttpResponseBadRequest(str(exc))

    context = {
        "tracked": True,
        "watched": request.method == "POST",
        "show_external_id": external_id,
        "episode_id": episode.id,
    }
    return render(request, "tv/fragments/episode_detail_watched_button.html", context)


@only_htmx
@htmx_login_required
@require_http_methods(["GET"])
def home_watchlist(request):
    entries = get_watchlist(request.user)
    return render(request, "tv/fragments/home_watchlist.html", {"entries": entries})


@only_htmx
@htmx_login_required
@require_http_methods(["POST", "DELETE"])
def home_watchlist_episode_watched(request, episode_id):
    if settings.DEMO and not request.user.is_superuser:
        return HttpResponseForbidden("Demo mode is read-only.")

    episode = get_object_or_404(Episode, id=episode_id)

    try:
        if request.method == "POST":
            mark_episode_watched(request.user, episode)
        else:
            unmark_episode_watched(request.user, episode)
    except ValueError as exc:
        return HttpResponseBadRequest(str(exc))

    entry = get_watchlist_entry(request.user, episode.show)
    if entry is None:
        return HttpResponse("")

    return render(request, "tv/fragments/home_watchlist_row.html", {"entry": entry})


@only_htmx
@htmx_login_required
@require_http_methods(["GET"])
def home_upcoming(request):
    entries = get_upcoming_episodes(request.user)
    return render(request, "tv/fragments/home_upcoming.html", {"entries": entries})


def _redirect_to_show_detail(external_id):
    response = HttpResponse()
    response["HX-Redirect"] = reverse("tv-detail", kwargs={"external_id": external_id})
    return response


def _build_show_context(user, external_id):
    show = Show.objects.filter(provider="tvdb", external_id=external_id).first()

    if show is None:
        return _preview_show_context(external_id)

    user_show = UserShow.objects.filter(user=user, show=show).first()
    tracked = bool(user_show and user_show.status == UserShow.Status.TRACKED)
    watched_ids = set()
    if tracked:
        watched_ids = set(
            UserEpisode.objects.filter(user=user, episode__show=show).values_list(
                "episode_id", flat=True
            )
        )

    seasons = [
        _season_context(season, list(season.episodes.order_by("episode_number")), watched_ids, tracked)
        for season in Season.objects.filter(show=show).order_by("season_number")
    ]
    numbered_seasons = [season for season in seasons if season["season_number"] > 0]

    return {
        "external_id": show.external_id,
        "title": show.name,
        "overview": show.overview,
        "status": show.status,
        "network": show.network,
        "release_date": show.first_aired,
        "genres": [genre.name for genre in show.genres.all()],
        "poster_url": show.poster_url,
        "backdrop_url": show.backdrop_url,
        "imdb_id": show.imdb_id,
        "trailer_url": show.trailer_url,
        "average_runtime": show.average_runtime,
        "next_air_date": show.next_air_date,
        "last_air_date": show.last_air_date,
        "airs_time": show.airs_time,
        "cast": show.cast,
        "tracked": tracked,
        "tracking_status": user_show.status if user_show else None,
        "can_delete": user_show is not None,
        "seasons": seasons,
        "show_fully_watched": (
            tracked and bool(numbered_seasons) and all(s["fully_watched"] for s in numbered_seasons)
        ),
    }


def _build_season_context(user, season: Season):
    tracked = UserShow.objects.filter(
        user=user,
        show=season.show,
        status=UserShow.Status.TRACKED,
    ).exists()
    watched_ids = set(
        UserEpisode.objects.filter(user=user, episode__season=season).values_list(
            "episode_id", flat=True
        )
    )
    episodes = list(season.episodes.order_by("episode_number"))
    return _season_context(season, episodes, watched_ids, tracked)


def _season_context(season: Season, episodes: list[Episode], watched_ids: set, tracked: bool):
    today = timezone.localdate()
    episode_rows = []
    aired_count = 0
    aired_watched_count = 0

    for episode in episodes:
        aired = bool(episode.air_date and episode.air_date <= today)
        watched = episode.id in watched_ids
        if aired:
            aired_count += 1
            if watched:
                aired_watched_count += 1
        episode_rows.append(
            {
                "id": episode.id,
                "episode_number": episode.episode_number,
                "name": episode.name,
                "air_date": episode.air_date,
                "aired": aired,
                "watched": watched,
            }
        )

    return {
        "id": season.id,
        "season_number": season.season_number,
        "name": season.name,
        "episodes": episode_rows,
        "aired_count": aired_count,
        "aired_watched_count": aired_watched_count,
        "fully_watched": aired_count > 0 and aired_watched_count == aired_count,
        "tracked": tracked,
    }


def _preview_show_context(external_id):
    detail = get_show_detail(external_id)
    episodes = get_show_episodes(external_id)
    today = timezone.localdate()

    episodes_by_season: dict[int, list] = {}
    for episode in episodes:
        episodes_by_season.setdefault(episode.season_number, []).append(episode)

    seasons = []
    for season_number in sorted(episodes_by_season):
        season_episodes = sorted(
            episodes_by_season[season_number], key=lambda episode: episode.episode_number
        )
        episode_rows = []
        aired_count = 0
        for episode in season_episodes:
            air_date = _parse_iso_date(episode.air_date)
            aired = bool(air_date and air_date <= today)
            if aired:
                aired_count += 1
            episode_rows.append(
                {
                    "id": None,
                    "episode_number": episode.episode_number,
                    "name": episode.name,
                    "air_date": air_date,
                    "aired": aired,
                    "watched": False,
                }
            )
        seasons.append(
            {
                "id": None,
                "season_number": season_number,
                "name": "Specials" if season_number == 0 else f"Season {season_number}",
                "episodes": episode_rows,
                "aired_count": aired_count,
                "aired_watched_count": 0,
                "fully_watched": False,
                "tracked": False,
            }
        )

    return {
        "external_id": detail.external_id,
        "title": detail.title,
        "overview": detail.overview,
        "status": detail.status,
        "network": detail.network,
        "release_date": _parse_iso_date(detail.release_date),
        "genres": [genre.name for genre in detail.genres],
        "poster_url": detail.poster_path,
        "backdrop_url": detail.backdrop_path,
        "imdb_id": detail.imdb_id,
        "trailer_url": detail.trailer_url,
        "average_runtime": detail.average_runtime,
        "next_air_date": _parse_iso_date(detail.next_air_date),
        "last_air_date": _parse_iso_date(detail.last_air_date),
        "airs_time": _parse_iso_time(detail.airs_time),
        "cast": [
            {"name": member.name, "character": member.character, "photo_url": member.photo_url}
            for member in detail.cast
        ],
        "tracked": False,
        "tracking_status": None,
        "can_delete": False,
        "seasons": seasons,
        "show_fully_watched": False,
    }


def _parse_iso_date(value):
    if not value:
        return None

    return date.fromisoformat(value)


def _parse_iso_time(value):
    if not value:
        return None

    try:
        return time.fromisoformat(value)
    except ValueError:
        return None
