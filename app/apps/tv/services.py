import dataclasses
import itertools
from dataclasses import dataclass
from datetime import date, time, timedelta

from django.db import transaction
from django.db.models import Count, Max, Q
from django.utils import timezone

from apps.catalog.models import Genre, SyncStatus
from apps.catalog.localization import (
    PROVIDER_DEFAULT_LANGUAGES,
    episode_name,
    merge_translation_maps,
    metadata_language_for_user,
    season_name,
)
from apps.catalog.providers.exceptions import ProviderError
from apps.catalog.providers.registry import get_provider
from apps.catalog.tracking import find_tracking_match, identity_keys
from apps.tv.models import Episode, Season, Show, UserEpisode, UserShow


def import_show(
    external_id: str,
    *,
    language: str | None = None,
    provider: str = "tvdb",
    provider_getter=get_provider,
    base_detail=None,
    base_episodes=None,
    base_seasons=None,
) -> Show:
    if provider not in PROVIDER_DEFAULT_LANGUAGES:
        raise ValueError(f"Unsupported provider: {provider}")

    default_language = PROVIDER_DEFAULT_LANGUAGES[provider]
    language = language or default_language
    provider_client = provider_getter(provider)

    try:
        detail = (
            base_detail
            if base_detail is not None
            else provider_client.fetch_detail(
                external_id,
                language=language,
                media_type="tv",
            )
        )
        episodes = (
            base_episodes
            if base_episodes is not None
            else provider_client.fetch_episodes(
                external_id,
                language=default_language,
            )
        )
        season_details = (
            base_seasons
            if base_seasons is not None
            else provider_client.fetch_seasons(
                external_id,
                language=default_language,
            )
        )
        selected_episodes = (
            episodes
            if language == default_language
            else provider_client.fetch_episodes(
                external_id,
                language=language,
            )
        )
        selected_seasons = (
            season_details
            if language == default_language
            else provider_client.fetch_seasons(
                external_id,
                language=language,
            )
        )
    except ProviderError:
        Show.objects.filter(provider=provider, external_id=external_id).update(
            sync_status=SyncStatus.ERROR,
        )
        raise

    today = timezone.localdate()

    with transaction.atomic():
        existing_show = Show.objects.filter(
            provider=provider,
            external_id=detail.external_id,
        ).first()
        incoming_show_translations = {
            code: {
                field_name: value
                for field_name, value in {
                    "name": values.get("title"),
                    "overview": values.get("overview"),
                }.items()
                if value
            }
            for code, values in detail.translations.items()
        }
        show, _created = Show.objects.update_or_create(
            provider=provider,
            external_id=detail.external_id,
            defaults={
                "name": detail.title,
                "overview": detail.overview,
                "translations": merge_translation_maps(
                    existing_show.translations if existing_show else {},
                    incoming_show_translations,
                ),
                "poster_path": detail.poster_path,
                "backdrop_path": detail.backdrop_path,
                "first_aired": _parse_date(detail.release_date),
                "status": detail.status,
                "network": detail.network,
                "imdb_id": detail.imdb_id,
                "tmdb_id": detail.tmdb_id or (external_id if provider == "tmdb" else None),
                "tvdb_id": detail.tvdb_id or (external_id if provider == "tvdb" else None),
                "trailer_url": detail.trailer_url,
                "cast": [dataclasses.asdict(member) for member in detail.cast],
                "average_runtime": detail.average_runtime,
                "next_air_date": _parse_date(detail.next_air_date),
                "last_air_date": _parse_date(detail.last_air_date),
                "airs_time": _parse_time(detail.airs_time),
                "last_synced_at": timezone.now(),
                "sync_status": SyncStatus.OK,
            },
        )

        genres = []
        for genre in detail.genres:
            saved_genre, _created = Genre.objects.get_or_create(
                provider=genre.provider,
                external_id=genre.external_id,
                defaults={"name": genre.name},
            )
            saved_genre.name = (
                genre.translations.get("eng", {}).get("name")
                or saved_genre.name
                or genre.name
            )
            saved_genre.translations = merge_translation_maps(
                saved_genre.translations,
                genre.translations,
            )
            saved_genre.save(update_fields=["name", "translations"])
            genres.append(saved_genre)
        show.genres.set(genres)

        seasons_by_number = {item.season_number: item for item in season_details}
        selected_seasons_by_number = {
            item.season_number: item for item in selected_seasons
        }
        selected_episodes_by_key = {
            (item.season_number, item.episode_number): item
            for item in selected_episodes
        }
        incoming_season_numbers = set(seasons_by_number)
        incoming_season_numbers.update(item.season_number for item in episodes)
        seasons = {}
        for season_number in sorted(incoming_season_numbers):
            season_detail = seasons_by_number.get(season_number)
            selected_season = selected_seasons_by_number.get(season_number)
            existing_season = Season.objects.filter(
                show=show,
                season_number=season_number,
            ).first()
            seasons[season_number] = Season.objects.update_or_create(
                show=show,
                season_number=season_number,
                defaults={
                    "name": season_name(season_number),
                    "overview": season_detail.overview if season_detail else "",
                    "poster_path": season_detail.poster_path if season_detail else None,
                    "translations": _season_translations(
                        existing_season.translations if existing_season else {},
                        season_detail.translations if season_detail else {},
                        selected_season.translations if selected_season else {},
                    ),
                },
            )[0]

        incoming_episode_keys = {
            (item.season_number, item.episode_number) for item in episodes
        }
        for item in episodes:
            season = seasons[item.season_number]
            selected_item = selected_episodes_by_key.get(
                (item.season_number, item.episode_number)
            )
            existing_episode = Episode.objects.filter(
                show=show,
                season_number=item.season_number,
                episode_number=item.episode_number,
            ).first()
            Episode.objects.update_or_create(
                show=show,
                season_number=item.season_number,
                episode_number=item.episode_number,
                defaults={
                    "season": season,
                    "absolute_number": item.absolute_number,
                    "name": item.name or episode_name(item.episode_number),
                    "overview": item.overview,
                    "translations": merge_translation_maps(
                        existing_episode.translations if existing_episode else {},
                        item.translations,
                        selected_item.translations if selected_item else {},
                    ),
                    "still_path": item.still_path,
                    "air_date": _parse_date(item.air_date),
                    "runtime": item.runtime,
                    "finale_type": item.finale_type,
                },
            )

        for existing_episode in Episode.objects.filter(show=show).only(
            "id", "season_number", "episode_number"
        ):
            episode_key = (existing_episode.season_number, existing_episode.episode_number)
            if episode_key not in incoming_episode_keys:
                existing_episode.delete()

        if incoming_season_numbers:
            Season.objects.filter(show=show).exclude(
                season_number__in=incoming_season_numbers
            ).delete()
        else:
            Season.objects.filter(show=show).delete()

        show.aired_episode_count = (
            Episode.objects.filter(show=show, season_number__gt=0)
            .filter(Q(air_date__isnull=False) & Q(air_date__lte=today))
            .aggregate(count=Count("id"))["count"]
        )
        show.save(update_fields=["aired_episode_count", "updated_at"])

    return show


def hydrate_show_translations_sync(show_id: int) -> Show:
    show = Show.objects.get(id=show_id)
    provider = get_provider(show.provider)
    failures = []
    result = show
    try:
        default_language = PROVIDER_DEFAULT_LANGUAGES[show.provider]
        detail = provider.fetch_detail(
            show.external_id,
            language=default_language,
            media_type="tv",
        )
        base_episodes = provider.fetch_episodes(
            show.external_id,
            language=default_language,
        )
        base_seasons = provider.fetch_seasons(
            show.external_id,
            language=default_language,
        )
    except ProviderError:
        Show.objects.filter(id=show.id).update(sync_status=SyncStatus.ERROR)
        raise
    languages = dict.fromkeys([default_language, *detail.translations])

    for language in languages:
        try:
            result = import_show(
                show.external_id,
                language=language,
                provider=show.provider,
                provider_getter=lambda _name: provider,
                base_detail=detail,
                base_episodes=base_episodes,
                base_seasons=base_seasons,
            )
        except ProviderError:
            failures.append(language)

    if failures:
        raise ProviderError(
            f"TV translation hydration failed for: {', '.join(failures)}"
        )

    return result


def track_show(
    user,
    external_id: str,
    *,
    provider: str = "tvdb",
    import_func=import_show,
    hydrate_func=None,
) -> UserShow:
    if provider not in PROVIDER_DEFAULT_LANGUAGES:
        raise ValueError(f"Unsupported provider: {provider}")

    show = import_func(
        external_id,
        provider=provider,
        language=metadata_language_for_user(user, provider),
    )
    match = find_tracking_match(
        user,
        "tv",
        provider=show.provider,
        external_id=show.external_id,
        tmdb_id=show.tmdb_id,
        tvdb_id=show.tvdb_id,
        imdb_id=show.imdb_id,
    )
    if match is not None and not match.same_provider:
        raise ValueError("Tracked on another provider.")
    user_show, created = UserShow.objects.get_or_create(user=user, show=show)
    if not created and user_show.status == UserShow.Status.TRACKED:
        return user_show

    user_show.status = UserShow.Status.TRACKED
    user_show.tracking_started_at = timezone.now()
    user_show.save(update_fields=["status", "tracking_started_at", "updated_at"])
    if hydrate_func is None:
        from apps.tv.tasks import hydrate_show_translations

        hydrate_func = lambda show_id: hydrate_show_translations.defer(
            show_id=show_id,
        )
    hydrate_func(show.id)
    return user_show


def refresh_show(user, show, *, sync_func=None) -> Show:
    if not UserShow.objects.filter(user=user, show=show).exists():
        raise ValueError("Show is not tracked by this user.")

    show.sync_status = SyncStatus.PENDING
    show.save(update_fields=["sync_status", "updated_at"])
    if sync_func is None:
        from apps.tv.tasks import sync_show

        sync_func = lambda show_id: sync_show.defer(show_id=show_id)
    sync_func(show.id)
    return show


def switch_show_provider(
    user,
    *,
    source_provider: str,
    source_external_id: str,
    target_provider: str,
    target_external_id: str,
    target_imdb_id: str | None = None,
    sync_func=None,
) -> Show:
    if source_provider not in PROVIDER_DEFAULT_LANGUAGES:
        raise ValueError(f"Unsupported provider: {source_provider}")
    if target_provider not in PROVIDER_DEFAULT_LANGUAGES:
        raise ValueError(f"Unsupported provider: {target_provider}")
    if source_provider == target_provider:
        raise ValueError("Target provider must differ from the source provider.")

    with transaction.atomic():
        source_state = (
            UserShow.objects.select_for_update()
            .filter(
                user=user,
                show__provider=source_provider,
                show__external_id=str(source_external_id),
            )
            .select_related("show")
            .first()
        )
        if source_state is None:
            raise ValueError("Source show is not tracked by this user.")

        source = Show.objects.select_for_update().get(id=source_state.show_id)
        target = Show.objects.filter(
            provider=target_provider,
            external_id=str(target_external_id),
        ).first()
        if not _show_provider_ids_match(
            source,
            target,
            target_provider=target_provider,
            target_external_id=target_external_id,
            target_imdb_id=target_imdb_id,
        ):
            raise ValueError("Shows do not match across providers.")

        target_created = target is None
        if target_created:
            target = Show.objects.create(
                provider=target_provider,
                external_id=str(target_external_id),
                **_show_switch_defaults(source, target_provider, target_external_id),
            )
            target.genres.set(source.genres.all())
            _clone_show_catalog(source, target)
        else:
            target.imdb_id = target.imdb_id or target_imdb_id or source.imdb_id
            target.tmdb_id = target.tmdb_id or source.tmdb_id
            target.tvdb_id = target.tvdb_id or source.tvdb_id

        target.tmdb_id = (
            str(target_external_id)
            if target_provider == "tmdb"
            else target.tmdb_id
        )
        target.tvdb_id = (
            str(target_external_id)
            if target_provider == "tvdb"
            else target.tvdb_id
        )
        target.sync_status = SyncStatus.PENDING
        target.save(
            update_fields=[
                "imdb_id",
                "tmdb_id",
                "tvdb_id",
                "sync_status",
                "updated_at",
            ]
        )

        target_state, _created = UserShow.objects.get_or_create(
            user=user,
            show=target,
        )
        target_state.status = source_state.status
        target_state.tracking_started_at = source_state.tracking_started_at
        target_state.tier = source_state.tier
        target_state.save(update_fields=["status", "tracking_started_at", "tier", "updated_at"])

        source_episodes = list(
            UserEpisode.objects.filter(user=user, episode__show=source)
            .select_related("episode")
        )
        for source_user_episode in source_episodes:
            source_episode = source_user_episode.episode
            target_episode = Episode.objects.filter(
                show=target,
                season_number=source_episode.season_number,
                episode_number=source_episode.episode_number,
            ).first()
            if target_episode is not None:
                target_user_episode, created = UserEpisode.objects.get_or_create(
                    user=user,
                    episode=target_episode,
                    defaults={"seen_at": source_user_episode.seen_at},
                )
                if not created and (
                    target_user_episode.seen_at is None
                    or (
                        source_user_episode.seen_at is not None
                        and source_user_episode.seen_at > target_user_episode.seen_at
                    )
                ):
                    target_user_episode.seen_at = source_user_episode.seen_at
                    target_user_episode.save(update_fields=["seen_at"])
            source_user_episode.delete()

        source_state.delete()
        if not source.user_states.exists() and not source.episodes.filter(
            user_states__isnull=False
        ).exists():
            source.delete()

    if sync_func is None:
        from apps.tv.tasks import sync_show

        sync_func = lambda show_id: sync_show.defer(show_id=show_id)
    sync_func(target.id)
    return target


def _show_provider_ids_match(
    source: Show,
    target: Show | None,
    *,
    target_provider: str,
    target_external_id: str,
    target_imdb_id: str | None,
) -> bool:
    source_keys = identity_keys(
        source.provider,
        source.external_id,
        tmdb_id=source.tmdb_id,
        tvdb_id=source.tvdb_id,
        imdb_id=source.imdb_id,
    )
    target_keys = identity_keys(
        target_provider,
        target_external_id,
        tmdb_id=target.tmdb_id if target else None,
        tvdb_id=target.tvdb_id if target else None,
        imdb_id=(target.imdb_id if target else None) or target_imdb_id,
    )
    return bool(source_keys.intersection(target_keys))


def _show_switch_defaults(
    source: Show,
    target_provider: str,
    target_external_id: str,
) -> dict:
    return {
        "name": source.name,
        "overview": source.overview,
        "translations": source.translations,
        "poster_path": source.poster_url,
        "backdrop_path": source.backdrop_path,
        "cast": source.cast,
        "trailer_url": source.trailer_url,
        "imdb_id": source.imdb_id,
        "tmdb_id": str(target_external_id) if target_provider == "tmdb" else source.tmdb_id,
        "tvdb_id": str(target_external_id) if target_provider == "tvdb" else source.tvdb_id,
        "average_runtime": source.average_runtime,
        "next_air_date": source.next_air_date,
        "last_air_date": source.last_air_date,
        "airs_time": source.airs_time,
        "first_aired": source.first_aired,
        "status": source.status,
        "network": source.network,
        "aired_episode_count": source.aired_episode_count,
        "sync_status": SyncStatus.PENDING,
    }


def _clone_show_catalog(source: Show, target: Show) -> None:
    seasons = {}
    for source_season in source.seasons.all():
        seasons[source_season.season_number] = Season.objects.create(
            show=target,
            season_number=source_season.season_number,
            name=source_season.name,
            overview=source_season.overview,
            translations=source_season.translations,
            poster_path=source_season.poster_path,
        )

    for source_episode in source.episodes.all():
        target_season = seasons[source_episode.season_number]
        Episode.objects.create(
            show=target,
            season=target_season,
            season_number=source_episode.season_number,
            episode_number=source_episode.episode_number,
            absolute_number=source_episode.absolute_number,
            name=source_episode.name,
            overview=source_episode.overview,
            translations=source_episode.translations,
            still_path=source_episode.still_path,
            air_date=source_episode.air_date,
            runtime=source_episode.runtime,
            finale_type=source_episode.finale_type,
        )


def _season_translations(*translations):
    return {
        language: {
            field_name: value
            for field_name, value in values.items()
            if field_name != "name"
        }
        for language, values in merge_translation_maps(*translations).items()
    }


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None

    return date.fromisoformat(value)


def _parse_time(value: str | None) -> time | None:
    if not value:
        return None

    try:
        return time.fromisoformat(value)
    except ValueError:
        return None


def drop_show(user, show: Show) -> UserShow:
    user_show, _created = UserShow.objects.get_or_create(user=user, show=show)
    user_show.status = UserShow.Status.DROPPED
    user_show.save(update_fields=["status", "updated_at"])
    return user_show


def pause_show(user, show: Show) -> UserShow:
    user_show, _created = UserShow.objects.get_or_create(user=user, show=show)
    user_show.status = UserShow.Status.PAUSED
    user_show.save(update_fields=["status", "updated_at"])
    return user_show


def delete_show_data(user, show: Show) -> None:
    UserEpisode.objects.filter(user=user, episode__show=show).delete()
    UserShow.objects.filter(user=user, show=show).delete()


def _require_tracking(user, show: Show) -> UserShow:
    try:
        return UserShow.objects.get(user=user, show=show, status=UserShow.Status.TRACKED)
    except UserShow.DoesNotExist as exc:
        raise ValueError("Show must be tracked before managing watched episodes.") from exc


def mark_episode_watched(user, episode: Episode) -> UserEpisode:
    _require_tracking(user, episode.show)
    user_episode, _created = UserEpisode.objects.get_or_create(user=user, episode=episode)
    return user_episode


def unmark_episode_watched(user, episode: Episode) -> None:
    _require_tracking(user, episode.show)
    UserEpisode.objects.filter(user=user, episode=episode).delete()


def mark_season_watched(user, season: Season) -> None:
    _require_tracking(user, season.show)
    today = timezone.localdate()
    aired_episodes = season.episodes.filter(air_date__isnull=False, air_date__lte=today)
    for episode in aired_episodes:
        UserEpisode.objects.get_or_create(user=user, episode=episode)


def unmark_season_watched(user, season: Season) -> None:
    _require_tracking(user, season.show)
    UserEpisode.objects.filter(user=user, episode__season=season).delete()


def mark_show_watched(user, show: Show) -> None:
    _require_tracking(user, show)
    today = timezone.localdate()
    aired_episodes = Episode.objects.filter(
        show=show,
        season_number__gt=0,
        air_date__isnull=False,
        air_date__lte=today,
    )
    for episode in aired_episodes:
        UserEpisode.objects.get_or_create(user=user, episode=episode)


def unmark_show_watched(user, show: Show) -> None:
    _require_tracking(user, show)
    UserEpisode.objects.filter(
        user=user, episode__show=show, episode__season_number__gt=0
    ).delete()


@dataclass
class WatchlistEntry:
    show: Show
    next_episode: Episode
    pending_count: int


@dataclass
class UpNextSections:
    active: list[WatchlistEntry]
    not_seen_in_a_while: list[WatchlistEntry]
    not_started: list[WatchlistEntry]


WATCHLIST_SECTIONS = ("all", "watching", "completed", "paused", "dropped")
FINISHED_SHOW_STATUSES = frozenset(
    {"canceled", "cancelled", "completed", "ended", "finished"}
)


def watchlist_progress_color(
    watched_count: int,
    total_count: int,
    show_status: str,
) -> str:
    if watched_count < total_count:
        return "warning"
    if (show_status or "").strip().casefold() in FINISHED_SHOW_STATUSES:
        return "success"
    return "info"


def _attach_watchlist_progress(user, shows: list[Show]) -> None:
    if not shows:
        return

    show_ids = [show.id for show in shows]
    available_episodes = Episode.objects.filter(
        show_id__in=show_ids,
        season_number__gt=0,
        air_date__isnull=False,
        air_date__lte=timezone.localdate(),
    )
    total_counts = {
        row["show_id"]: row["total_count"]
        for row in available_episodes.order_by()
        .values("show_id")
        .annotate(total_count=Count("id"))
    }
    watched_counts = {
        row["episode__show_id"]: row["watched_count"]
        for row in UserEpisode.objects.filter(
            user=user,
            episode__in=available_episodes,
        )
        .values("episode__show_id")
        .annotate(watched_count=Count("id"))
    }

    for show in shows:
        show.total_episode_count = total_counts.get(show.id, 0)
        show.watched_episode_count = watched_counts.get(show.id, 0)
        show.progress_color = watchlist_progress_color(
            show.watched_episode_count,
            show.total_episode_count,
            show.status,
        )


def get_watchlist_shows(user, section: str = "all") -> list[Show]:
    if section not in WATCHLIST_SECTIONS:
        raise ValueError(f"Unknown watchlist section: {section}")

    user_shows = list(
        UserShow.objects.filter(user=user)
        .select_related("show")
        .order_by("show__name", "show_id")
    )
    shows = [user_show.show for user_show in user_shows]
    _attach_watchlist_progress(user, shows)
    if section == "all":
        return shows

    if section in {"paused", "dropped"}:
        status = (
            UserShow.Status.PAUSED
            if section == "paused"
            else UserShow.Status.DROPPED
        )
        return [
            show
            for user_show, show in zip(user_shows, shows)
            if user_show.status == status
        ]

    tracked_shows = [
        show
        for user_show, show in zip(user_shows, shows)
        if user_show.status == UserShow.Status.TRACKED
    ]
    if not tracked_shows:
        return []

    selected = []
    for show in tracked_shows:
        aired_count = show.total_episode_count
        watched_count = show.watched_episode_count
        if section == "completed":
            include = aired_count > 0 and watched_count == aired_count
        else:
            include = aired_count > watched_count
        if include:
            selected.append(show)
    return selected


def get_watchlist(user) -> list[WatchlistEntry]:
    tracked_shows = list(
        Show.objects.filter(user_states__user=user, user_states__status=UserShow.Status.TRACKED)
    )
    if not tracked_shows:
        return []

    today = timezone.localdate()
    shows_by_id = {show.id: show for show in tracked_shows}

    aired_episodes = Episode.objects.filter(
        show__in=tracked_shows,
        season_number__gt=0,
        air_date__isnull=False,
        air_date__lte=today,
    ).order_by("show_id", "air_date", "episode_number")

    watched_ids = set(
        UserEpisode.objects.filter(user=user, episode__show__in=tracked_shows).values_list(
            "episode_id", flat=True
        )
    )

    entries = []
    for show_id, episodes in itertools.groupby(aired_episodes, key=lambda episode: episode.show_id):
        pending = [episode for episode in episodes if episode.id not in watched_ids]
        if not pending:
            continue
        entries.append(
            WatchlistEntry(
                show=shows_by_id[show_id],
                next_episode=pending[0],
                pending_count=len(pending) - 1,
            )
        )

    entries.sort(key=lambda entry: entry.next_episode.air_date, reverse=True)
    return entries


def get_up_next(user) -> UpNextSections:
    entries = get_watchlist(user)
    if not entries:
        return UpNextSections(active=[], not_seen_in_a_while=[], not_started=[])

    show_ids = [entry.show.id for entry in entries]
    activity = {
        row["episode__show_id"]: row
        for row in UserEpisode.objects.filter(
            user=user,
            episode__show_id__in=show_ids,
        )
        .values("episode__show_id")
        .annotate(watched_count=Count("id"), last_seen_at=Max("seen_at"))
    }
    stale_cutoff = timezone.now() - timedelta(days=30)
    active = []
    not_seen_in_a_while = []
    not_started = []

    for entry in entries:
        row = activity.get(entry.show.id)
        if row is None or row["watched_count"] == 0:
            not_started.append(entry)
        elif row["last_seen_at"] < stale_cutoff:
            not_seen_in_a_while.append(entry)
        else:
            active.append(entry)

    return UpNextSections(
        active=active,
        not_seen_in_a_while=not_seen_in_a_while,
        not_started=not_started,
    )


def get_watchlist_entry(user, show: Show) -> WatchlistEntry | None:
    today = timezone.localdate()
    pending = list(
        Episode.objects.filter(
            show=show, season_number__gt=0, air_date__isnull=False, air_date__lte=today
        )
        .exclude(user_states__user=user)
        .order_by("air_date", "episode_number")
    )
    if not pending:
        return None
    return WatchlistEntry(show=show, next_episode=pending[0], pending_count=len(pending) - 1)


@dataclass
class UpcomingEntry:
    episode: Episode
    countdown: str
    watched: bool


@dataclass
class UpcomingMonth:
    month_start: date
    entries: list[UpcomingEntry]
    next_cursor: date | None


def countdown_label(air_date: date, today: date) -> str:
    delta = (air_date - today).days
    if delta == -1:
        return "Yesterday"
    if delta == 0:
        return "Today"
    if delta == 1:
        return "Tomorrow"
    return f"{delta} days"


def _next_month_start(month_start: date) -> date:
    if month_start.month == 12:
        return month_start.replace(year=month_start.year + 1, month=1)
    return month_start.replace(month=month_start.month + 1)


def _upcoming_queryset(user):
    tracked_shows = Show.objects.filter(
        user_states__user=user,
        user_states__status=UserShow.Status.TRACKED,
    )
    yesterday = timezone.localdate() - timedelta(days=1)

    return (
        Episode.objects.filter(
            show__in=tracked_shows,
            season_number__gt=0,
            air_date__gte=yesterday,
        )
        .select_related("show")
        .order_by("air_date", "show__name", "episode_number")
    )


def _build_upcoming_entries(user, episodes) -> list[UpcomingEntry]:
    if not episodes:
        return []

    today = timezone.localdate()
    watched_ids = set(
        UserEpisode.objects.filter(user=user, episode__in=episodes).values_list(
            "episode_id", flat=True
        )
    )

    return [
        UpcomingEntry(
            episode=episode,
            countdown=countdown_label(episode.air_date, today),
            watched=episode.id in watched_ids,
        )
        for episode in episodes
    ]


def get_upcoming_episodes(user, count: int = 10) -> list[UpcomingEntry]:
    episodes = list(_upcoming_queryset(user)[:count])
    return _build_upcoming_entries(user, episodes)


def get_upcoming_month(user, after_month: date | None = None) -> UpcomingMonth | None:
    episodes = _upcoming_queryset(user)
    if after_month is not None:
        episodes = episodes.filter(air_date__gte=_next_month_start(after_month))

    first_episode = episodes.first()
    if first_episode is None:
        return None

    month_start = first_episode.air_date.replace(day=1)
    following_month = _next_month_start(month_start)
    month_episodes = list(
        episodes.filter(air_date__gte=month_start, air_date__lt=following_month)
    )
    next_cursor = (
        month_start if episodes.filter(air_date__gte=following_month).exists() else None
    )
    return UpcomingMonth(
        month_start=month_start,
        entries=_build_upcoming_entries(user, month_episodes),
        next_cursor=next_cursor,
    )
