from datetime import timedelta
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from apps.movies.models import Movie, UserMovie
from apps.catalog.providers.exceptions import ProviderError
from apps.trakt.changes import record_intent
from apps.trakt.client import TraktSnapshot
from apps.trakt.models import TraktAccount, TraktSyncIntent
from apps.trakt.sync import (
    RemoteSnapshot,
    WatchedEpisode,
    _ensure_movie,
    _ensure_show,
    _ensure_episodes_batch,
    _find_by_ids,
    _acknowledge_intents,
    _merge_latest_watches,
    normalize_snapshot,
    sync_account,
)
from apps.tv.models import Episode, Season, Show, UserEpisode, UserShow


class FakeTraktClient:
    def __init__(self, snapshot):
        self.snapshot = snapshot
        self.snapshot_calls = []
        self.watchlist_calls = []
        self.history_calls = []
        self.dropped_calls = []

    def get_snapshot(self, **kwargs):
        self.snapshot_calls.append(kwargs)
        return self.snapshot

    def post_watchlist(self, items, *, remove=False):
        self.watchlist_calls.append((items, remove))

    def post_history(self, movies, shows):
        self.history_calls.append((movies, shows))

    def post_dropped(self, shows, *, remove=False):
        self.dropped_calls.append((shows, remove))


def client_factory(client):
    return lambda _account: client


class TraktSyncTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            "user@example.com",
            password="password",
        )
        self.account = TraktAccount.objects.create(
            user=self.user,
            access_token="access",
            refresh_token="refresh",
        )

    def test_duplicate_remote_movie_watches_keep_latest_timestamp(self):
        first = timezone.now() - timedelta(days=1)
        second = timezone.now()
        movie = Movie.objects.create(
            external_id="550",
            trakt_id="5500",
            tmdb_id="550",
            title="Fight Club",
        )
        client = FakeTraktClient(
            TraktSnapshot(
                watchlist_movies=[],
                watchlist_shows=[],
                watched_movies=[
                    {
                        "watched_at": first.isoformat(),
                        "movie": {"ids": {"trakt": 5500, "tmdb": 550}},
                    },
                    {
                        "watched_at": second.isoformat(),
                        "movie": {"ids": {"trakt": 5500, "tmdb": 550}},
                    },
                ],
                watched_shows=[],
                dropped_shows=[],
            )
        )

        sync_account(self.account.id, client_factory=client_factory(client))

        state = UserMovie.objects.get(user=self.user, movie=movie)
        self.assertTrue(state.is_seen)
        self.assertAlmostEqual(state.seen_at.timestamp(), second.timestamp(), places=3)

    def test_duplicate_remote_episode_history_keeps_latest_watch(self):
        first = timezone.now() - timedelta(days=1)
        second = timezone.now()
        snapshot = TraktSnapshot(
            [],
            [],
            [],
            [],
            [],
            watched_episodes=[
                {
                    "watched_at": first.isoformat(),
                    "show": {"ids": {"trakt": 1000}},
                    "episode": {
                        "season": 1,
                        "number": 1,
                        "ids": {"trakt": 1001},
                    },
                },
                {
                    "watched_at": second.isoformat(),
                    "show": {"ids": {"trakt": 1000}},
                    "episode": {
                        "season": 1,
                        "number": 1,
                        "ids": {"trakt": 1001},
                    },
                },
            ],
        )

        remote = normalize_snapshot(snapshot)

        self.assertEqual(len(remote.watched_episodes), 1)
        watched = next(iter(remote.watched_episodes.values()))
        self.assertAlmostEqual(watched.watched_at.timestamp(), second.timestamp(), places=3)

    def test_trakt_import_uses_provider_default_language(self):
        self.user.settings.tmdb_metadata_language = "pt-BR"
        self.user.settings.save(update_fields=["tmdb_metadata_language"])
        movie = Movie.objects.create(external_id="999", title="Fight Club")

        with patch("apps.trakt.sync.movie_services.import_movie", return_value=movie) as import_movie:
            _ensure_movie(
                self.user,
                {"ids": {"tmdb": 550, "trakt": 5500}},
            )

        import_movie.assert_called_once_with("tmdb", "550", language="en-US")

    def test_trakt_show_import_prefers_tvdb_and_falls_back_to_tmdb(self):
        fallback_show = Show.objects.create(
            provider="tmdb",
            external_id="9999",
            name="The Series",
        )

        with patch(
            "apps.trakt.sync.tv_services.import_show",
            side_effect=[ProviderError("missing"), fallback_show],
        ) as import_show:
            _ensure_show(
                self.user,
                {
                    "title": "The Series",
                    "ids": {"trakt": 1000, "tmdb": 100, "tvdb": 200},
                },
            )

        self.assertEqual(import_show.call_count, 2)
        self.assertEqual(import_show.call_args_list[0].kwargs, {
            "provider": "tvdb",
            "language": "eng",
        })
        self.assertEqual(import_show.call_args_list[1].kwargs, {
            "provider": "tmdb",
            "language": "en-US",
        })

    def test_stronger_trakt_or_tmdb_identity_wins_over_shared_tvdb_id(self):
        show = Show.objects.create(
            provider="tmdb",
            external_id="109958",
            trakt_id="169891",
            tmdb_id="109958",
            tvdb_id="345246",
            name="The Haunting of Bly Manor",
        )
        UserShow.objects.create(user=self.user, show=show)

        match = _find_by_ids(
            Show,
            {
                "ids": {
                    "trakt": 134526,
                    "tmdb": 72844,
                    "tvdb": 345246,
                }
            },
            user=self.user,
            user_state_relation="user_states",
        )

        self.assertIsNone(match)

    def test_existing_catalog_scalars_use_default_titles(self):
        movie = Movie.objects.create(
            external_id="550",
            trakt_id="5500",
            title="Clube da Luta",
            original_title="Fight Club",
            translations={"pt-BR": {"title": "Clube da Luta"}},
        )
        show = Show.objects.create(
            external_id="100",
            trakt_id="1000",
            name="A Série",
            translations={"eng": {"name": "The Series"}},
        )

        _ensure_movie(self.user, {"ids": {"trakt": 5500, "tmdb": 550}})
        _ensure_show(self.user, {"ids": {"trakt": 1000, "tmdb": 100}, "title": "The Series"})

        movie.refresh_from_db()
        show.refresh_from_db()
        self.assertEqual(movie.title, "Fight Club")
        self.assertEqual(movie.translations["en-US"]["title"], "Fight Club")
        self.assertEqual(show.name, "The Series")
        self.assertEqual(show.translations["eng"]["name"], "The Series")

    def test_cached_episode_history_prevents_reposting_on_incremental_sync(self):
        show = Show.objects.create(external_id="100", trakt_id="1000", name="The Show")
        season = Season.objects.create(show=show, season_number=1, name="Season 1")
        episode = Episode.objects.create(
            show=show,
            season=season,
            season_number=1,
            episode_number=1,
            trakt_id="1001",
            name="Pilot",
        )
        UserShow.objects.create(user=self.user, show=show)
        watched_at = timezone.now() - timedelta(hours=1)
        snapshot = TraktSnapshot(
            [],
            [],
            [],
            [],
            [],
            watched_episodes=[
                {
                    "watched_at": watched_at.isoformat(),
                    "show": {"ids": {"trakt": 1000}},
                    "episode": {
                        "season": 1,
                        "number": 1,
                        "ids": {"trakt": 1001},
                    },
                }
            ],
        )
        first_client = FakeTraktClient(snapshot)

        sync_account(self.account.id, client_factory=client_factory(first_client))

        self.assertTrue(UserEpisode.objects.filter(user=self.user, episode=episode).exists())
        self.assertTrue(self.account.watched_episode_cache.exists())

        second_client = FakeTraktClient(TraktSnapshot([], [], [], [], []))
        sync_account(self.account.id, client_factory=client_factory(second_client))

        self.assertFalse(second_client.history_calls)
        self.assertIsNotNone(second_client.snapshot_calls[0]["episode_history_start_at"])

    def test_episode_catalog_reconciliation_uses_batch_queries(self):
        show = Show.objects.create(external_id="100", trakt_id="1000", name="The Show")
        watched = [
            WatchedEpisode(
                show={"ids": {"trakt": 1000}},
                episode={
                    "season": 1,
                    "number": number,
                    "ids": {"trakt": 1000 + number},
                    "title": f"Episode {number}",
                },
                season_number=1,
                episode_number=number,
                watched_at=timezone.now(),
            )
            for number in (1, 2)
        ]

        with self.assertNumQueries(4):
            episodes = _ensure_episodes_batch([(item, show) for item in watched])

        self.assertEqual(len(episodes), 2)
        self.assertEqual(Season.objects.filter(show=show).count(), 1)

    def test_episode_position_wins_when_trakt_id_belongs_to_another_show(self):
        target = Show.objects.create(external_id="100", trakt_id="1000", name="Target")
        target_season = Season.objects.create(show=target, season_number=1, name="Season 1")
        target_episode = Episode.objects.create(
            show=target,
            season=target_season,
            season_number=1,
            episode_number=1,
            trakt_id="old-target-id",
            name="Target episode",
        )
        other = Show.objects.create(external_id="200", trakt_id="2000", name="Other")
        other_season = Season.objects.create(show=other, season_number=1, name="Season 1")
        other_episode = Episode.objects.create(
            show=other,
            season=other_season,
            season_number=1,
            episode_number=2,
            trakt_id="shared-wrong-id",
            name="Other episode",
        )
        watched = WatchedEpisode(
            show={"ids": {"trakt": 1000}},
            episode={
                "season": 1,
                "number": 1,
                "ids": {"trakt": "shared-wrong-id"},
            },
            season_number=1,
            episode_number=1,
            watched_at=timezone.now(),
        )

        _ensure_episodes_batch([(watched, target)])

        target_episode.refresh_from_db()
        other_episode.refresh_from_db()
        self.assertEqual(target_episode.trakt_id, "shared-wrong-id")
        self.assertIsNone(other_episode.trakt_id)

    def test_local_watched_movie_is_sent_when_trakt_does_not_have_it(self):
        seen_at = timezone.now() - timedelta(hours=2)
        movie = Movie.objects.create(
            external_id="550",
            trakt_id="5500",
            title="Fight Club",
        )
        UserMovie.objects.create(
            user=self.user,
            movie=movie,
            is_seen=True,
            seen_at=seen_at,
        )
        client = FakeTraktClient(TraktSnapshot([], [], [], [], []))

        sync_account(self.account.id, client_factory=client_factory(client))

        self.assertEqual(len(client.history_calls), 1)
        movies, shows = client.history_calls[0]
        self.assertEqual(movies[0]["ids"]["trakt"], 5500)
        self.assertFalse(shows)

    def test_local_watched_episode_is_sent_after_initial_sync_when_trakt_does_not_have_it(self):
        self.account.initial_sync_complete = True
        self.account.save(update_fields=["initial_sync_complete"])
        show = Show.objects.create(
            external_id="100",
            trakt_id="1000",
            name="The Show",
        )
        season = Season.objects.create(show=show, season_number=1, name="Season 1")
        episode = Episode.objects.create(
            show=show,
            season=season,
            season_number=1,
            episode_number=1,
            trakt_id="1001",
            name="Pilot",
        )
        seen_at = timezone.now() - timedelta(hours=2)
        UserEpisode.objects.create(user=self.user, episode=episode, seen_at=seen_at)
        client = FakeTraktClient(TraktSnapshot([], [], [], [], []))

        sync_account(self.account.id, client_factory=client_factory(client))

        self.assertEqual(len(client.history_calls), 1)
        movies, shows = client.history_calls[0]
        self.assertFalse(movies)
        self.assertEqual(shows[0]["ids"]["trakt"], 1000)
        self.assertEqual(shows[0]["seasons"][0]["episodes"][0]["ids"]["trakt"], 1001)

    def test_local_watched_episode_is_not_reposted_when_trakt_already_has_it(self):
        self.account.initial_sync_complete = True
        self.account.save(update_fields=["initial_sync_complete"])
        show = Show.objects.create(
            external_id="100",
            trakt_id="1000",
            name="The Show",
        )
        season = Season.objects.create(show=show, season_number=1, name="Season 1")
        episode = Episode.objects.create(
            show=show,
            season=season,
            season_number=1,
            episode_number=1,
            trakt_id="1001",
            name="Pilot",
        )
        seen_at = timezone.now()
        UserEpisode.objects.create(user=self.user, episode=episode, seen_at=seen_at)
        client = FakeTraktClient(
            TraktSnapshot(
                [],
                [],
                [],
                [],
                [],
                watched_episodes=[
                    {
                        "watched_at": seen_at.replace(microsecond=0).isoformat(),
                        "show": {"ids": {"trakt": 1000}},
                        "episode": {
                            "season": 1,
                            "number": 1,
                            "ids": {"trakt": 1001},
                        },
                    }
                ],
            )
        )

        sync_account(self.account.id, client_factory=client_factory(client))

        self.assertFalse(client.history_calls)

    def test_episode_history_intent_is_not_reposted_after_remote_accepts_it(self):
        show = Show.objects.create(external_id="100", trakt_id="1000", name="The Show")
        season = Season.objects.create(show=show, season_number=1, name="Season 1")
        episode = Episode.objects.create(
            show=show,
            season=season,
            season_number=1,
            episode_number=1,
            trakt_id="1001",
            name="Pilot",
        )
        UserShow.objects.create(user=self.user, show=show)
        seen_at = timezone.now() - timedelta(hours=1)
        UserEpisode.objects.create(user=self.user, episode=episode, seen_at=seen_at)
        record_intent(
            self.user,
            TraktSyncIntent.Kind.EPISODE_HISTORY,
            {
                "show": {"ids": {"trakt": 1000}},
                "seasons": [
                    {
                        "number": 1,
                        "episodes": [
                            {
                                "number": 1,
                                "ids": {"trakt": 1001},
                                "watched_at": seen_at.isoformat(),
                            }
                        ],
                    }
                ],
            },
        )
        client = FakeTraktClient(
            TraktSnapshot(
                [],
                [],
                [],
                [
                    {
                        "show": {"ids": {"trakt": 1000}},
                        "seasons": [
                            {
                                "number": 1,
                                "episodes": [
                                    {
                                        "number": 1,
                                        "last_watched_at": seen_at.replace(microsecond=0).isoformat(),
                                        "episode": {"ids": {"trakt": 1001}},
                                    }
                                ],
                            }
                        ],
                    }
                ],
                [],
            )
        )

        sync_account(self.account.id, client_factory=client_factory(client))

        self.assertFalse(client.history_calls)
        self.assertFalse(
            TraktSyncIntent.objects.filter(
                user=self.user,
                kind=TraktSyncIntent.Kind.EPISODE_HISTORY,
            ).exists()
        )

    def test_watched_episode_is_merged_with_local_state(self):
        show = Show.objects.create(external_id="100", trakt_id="1000", name="The Show")
        season = Season.objects.create(show=show, season_number=1, name="Season 1")
        episode = Episode.objects.create(
            show=show,
            season=season,
            season_number=1,
            episode_number=1,
            trakt_id="1001",
            name="Pilot",
        )
        local_seen_at = timezone.now() - timedelta(days=2)
        UserShow.objects.create(user=self.user, show=show, status=UserShow.Status.TRACKED)
        UserEpisode.objects.create(user=self.user, episode=episode, seen_at=local_seen_at)
        remote_seen_at = timezone.now()
        client = FakeTraktClient(
            TraktSnapshot(
                [],
                [],
                [],
                [
                    {
                        "show": {"ids": {"trakt": 1000}},
                        "seasons": [
                            {
                                "number": 1,
                                "episodes": [
                                    {
                                        "number": 1,
                                        "last_watched_at": remote_seen_at.isoformat(),
                                        "episode": {"ids": {"trakt": 1001}},
                                    }
                                ],
                            }
                        ],
                    }
                ],
                [],
            )
        )

        sync_account(self.account.id, client_factory=client_factory(client))

        self.assertAlmostEqual(
            UserEpisode.objects.get(user=self.user, episode=episode).seen_at.timestamp(),
            remote_seen_at.timestamp(),
            places=3,
        )

    def test_duplicate_remote_episode_records_keep_latest_timestamp(self):
        show = Show.objects.create(external_id="100", trakt_id="1000", name="The Show")
        season = Season.objects.create(show=show, season_number=1, name="Season 1")
        episode = Episode.objects.create(
            show=show,
            season=season,
            season_number=1,
            episode_number=1,
            trakt_id="1001",
            name="Pilot",
        )
        UserShow.objects.create(user=self.user, show=show)
        first = timezone.now() - timedelta(days=1)
        second = timezone.now()
        watched_show = lambda timestamp: {
            "show": {"ids": {"trakt": 1000}},
            "seasons": [
                {
                    "number": 1,
                    "episodes": [
                        {
                            "number": 1,
                            "last_watched_at": timestamp.isoformat(),
                            "episode": {"ids": {"trakt": 1001}},
                        }
                    ],
                }
            ],
        }
        client = FakeTraktClient(
            TraktSnapshot([], [], [], [watched_show(first), watched_show(second)], [])
        )

        sync_account(self.account.id, client_factory=client_factory(client))

        self.assertAlmostEqual(
            UserEpisode.objects.get(user=self.user, episode=episode).seen_at.timestamp(),
            second.timestamp(),
            places=3,
        )

    def test_watched_special_episode_in_season_zero_is_synced(self):
        show = Show.objects.create(external_id="100", trakt_id="1000", name="The Show")
        client = FakeTraktClient(
            TraktSnapshot(
                [],
                [],
                [],
                [
                    {
                        "show": {"ids": {"trakt": 1000}},
                        "seasons": [
                            {
                                "number": 0,
                                "episodes": [
                                    {
                                        "number": 1,
                                        "last_watched_at": timezone.now().isoformat(),
                                        "episode": {"ids": {"trakt": 1001}},
                                    }
                                ],
                            }
                        ],
                    }
                ],
                [],
            )
        )

        sync_account(self.account.id, client_factory=client_factory(client))

        self.assertTrue(
            UserEpisode.objects.filter(
                user=self.user,
                episode__show=show,
                episode__season_number=0,
                episode__episode_number=1,
            ).exists()
        )

    def test_watchlist_only_show_is_tracked_without_watched_episodes(self):
        show = Show.objects.create(external_id="100", trakt_id="1000", name="The Show")
        client = FakeTraktClient(
            TraktSnapshot(
                [],
                [{"show": {"ids": {"trakt": 1000}, "title": "The Show"}}],
                [],
                [],
                [],
            )
        )

        sync_account(self.account.id, client_factory=client_factory(client))

        user_show = UserShow.objects.get(user=self.user, show=show)
        self.assertEqual(user_show.status, UserShow.Status.TRACKED)
        self.assertTrue(user_show.on_watchlist)
        self.assertFalse(UserEpisode.objects.filter(user=self.user).exists())

    def test_remote_watchlist_preserves_local_paused_status(self):
        show = Show.objects.create(external_id="100", trakt_id="1000", name="The Show")
        UserShow.objects.create(
            user=self.user,
            show=show,
            status=UserShow.Status.PAUSED,
        )
        client = FakeTraktClient(
            TraktSnapshot(
                [],
                [{"show": {"ids": {"trakt": 1000}, "title": "The Show"}}],
                [],
                [],
                [],
            )
        )

        sync_account(self.account.id, client_factory=client_factory(client))

        user_show = UserShow.objects.get(user=self.user, show=show)
        self.assertEqual(user_show.status, UserShow.Status.PAUSED)
        self.assertTrue(user_show.on_watchlist)

    def test_remote_watched_episode_preserves_local_paused_status(self):
        show = Show.objects.create(external_id="100", trakt_id="1000", name="The Show")
        season = Season.objects.create(show=show, season_number=1, name="Season 1")
        episode = Episode.objects.create(
            show=show,
            season=season,
            season_number=1,
            episode_number=1,
            trakt_id="1001",
            name="Pilot",
        )
        UserShow.objects.create(
            user=self.user,
            show=show,
            status=UserShow.Status.PAUSED,
        )
        client = FakeTraktClient(
            TraktSnapshot(
                [],
                [],
                [],
                [],
                [],
                watched_episodes=[
                    {
                        "watched_at": timezone.now().isoformat(),
                        "show": {"ids": {"trakt": 1000}},
                        "episode": {
                            "season": 1,
                            "number": 1,
                            "ids": {"trakt": 1001},
                        },
                    }
                ],
            )
        )

        sync_account(self.account.id, client_factory=client_factory(client))

        user_show = UserShow.objects.get(user=self.user, show=show)
        self.assertEqual(user_show.status, UserShow.Status.PAUSED)
        self.assertTrue(UserEpisode.objects.filter(user=self.user, episode=episode).exists())

    def test_remote_dropped_show_preserves_local_paused_status(self):
        show = Show.objects.create(external_id="100", trakt_id="1000", name="The Show")
        UserShow.objects.create(
            user=self.user,
            show=show,
            status=UserShow.Status.PAUSED,
        )
        client = FakeTraktClient(
            TraktSnapshot(
                [],
                [],
                [],
                [],
                [{"show": {"ids": {"trakt": 1000}, "title": "The Show"}}],
            )
        )

        sync_account(self.account.id, client_factory=client_factory(client))

        self.assertEqual(
            UserShow.objects.get(user=self.user, show=show).status,
            UserShow.Status.PAUSED,
        )

    def test_remote_dropped_show_keeps_history_and_dropped_status(self):
        show = Show.objects.create(external_id="100", trakt_id="1000", name="The Show")
        season = Season.objects.create(show=show, season_number=1, name="Season 1")
        episode = Episode.objects.create(
            show=show,
            season=season,
            season_number=1,
            episode_number=1,
            name="Pilot",
        )
        UserShow.objects.create(user=self.user, show=show)
        UserEpisode.objects.create(user=self.user, episode=episode)
        client = FakeTraktClient(
            TraktSnapshot(
                [],
                [],
                [],
                [],
                [{"show": {"ids": {"trakt": 1000}, "title": "The Show"}}],
            )
        )

        sync_account(self.account.id, client_factory=client_factory(client))

        self.assertEqual(
            UserShow.objects.get(user=self.user, show=show).status,
            UserShow.Status.DROPPED,
        )
        self.assertTrue(UserEpisode.objects.filter(user=self.user, episode=episode).exists())

    def test_remote_watchlist_removal_clears_local_watchlist(self):
        self.account.initial_sync_complete = True
        self.account.save(update_fields=["initial_sync_complete"])
        movie = Movie.objects.create(
            external_id="550",
            trakt_id="5500",
            title="Fight Club",
        )
        UserMovie.objects.create(user=self.user, movie=movie, on_watchlist=True)
        client = FakeTraktClient(TraktSnapshot([], [], [], [], []))

        sync_account(self.account.id, client_factory=client_factory(client))

        self.assertFalse(UserMovie.objects.get(user=self.user, movie=movie).on_watchlist)

    def test_initial_sync_preserves_preexisting_local_watchlist(self):
        movie = Movie.objects.create(
            external_id="550",
            trakt_id="5500",
            title="Fight Club",
        )
        UserMovie.objects.create(user=self.user, movie=movie, on_watchlist=True)
        client = FakeTraktClient(TraktSnapshot([], [], [], [], []))

        sync_account(self.account.id, client_factory=client_factory(client))

        self.assertTrue(UserMovie.objects.get(user=self.user, movie=movie).on_watchlist)
        self.assertTrue(client.watchlist_calls)

    def test_pending_local_drop_wins_over_stale_remote_snapshot(self):
        show = Show.objects.create(external_id="100", trakt_id="1000", name="The Show")
        UserShow.objects.create(
            user=self.user,
            show=show,
            status=UserShow.Status.DROPPED,
        )
        record_intent(
            self.user,
            TraktSyncIntent.Kind.SHOW_DROPPED,
            {"show": {"ids": {"trakt": 1000}}},
        )
        client = FakeTraktClient(TraktSnapshot([], [], [], [], []))

        sync_account(self.account.id, client_factory=client_factory(client))

        self.assertEqual(
            UserShow.objects.get(user=self.user, show=show).status,
            UserShow.Status.DROPPED,
        )
        self.assertTrue(client.dropped_calls)
        self.assertFalse(client.dropped_calls[0][1])

    def test_acknowledgement_does_not_delete_a_concurrently_updated_intent(self):
        intent = record_intent(
            self.user,
            TraktSyncIntent.Kind.MOVIE_WATCHLIST,
            {"ids": {"trakt": 5500}},
        )
        TraktSyncIntent.objects.filter(id=intent.id).update(
            updated_at=timezone.now() + timedelta(seconds=1),
        )

        _acknowledge_intents(
            [intent],
            RemoteSnapshot(
                watchlist_movies={"trakt:5500": {"ids": {"trakt": 5500}}},
            ),
        )

        self.assertTrue(TraktSyncIntent.objects.filter(id=intent.id).exists())
