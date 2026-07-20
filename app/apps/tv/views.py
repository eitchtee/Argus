from dataclasses import replace
from datetime import date, time

from django.conf import settings
from django.contrib import messages
from django.db.models import Q
from django.http import HttpResponse, HttpResponseBadRequest, HttpResponseForbidden
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_http_methods

from apps.catalog.localization import (
    LocalizedRecord,
    episode_name,
    metadata_language_for_user,
    resolve_field,
    resolve_from_map,
    season_name,
)
from apps.catalog.localization import PROVIDER_DEFAULT_LANGUAGES
from apps.catalog.providers.exceptions import ProviderError
from apps.catalog.providers.tmdb import build_backdrop_url, build_poster_url
from apps.catalog.tracking import find_tracking_match
from apps.catalog.services import (
    SUPPORTED_PROVIDERS,
    get_show_detail,
    get_show_episodes,
)
from apps.common.decorators.htmx import only_htmx
from apps.common.decorators.user import htmx_login_required
from apps.tv.models import Episode, Season, Show, UserEpisode, UserShow
from apps.tv.services import (
    delete_show_data,
    drop_show,
    get_up_next,
    get_upcoming_month,
    get_upcoming_episodes,
    get_watchlist,
    get_watchlist_entry,
    get_watchlist_shows,
    mark_episode_watched,
    mark_season_watched,
    mark_show_watched,
    pause_show,
    refresh_show,
    switch_show_provider,
    track_show,
    unmark_episode_watched,
    unmark_season_watched,
    unmark_show_watched,
)



@htmx_login_required
@require_http_methods(["GET"])
def up_next(request):
    return render(
        request,
        "tv/pages/up_next.html",
        {"sections": _localize_sections(get_up_next(request.user), request.user)},
    )


def _parse_upcoming_month_cursor(value):
    if len(value) != 7 or value[4] != "-" or not value[:4].isdigit() or not value[5:].isdigit():
        raise ValueError
    return date(int(value[:4]), int(value[5:]), 1)


@htmx_login_required
@require_http_methods(["GET"])
def upcoming(request):
    cursor = request.GET.get("after")
    if cursor is None:
        month = get_upcoming_month(request.user)
        return render(
            request,
            "tv/pages/upcoming.html",
            {"month": _localize_upcoming_month(month, request.user)},
        )

    try:
        after_month = _parse_upcoming_month_cursor(cursor)
    except ValueError:
        return HttpResponseBadRequest("Invalid upcoming month cursor.")

    month = get_upcoming_month(request.user, after_month=after_month)
    if month is None:
        return HttpResponse("")
    return render(
        request,
        "tv/fragments/upcoming_month.html",
        {"month": _localize_upcoming_month(month, request.user)},
    )


@htmx_login_required
@require_http_methods(["GET"])
def watchlist(request):
    return render(request, "tv/pages/watchlist.html")


@only_htmx
@htmx_login_required
@require_http_methods(["GET"])
def watchlist_tab(request, section):
    try:
        shows = get_watchlist_shows(request.user, section)
    except ValueError as exc:
        return HttpResponseBadRequest(str(exc))

    return render(
        request,
        "tv/fragments/watchlist_grid.html",
        {
            "shows": [
                LocalizedRecord(
                    show,
                    metadata_language_for_user(request.user, show.provider),
                )
                for show in shows
            ]
        },
    )


@only_htmx
@htmx_login_required
@require_http_methods(["POST", "DELETE"])
def up_next_episode_watched(request, episode_id):
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

    return render(
        request,
        "tv/fragments/up_next_content.html",
        {"sections": _localize_sections(get_up_next(request.user), request.user)},
    )


@htmx_login_required
@require_http_methods(["GET"])
def show_detail(request, external_id):
    provider = _provider_from_request(request)
    context = {"show": _build_show_context(request.user, external_id, provider)}
    return render(request, "tv/pages/detail.html", context)


@only_htmx
@htmx_login_required
@require_http_methods(["POST"])
def show_track(request, external_id):
    if settings.DEMO and not request.user.is_superuser:
        return HttpResponseForbidden("Demo mode is read-only.")

    provider = _provider_from_request(request)
    track_show(request.user, external_id, provider=provider)
    return _redirect_to_show_detail(external_id, provider)


@only_htmx
@htmx_login_required
@require_http_methods(["POST"])
def show_refresh(request, external_id):
    if settings.DEMO and not request.user.is_superuser:
        return HttpResponseForbidden("Demo mode is read-only.")

    provider = _provider_from_request(request)
    show = get_object_or_404(Show, provider=provider, external_id=external_id)
    try:
        refresh_show(request.user, show)
    except ValueError as exc:
        return HttpResponseBadRequest(str(exc))
    messages.success(request, _("Metadata refresh queued."))
    return HttpResponse(status=204)


@only_htmx
@htmx_login_required
@require_http_methods(["POST"])
def show_switch(request, external_id):
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
        switch_show_provider(
            request.user,
            **switch_kwargs,
        )
    except ValueError as exc:
        return HttpResponseBadRequest(str(exc))

    return _redirect_to_show_detail(external_id, target_provider)


@only_htmx
@htmx_login_required
@require_http_methods(["POST"])
def show_drop(request, external_id):
    if settings.DEMO and not request.user.is_superuser:
        return HttpResponseForbidden("Demo mode is read-only.")

    provider = _provider_from_request(request)
    show = get_object_or_404(Show, provider=provider, external_id=external_id)
    drop_show(request.user, show)
    return _redirect_to_show_detail(external_id, provider)


@only_htmx
@htmx_login_required
@require_http_methods(["POST"])
def show_pause(request, external_id):
    if settings.DEMO and not request.user.is_superuser:
        return HttpResponseForbidden("Demo mode is read-only.")

    provider = _provider_from_request(request)
    show = get_object_or_404(Show, provider=provider, external_id=external_id)
    pause_show(request.user, show)
    return _redirect_to_show_detail(external_id, provider)


@only_htmx
@htmx_login_required
@require_http_methods(["POST"])
def show_delete(request, external_id):
    if settings.DEMO and not request.user.is_superuser:
        return HttpResponseForbidden("Demo mode is read-only.")

    provider = _provider_from_request(request)
    show = get_object_or_404(Show, provider=provider, external_id=external_id)
    delete_show_data(request.user, show)
    return _redirect_to_show_detail(external_id, provider)


@only_htmx
@htmx_login_required
@require_http_methods(["POST", "DELETE"])
def show_watched(request, external_id):
    provider = _provider_from_request(request)
    show = get_object_or_404(Show, provider=provider, external_id=external_id)

    try:
        if request.method == "POST":
            mark_show_watched(request.user, show)
        else:
            unmark_show_watched(request.user, show)
    except ValueError as exc:
        return HttpResponseBadRequest(str(exc))

    return _redirect_to_show_detail(external_id, provider)


@only_htmx
@htmx_login_required
@require_http_methods(["POST", "DELETE"])
def season_watched(request, external_id, season_id):
    provider = _provider_from_request(request)
    show = get_object_or_404(Show, provider=provider, external_id=external_id)
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
        "show_provider": provider,
    }
    return render(request, "tv/fragments/season_inner.html", context)


@only_htmx
@htmx_login_required
@require_http_methods(["POST", "DELETE"])
def episode_watched(request, external_id, episode_id):
    provider = _provider_from_request(request)
    show = get_object_or_404(Show, provider=provider, external_id=external_id)
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
        "show_provider": provider,
    }
    return render(request, "tv/fragments/season_inner.html", context)


@htmx_login_required
@require_http_methods(["GET"])
def episode_detail(request, external_id, episode_id):
    provider = _provider_from_request(request)
    show = get_object_or_404(Show, provider=provider, external_id=external_id)
    episode = get_object_or_404(Episode, id=episode_id, show=show)
    tracked = UserShow.objects.filter(
        user=request.user,
        show=show,
        status=UserShow.Status.TRACKED,
    ).exists()
    watched = tracked and UserEpisode.objects.filter(user=request.user, episode=episode).exists()

    context = {
        "episode": _localize_episode(episode, request.user),
        "show": _localize_show(show, request.user),
        "show_provider": provider,
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

    provider = _provider_from_request(request)
    show = get_object_or_404(Show, provider=provider, external_id=external_id)
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
        "show_provider": provider,
        "episode_id": episode.id,
    }
    return render(request, "tv/fragments/episode_detail_watched_button.html", context)


@only_htmx
@htmx_login_required
@require_http_methods(["GET"])
def home_watchlist(request):
    entries = get_watchlist(request.user)
    return render(
        request,
        "tv/fragments/home_watchlist.html",
        {"entries": [_localize_watchlist_entry(entry, request.user) for entry in entries]},
    )


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

    return render(
        request,
        "tv/fragments/home_watchlist_row.html",
        {"entry": _localize_watchlist_entry(entry, request.user)},
    )


@only_htmx
@htmx_login_required
@require_http_methods(["GET"])
def home_upcoming(request):
    entries = get_upcoming_episodes(request.user)
    return render(
        request,
        "tv/fragments/home_upcoming.html",
        {"entries": [_localize_upcoming_entry(entry, request.user) for entry in entries]},
    )


def _localize_show(show, user):
    return LocalizedRecord(show, metadata_language_for_user(user, show.provider))


def _localize_episode(episode, user):
    language = metadata_language_for_user(user, episode.show.provider)
    return LocalizedRecord(
        episode,
        language,
        overrides={
            "name": resolve_field(episode, "name", language),
            "show": LocalizedRecord(episode.show, language),
        },
    )


def _localize_watchlist_entry(entry, user):
    return replace(
        entry,
        show=_localize_show(entry.show, user),
        next_episode=_localize_episode(entry.next_episode, user),
    )


def _localize_sections(sections, user):
    return replace(
        sections,
        active=[_localize_watchlist_entry(entry, user) for entry in sections.active],
        not_seen_in_a_while=[
            _localize_watchlist_entry(entry, user)
            for entry in sections.not_seen_in_a_while
        ],
        not_started=[
            _localize_watchlist_entry(entry, user) for entry in sections.not_started
        ],
    )


def _localize_upcoming_entry(entry, user):
    return replace(entry, episode=_localize_episode(entry.episode, user))


def _localize_upcoming_month(month, user):
    if month is None:
        return None
    return replace(
        month,
        entries=[_localize_upcoming_entry(entry, user) for entry in month.entries],
    )


def _redirect_to_show_detail(external_id, provider="tvdb"):
    response = HttpResponse()
    location = reverse("tv-detail", kwargs={"external_id": external_id})
    if provider != "tvdb":
        location = f"{location}?provider={provider}"
    response["HX-Redirect"] = location
    return response


def _build_show_context(user, external_id, provider="tvdb"):
    language = metadata_language_for_user(user, provider)
    show = Show.objects.filter(provider=provider, external_id=external_id).first()

    if show is None:
        return _preview_show_context(user, external_id, language, provider)

    tracking_state = _refresh_show_identity(user, show, language)
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
        _season_context(
            season,
            list(season.episodes.order_by("episode_number")),
            watched_ids,
            tracked,
            language,
        )
        for season in Season.objects.filter(show=show).order_by("season_number")
    ]
    numbered_seasons = [season for season in seasons if season["season_number"] > 0]

    return {
        "external_id": show.external_id,
        "provider": show.provider,
        "title": resolve_field(show, "name", language),
        "overview": resolve_field(show, "overview", language),
        "status": show.status,
        "network": show.network,
        "release_date": show.first_aired,
        "genres": [resolve_field(genre, "name", language) for genre in show.genres.all()],
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
        **tracking_state,
    }


def _build_season_context(user, season: Season):
    language = metadata_language_for_user(user, season.show.provider)
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
    return _season_context(season, episodes, watched_ids, tracked, language)


def _season_context(
    season: Season,
    episodes: list[Episode],
    watched_ids: set,
    tracked: bool,
    language: str = "eng",
):
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
                "name": resolve_field(episode, "name", language) or episode_name(
                    episode.episode_number
                ),
                "air_date": episode.air_date,
                "aired": aired,
                "watched": watched,
            }
        )

    return {
        "id": season.id,
        "season_number": season.season_number,
        "name": resolve_from_map(
            season.translations,
            "name",
            language,
            PROVIDER_DEFAULT_LANGUAGES[season.show.provider],
            season_name(season.season_number),
        ),
        "episodes": episode_rows,
        "aired_count": aired_count,
        "aired_watched_count": aired_watched_count,
        "fully_watched": aired_count > 0 and aired_watched_count == aired_count,
        "tracked": tracked,
    }


def _preview_show_context(user, external_id, language=None, provider="tvdb"):
    language = language or PROVIDER_DEFAULT_LANGUAGES[provider]
    default_language = PROVIDER_DEFAULT_LANGUAGES[provider]
    detail = get_show_detail(
        external_id,
        language=language,
        provider=provider,
    )
    episodes = get_show_episodes(
        external_id,
        language=language,
        provider=provider,
    )
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
                    "name": resolve_from_map(
                        episode.translations,
                        "name",
                        language,
                        default_language,
                        episode.name,
                    ) or episode_name(episode.episode_number),
                    "air_date": air_date,
                    "aired": aired,
                    "watched": False,
                }
            )
        seasons.append(
            {
                "id": None,
                "season_number": season_number,
                "name": season_name(season_number),
                "episodes": episode_rows,
                "aired_count": aired_count,
                "aired_watched_count": 0,
                "fully_watched": False,
                "tracked": False,
            }
        )

    return {
        "external_id": detail.external_id,
        "provider": provider,
        "title": resolve_from_map(
            detail.translations,
            "title",
            language,
            default_language,
            detail.title,
        ),
        "overview": resolve_from_map(
            detail.translations,
            "overview",
            language,
            default_language,
            detail.overview,
        ),
        "status": detail.status,
        "network": detail.network,
        "release_date": _parse_iso_date(detail.release_date),
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
        "poster_url": _provider_poster_url(detail.poster_path, provider),
        "backdrop_url": _provider_backdrop_url(detail.backdrop_path, provider),
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
        **_tracking_state_from_ids(
            user,
            "tv",
            provider=detail.provider,
            external_id=detail.external_id,
            tmdb_id=detail.tmdb_id,
            tvdb_id=detail.tvdb_id,
            imdb_id=detail.imdb_id,
        ),
    }


def _show_tracking_state(user, show):
    return _tracking_state_from_ids(
        user,
        "tv",
        provider=show.provider,
        external_id=show.external_id,
        tmdb_id=show.tmdb_id,
        tvdb_id=show.tvdb_id,
        imdb_id=show.imdb_id,
    )


def _refresh_show_identity(user, show, language):
    state = _show_tracking_state(user, show)
    if state["tracked_on_other_provider"]:
        return state
    if not Show.objects.filter(user_states__user=user).exclude(
        provider=show.provider
    ).exists():
        return state
    if not _show_identity_is_incomplete(show):
        return state

    try:
        detail = get_show_detail(
            show.external_id,
            language=language,
            provider=show.provider,
        )
    except ProviderError:
        return state

    fields = {}
    for field in ("imdb_id", "tmdb_id", "tvdb_id"):
        value = getattr(detail, field, None)
        if value and not getattr(show, field):
            fields[field] = value

    if fields:
        for field, value in fields.items():
            setattr(show, field, value)
        show.save(update_fields=list(fields))
        state = _show_tracking_state(user, show)

    return state


def _show_identity_is_incomplete(show):
    return not show.imdb_id or (
        show.provider == "tmdb" and not show.tvdb_id
    ) or (
        show.provider == "tvdb" and not show.tmdb_id
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


def _provider_from_request(request, default="tvdb"):
    provider = request.GET.get("provider", default).strip().lower()
    return provider if provider in SUPPORTED_PROVIDERS else default


def _provider_poster_url(path, provider):
    if provider == "tmdb":
        return build_poster_url(path)
    return path or None


def _provider_backdrop_url(path, provider):
    if provider == "tmdb":
        return build_backdrop_url(path)
    return path or None
