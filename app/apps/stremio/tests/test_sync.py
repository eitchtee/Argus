import base64
import zlib
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase

from apps.movies.models import Movie, UserMovie
from apps.stremio.models import StremioAccount
from apps.stremio.codec import decode_watched_bitfield, encode_watched_bitfield
from apps.stremio.sync import (
    SyncReport,
    _apply_remote,
    _acknowledge_intents,
    build_outbound_items,
    normalize_items,
    _movie_state_is_zero,
    _ensure_movie,
    _ensure_show,
    sync_account,
)
from apps.trakt.sync import LocalSnapshot
from apps.tv.models import Episode, Season, Show, UserEpisode, UserShow
from apps.stremio.models import StremioSyncIntent


class StremioSyncNormalizationTests(TestCase):
    def test_normalize_items_extracts_movie_and_series_episode_state(self):
        items = [
            {
                "_id": "tt0137523",
                "type": "movie",
                "name": "Fight Club",
                "state": {
                    "timesWatched": 1,
                    "lastWatched": "2026-08-01T10:00:00Z",
                },
            },
            {
                "_id": "tt0944947",
                "type": "series",
                "name": "Game of Thrones",
                "state": {"watched": "tt0944947:1:2:2:eJxjAgAAAwAD"},
            },
            {"_id": "unsupported", "type": "channel", "state": {}},
        ]

        snapshot = normalize_items(
            items,
            cinemeta_getter=lambda _imdb_id: {
                "videos": [
                    {"id": "tt0944947:1:1", "season": 1, "episode": 1},
                    {"id": "tt0944947:1:2", "season": 1, "episode": 2},
                ]
            },
        )

        self.assertEqual(snapshot.library_ids, {"tt0137523", "tt0944947"})
        self.assertEqual(snapshot.watched_movies["tt0137523"].watched_at, datetime(2026, 8, 1, 10, tzinfo=timezone.utc))
        self.assertEqual(
            set(snapshot.watched_episodes),
            {("tt0944947", 1, 2)},
        )


class StremioMovieStateTests(SimpleTestCase):
    def test_flagged_movie_is_imported_as_watched_without_playback(self):
        snapshot = normalize_items(
            [
                {
                    "_id": "tt0137523",
                    "type": "movie",
                    "state": {"timesWatched": 0, "flaggedWatched": 1},
                }
            ],
            cinemeta_getter=lambda _imdb_id: {},
        )

        self.assertIn("tt0137523", snapshot.watched_movies)

    def test_flagged_movie_is_not_treated_as_zero_state(self):
        self.assertFalse(
            _movie_state_is_zero(
                {"state": {"timesWatched": 0, "flaggedWatched": 1}}
            )
        )


class StremioOutboundTests(TestCase):
    def test_new_movie_item_includes_local_poster(self):
        user = get_user_model().objects.create_user("user@example.com", password="pw")
        movie = Movie.objects.create(
            imdb_id="tt0137523",
            title="Fight Club",
            external_id="550",
            tmdb_id="550",
            poster_path="/poster.jpg",
        )
        movie_state = UserMovie.objects.create(user=user, movie=movie, is_seen=True)

        changes = build_outbound_items(
            LocalSnapshot([], [movie_state], [], [], []),
            [],
            [],
            cinemeta_getter=lambda _imdb_id: {},
            initial=False,
        )

        self.assertEqual(changes[0]["poster"], "https://image.tmdb.org/t/p/w342/poster.jpg")

    def test_missing_remote_poster_is_filled_from_local_media(self):
        user = get_user_model().objects.create_user("user@example.com", password="pw")
        movie = Movie.objects.create(
            imdb_id="tt0137523",
            title="Fight Club",
            external_id="550",
            tmdb_id="550",
            poster_path="/poster.jpg",
        )
        movie_state = UserMovie.objects.create(user=user, movie=movie, on_watchlist=True)

        changes = build_outbound_items(
            LocalSnapshot([movie_state], [], [], [], []),
            [
                {
                    "_id": "tt0137523",
                    "type": "movie",
                    "name": "Fight Club",
                    "state": {},
                }
            ],
            [],
            cinemeta_getter=lambda _imdb_id: {},
            initial=False,
        )

        self.assertEqual(changes[0]["poster"], "https://image.tmdb.org/t/p/w342/poster.jpg")

    def test_history_only_outbound_item_is_temporary_not_removed(self):
        user = get_user_model().objects.create_user("user@example.com", password="pw")
        movie = Movie.objects.create(
            imdb_id="tt0137523",
            title="Fight Club",
            external_id="550",
            tmdb_id="550",
        )
        movie_state = UserMovie.objects.create(user=user, movie=movie, is_seen=True)

        changes = build_outbound_items(
            LocalSnapshot([], [movie_state], [], [], []),
            [],
            [],
            cinemeta_getter=lambda _imdb_id: {},
            initial=False,
        )

        self.assertEqual(len(changes), 1)
        self.assertFalse(changes[0]["removed"])
        self.assertTrue(changes[0]["temp"])

    def test_outbound_metadata_timestamps_are_iso8601_strings(self):
        user = get_user_model().objects.create_user("user@example.com", password="pw")
        movie = Movie.objects.create(
            imdb_id="tt0137523",
            title="Fight Club",
            external_id="550",
            tmdb_id="550",
        )
        movie_state = UserMovie.objects.create(user=user, movie=movie, is_seen=True)

        changes = build_outbound_items(
            LocalSnapshot([], [movie_state], [], [], []),
            [],
            [],
            cinemeta_getter=lambda _imdb_id: {},
            initial=False,
        )

        self.assertEqual(len(changes), 1)
        self.assertRegex(changes[0]["_ctime"], r"^\d{4}-\d{2}-\d{2}T.*Z$")
        self.assertRegex(changes[0]["_mtime"], r"^\d{4}-\d{2}-\d{2}T.*Z$")

    def test_new_outbound_item_contains_complete_stremio_state_defaults(self):
        user = get_user_model().objects.create_user("user@example.com", password="pw")
        movie = Movie.objects.create(
            imdb_id="tt0137523",
            title="Fight Club",
            external_id="550",
            tmdb_id="550",
        )
        movie_state = UserMovie.objects.create(user=user, movie=movie, is_seen=True)

        changes = build_outbound_items(
            LocalSnapshot([], [movie_state], [], [], []),
            [],
            [],
            cinemeta_getter=lambda _imdb_id: {},
            initial=False,
        )

        self.assertEqual(
            {
                "timeWatched": 0,
                "timeOffset": 0,
                "overallTimeWatched": 0,
                "timesWatched": 0,
                "flaggedWatched": 0,
                "duration": 0,
                "noNotif": False,
            },
            {
                key: changes[0]["state"][key]
                for key in (
                    "timeWatched",
                    "timeOffset",
                    "overallTimeWatched",
                    "timesWatched",
                    "flaggedWatched",
                    "duration",
                    "noNotif",
                )
            },
        )

    def test_outbound_changes_keep_unknown_remote_fields(self):
        user = get_user_model().objects.create_user("user@example.com", password="pw")
        movie = Movie.objects.create(
            imdb_id="tt0137523",
            title="Fight Club",
            external_id="550",
            tmdb_id="550",
        )
        UserMovie.objects.create(
            user=user,
            movie=movie,
            is_seen=True,
            seen_at=datetime(2026, 8, 1, 10, tzinfo=timezone.utc),
        )

        local = LocalSnapshot(
            movie_watchlist=[],
            movie_history=list(UserMovie.objects.filter(user=user)),
            show_watchlist=[],
            show_dropped=[],
            episode_history=[],
        )
        remote = {
            "_id": "tt0137523",
            "type": "movie",
            "name": "Fight Club",
            "addonData": {"keep": True},
            "state": {"timesWatched": 0, "lastWatched": None},
        }

        changes = build_outbound_items(
            local,
            [remote],
            [],
            cinemeta_getter=lambda _imdb_id: {},
            initial=False,
        )

        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0]["addonData"], {"keep": True})
        self.assertEqual(changes[0]["state"]["timesWatched"], 1)

    def test_outbound_watch_timestamp_is_iso8601_string(self):
        user = get_user_model().objects.create_user("user@example.com", password="pw")
        movie = Movie.objects.create(
            imdb_id="tt0137523",
            title="Fight Club",
            external_id="550",
            tmdb_id="550",
        )
        seen_at = datetime(2026, 8, 1, 10, 0, 0, 123000, tzinfo=timezone.utc)
        movie_state = UserMovie.objects.create(
            user=user,
            movie=movie,
            is_seen=True,
            seen_at=seen_at,
        )

        changes = build_outbound_items(
            LocalSnapshot([], [movie_state], [], [], []),
            [],
            [],
            cinemeta_getter=lambda _imdb_id: {},
            initial=False,
        )

        self.assertEqual(
            changes[0]["state"]["lastWatched"],
            "2026-08-01T10:00:00.123Z",
        )

    def test_series_watchlist_and_episode_history_share_one_outbound_candidate(self):
        user = get_user_model().objects.create_user("user@example.com", password="pw")
        show = Show.objects.create(
            imdb_id="tt0944947",
            name="Game of Thrones",
            external_id="1399",
            tmdb_id="1399",
        )
        user_show = UserShow.objects.create(user=user, show=show, on_watchlist=True)
        season = Season.objects.create(show=show, season_number=1)
        episode = Episode.objects.create(
            show=show,
            season=season,
            season_number=1,
            episode_number=2,
            name="The Kingsroad",
        )
        user_episode = UserEpisode.objects.create(user=user, episode=episode)
        local = LocalSnapshot(
            movie_watchlist=[],
            movie_history=[],
            show_watchlist=[user_show],
            show_dropped=[],
            episode_history=[user_episode],
        )

        changes = build_outbound_items(
            local,
            [],
            [],
            cinemeta_getter=lambda _imdb_id: {
                "videos": [{"id": "tt0944947:1:2", "season": 1, "episode": 2}]
            },
            initial=True,
        )

        self.assertEqual(len(changes), 1)
        self.assertFalse(changes[0]["removed"])
        self.assertFalse(changes[0]["temp"])
        self.assertEqual(
            decode_watched_bitfield(
                changes[0]["state"]["watched"],
                ["tt0944947:1:2"],
            ),
            {"tt0944947:1:2"},
        )

    def test_dropped_show_history_is_pushed_as_temporary_item(self):
        user = get_user_model().objects.create_user("user@example.com", password="pw")
        show = Show.objects.create(
            imdb_id="tt0944947",
            name="Game of Thrones",
            external_id="1399",
            tmdb_id="1399",
        )
        user_show = UserShow.objects.create(
            user=user,
            show=show,
            status=UserShow.Status.DROPPED,
        )
        season = Season.objects.create(show=show, season_number=1)
        episode = Episode.objects.create(
            show=show,
            season=season,
            season_number=1,
            episode_number=2,
            name="The Kingsroad",
        )
        user_episode = UserEpisode.objects.create(user=user, episode=episode)

        changes = build_outbound_items(
            LocalSnapshot([], [], [], [user_show], [user_episode]),
            [
                {
                    "_id": "tt0944947",
                    "type": "series",
                    "name": "Game of Thrones",
                    "removed": False,
                    "temp": False,
                    "state": {},
                }
            ],
            [],
            cinemeta_getter=lambda _imdb_id: {
                "videos": [{"id": "tt0944947:1:2", "season": 1, "episode": 2}]
            },
            initial=False,
        )

        self.assertEqual(len(changes), 1)
        self.assertFalse(changes[0]["removed"])
        self.assertTrue(changes[0]["temp"])
        self.assertEqual(
            decode_watched_bitfield(
                changes[0]["state"]["watched"],
                ["tt0944947:1:2"],
            ),
            {"tt0944947:1:2"},
        )

    def test_dropped_show_without_history_tombstones_existing_library_item(self):
        user = get_user_model().objects.create_user("user@example.com", password="pw")
        show = Show.objects.create(
            imdb_id="tt0944947",
            name="Game of Thrones",
            external_id="1399",
            tmdb_id="1399",
        )
        user_show = UserShow.objects.create(
            user=user,
            show=show,
            status=UserShow.Status.DROPPED,
        )

        changes = build_outbound_items(
            LocalSnapshot([], [], [], [user_show], []),
            [
                {
                    "_id": "tt0944947",
                    "type": "series",
                    "name": "Game of Thrones",
                    "removed": False,
                    "temp": False,
                    "state": {},
                }
            ],
            [],
            cinemeta_getter=lambda _imdb_id: {},
            initial=False,
        )

        self.assertEqual(len(changes), 1)
        self.assertTrue(changes[0]["removed"])
        self.assertFalse(changes[0]["temp"])

    def test_outbound_projection_updates_tmdb_remote_identity_without_duplicate(self):
        user = get_user_model().objects.create_user("user@example.com", password="pw")
        movie = Movie.objects.create(
            imdb_id="tt0137523",
            title="Fight Club",
            external_id="550",
            tmdb_id="550",
        )
        movie_state = UserMovie.objects.create(user=user, movie=movie, is_seen=True)
        local = LocalSnapshot(
            movie_watchlist=[],
            movie_history=[movie_state],
            show_watchlist=[],
            show_dropped=[],
            episode_history=[],
        )
        remote = {
            "_id": "tmdb:550",
            "type": "movie",
            "name": "Fight Club",
            "state": {"timesWatched": 0, "lastWatched": None},
        }

        changes = build_outbound_items(
            local,
            [remote],
            [],
            cinemeta_getter=lambda _imdb_id: {},
            initial=False,
        )

        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0]["_id"], "tmdb:550")
        self.assertEqual(changes[0]["state"]["timesWatched"], 1)

    def test_tvdb_only_movie_is_projected_with_a_tvdb_identity(self):
        user = get_user_model().objects.create_user("user@example.com", password="pw")
        movie = Movie.objects.create(
            provider="tvdb",
            external_id="123",
            tvdb_id="123",
            title="TVDB Movie",
        )
        movie_state = UserMovie.objects.create(user=user, movie=movie, is_seen=True)

        changes = build_outbound_items(
            LocalSnapshot([ ], [movie_state], [], [], []),
            [],
            [],
            cinemeta_getter=lambda _imdb_id: {},
            initial=False,
        )

        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0]["_id"], "tvdb:123")

    def test_malformed_remote_episode_bitfield_is_not_overwritten(self):
        user = get_user_model().objects.create_user("user@example.com", password="pw")
        show = Show.objects.create(
            imdb_id="tt0944947",
            name="Game of Thrones",
            external_id="1399",
            tmdb_id="1399",
        )
        season = Season.objects.create(show=show, season_number=1)
        episode = Episode.objects.create(
            show=show,
            season=season,
            season_number=1,
            episode_number=1,
            name="Winter Is Coming",
        )
        user_episode = UserEpisode.objects.create(user=user, episode=episode)
        remote = {
            "_id": "tt0944947",
            "type": "series",
            "name": "Game of Thrones",
            "state": {"watched": "not-a-valid-bitfield"},
        }

        changes = build_outbound_items(
            LocalSnapshot([], [], [], [], [user_episode]),
            [remote],
            [],
            cinemeta_getter=lambda _imdb_id: {
                "videos": [{"id": "tt0944947:1:1", "season": 1, "episode": 1}]
            },
            initial=False,
        )

        self.assertEqual(changes, [])

    def test_outbound_episode_projection_keeps_newer_remote_last_watched(self):
        user = get_user_model().objects.create_user("user@example.com", password="pw")
        show = Show.objects.create(
            imdb_id="tt0944947",
            name="Game of Thrones",
            external_id="1399",
            tmdb_id="1399",
        )
        season = Season.objects.create(show=show, season_number=1)
        episode = Episode.objects.create(
            show=show,
            season=season,
            season_number=1,
            episode_number=1,
            name="Winter Is Coming",
        )
        second_episode = Episode.objects.create(
            show=show,
            season=season,
            season_number=1,
            episode_number=2,
            name="The Kingsroad",
        )
        user_episode = UserEpisode.objects.create(
            user=user,
            episode=episode,
            seen_at=datetime(2026, 8, 1, 10, tzinfo=timezone.utc),
        )
        second_user_episode = UserEpisode.objects.create(
            user=user,
            episode=second_episode,
            seen_at=datetime(2026, 8, 1, 10, tzinfo=timezone.utc),
        )
        video_ids = ["tt0944947:1:1", "tt0944947:1:2"]
        remote_last_watched = "2026-08-05T10:00:00Z"
        remote = {
            "_id": "tt0944947",
            "type": "series",
            "name": "Game of Thrones",
            "state": {
                "watched": encode_watched_bitfield({video_ids[0]}, video_ids),
                "lastWatched": remote_last_watched,
            },
        }

        changes = build_outbound_items(
            LocalSnapshot([], [], [], [], [user_episode, second_user_episode]),
            [remote],
            [],
            cinemeta_getter=lambda _imdb_id: {
                "videos": [{"id": video_ids[0], "season": 1, "episode": 1}]
                + [{"id": video_ids[1], "season": 1, "episode": 2}]
            },
            initial=False,
        )

        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0]["state"]["lastWatched"], remote_last_watched)

    def test_movie_and_series_candidates_with_same_id_stay_separate(self):
        user = get_user_model().objects.create_user("user@example.com", password="pw")
        movie = Movie.objects.create(
            provider="tmdb",
            external_id="550",
            tmdb_id="550",
            title="Fight Club",
        )
        show = Show.objects.create(
            provider="tmdb",
            external_id="550",
            tmdb_id="550",
            name="A Show With A Colliding ID",
        )
        movie_state = UserMovie.objects.create(user=user, movie=movie, is_seen=True)
        show_state = UserShow.objects.create(user=user, show=show, on_watchlist=True)

        changes = build_outbound_items(
            LocalSnapshot([], [movie_state], [show_state], [], []),
            [],
            [],
            cinemeta_getter=lambda _imdb_id: {},
            initial=False,
        )

        self.assertEqual({change["type"] for change in changes}, {"movie", "series"})

    def test_episode_history_intent_accepts_season_zero(self):
        user = get_user_model().objects.create_user("user@example.com", password="pw")
        show = Show.objects.create(
            imdb_id="tt0944947",
            name="Game of Thrones",
            external_id="1399",
            tmdb_id="1399",
        )
        video_ids = ["tt0944947:0:1"]
        watched = encode_watched_bitfield(set(video_ids), video_ids)
        intent = StremioSyncIntent.objects.create(
            user=user,
            kind=StremioSyncIntent.Kind.EPISODE_HISTORY,
            identity_key="tt0944947:0:1",
            payload={
                "show": {"ids": {"imdb": "tt0944947"}},
                "seasons": [{"number": 0, "episodes": [{"number": 1}]}],
            },
            desired=False,
        )

        changes = build_outbound_items(
            LocalSnapshot([], [], [], [], []),
            [{"_id": "tt0944947", "type": "series", "state": {"watched": watched}}],
            [intent],
            cinemeta_getter=lambda _imdb_id: {
                "videos": [{"id": video_ids[0], "season": 0, "episode": 1}]
            },
            initial=False,
        )

        self.assertEqual(len(changes), 1)
        self.assertEqual(decode_watched_bitfield(changes[0]["state"]["watched"], video_ids), set())


class StremioIdentityLookupTests(TestCase):
    def test_inbound_movie_prefers_the_duplicate_catalog_record_tracked_by_user(self):
        user = get_user_model().objects.create_user("user@example.com", password="pw")
        other_movie = Movie.objects.create(
            imdb_id="tt0137523",
            title="A Duplicate Fight Club",
            external_id="550",
            tmdb_id="550",
        )
        tracked_movie = Movie.objects.create(
            imdb_id="tt0137523",
            title="Z Tracked Fight Club",
            external_id="551",
            tmdb_id="551",
        )
        UserMovie.objects.create(user=user, movie=tracked_movie)
        remote = normalize_items(
            [{"_id": "tt0137523", "type": "movie", "state": {"timesWatched": 1}}],
            cinemeta_getter=lambda _imdb_id: {},
        )

        _apply_remote(
            user,
            remote,
            LocalSnapshot([], [], [], [], []),
            [],
            SyncReport(),
            initial=True,
            getter=lambda _imdb_id: {},
        )

        self.assertTrue(UserMovie.objects.get(user=user, movie=tracked_movie).is_seen)
        self.assertFalse(UserMovie.objects.filter(user=user, movie=other_movie).exists())

    def test_inbound_show_prefers_the_duplicate_catalog_record_tracked_by_user(self):
        user = get_user_model().objects.create_user("user@example.com", password="pw")
        other_show = Show.objects.create(
            imdb_id="tt0944947",
            name="A Duplicate Game of Thrones",
            external_id="1399",
            tmdb_id="1399",
        )
        tracked_show = Show.objects.create(
            imdb_id="tt0944947",
            name="Z Tracked Game of Thrones",
            external_id="1400",
            tmdb_id="1400",
        )
        UserShow.objects.create(user=user, show=tracked_show)
        video_id = "tt0944947:1:1"
        watched = encode_watched_bitfield({video_id}, [video_id])
        remote = normalize_items(
            [{"_id": "tt0944947", "type": "series", "state": {"watched": watched}}],
            cinemeta_getter=lambda _imdb_id: {
                "videos": [{"id": video_id, "season": 1, "episode": 1}]
            },
        )

        _apply_remote(
            user,
            remote,
            LocalSnapshot([], [], [], [], []),
            [],
            SyncReport(),
            initial=True,
            getter=lambda _imdb_id: {
                "videos": [{"id": video_id, "season": 1, "episode": 1}]
            },
        )

        self.assertTrue(UserEpisode.objects.filter(user=user, episode__show=tracked_show).exists())
        self.assertFalse(UserShow.objects.filter(user=user, show=other_show).exists())

    def test_tvdb_only_movie_is_reused_by_inbound_tvdb_identity(self):
        movie = Movie.objects.create(
            provider="tvdb",
            external_id="123",
            tvdb_id="123",
            title="TVDB Movie",
        )

        with patch("apps.stremio.sync.movie_services.import_movie") as import_movie:
            found, created = _ensure_movie("tvdb:123", "TVDB Movie")

        self.assertEqual(found.pk, movie.pk)
        self.assertFalse(created)
        import_movie.assert_not_called()

    def test_imdb_movie_reuses_cross_provider_identity_before_importing(self):
        movie = Movie.objects.create(
            provider="tvdb",
            external_id="123",
            tvdb_id="123",
            title="TVDB Movie",
        )
        with (
            patch("apps.stremio.sync.TMDBProvider") as tmdb_provider,
            patch("apps.stremio.sync.movie_services.import_movie") as import_movie,
        ):
            tmdb_provider.return_value.find_by_imdb_id.return_value = "550"
            tmdb_provider.return_value.fetch_detail.return_value = Mock(
                imdb_id="tt0137523",
                tmdb_id="550",
                tvdb_id="123",
            )
            found, created = _ensure_movie("tt0137523", "Fight Club")

        self.assertEqual(found.pk, movie.pk)
        self.assertFalse(created)
        import_movie.assert_not_called()

    def test_imdb_show_reuses_cross_provider_identity_before_importing(self):
        show = Show.objects.create(
            provider="tvdb",
            external_id="456",
            tvdb_id="456",
            tmdb_id="1399",
            name="TVDB Show",
        )
        with (
            patch("apps.stremio.sync.TMDBProvider") as tmdb_provider,
            patch("apps.stremio.sync.tv_services.import_show") as import_show,
        ):
            tmdb_provider.return_value.find_by_imdb_id.return_value = "1399"
            tmdb_provider.return_value.fetch_detail.return_value = Mock(
                imdb_id="tt0944947",
                tmdb_id="1399",
                tvdb_id="456",
            )
            found, created = _ensure_show("tt0944947", "Game of Thrones")

        self.assertEqual(found.pk, show.pk)
        self.assertFalse(created)
        import_show.assert_not_called()

    def test_tvdb_only_show_is_reused_by_inbound_tvdb_identity(self):
        show = Show.objects.create(
            provider="tvdb",
            external_id="456",
            tvdb_id="456",
            name="TVDB Show",
        )

        with patch("apps.stremio.sync.tv_services.import_show") as import_show:
            found, created = _ensure_show("tvdb:456", "TVDB Show")

        self.assertEqual(found.pk, show.pk)
        self.assertFalse(created)
        import_show.assert_not_called()


class StremioRemoteSafetyTests(TestCase):
    def test_library_item_does_not_readd_a_dropped_show_to_watchlist(self):
        user = get_user_model().objects.create_user("user@example.com", password="pw")
        show = Show.objects.create(
            imdb_id="tt0944947",
            name="Game of Thrones",
            external_id="1399",
            tmdb_id="1399",
        )
        UserShow.objects.create(
            user=user,
            show=show,
            status=UserShow.Status.DROPPED,
        )
        remote = normalize_items(
            [
                {
                    "_id": "tt0944947",
                    "type": "series",
                    "name": "Game of Thrones",
                    "state": {},
                }
            ],
            cinemeta_getter=lambda _imdb_id: {
                "videos": [{"id": "tt0944947:1:1", "season": 1, "episode": 1}]
            },
        )

        _apply_remote(
            user,
            remote,
            LocalSnapshot([], [], [], [], []),
            [],
            SyncReport(),
            initial=True,
            getter=lambda _imdb_id: {
                "videos": [{"id": "tt0944947:1:1", "season": 1, "episode": 1}]
            },
        )

        self.assertFalse(UserShow.objects.get(user=user, show=show).on_watchlist)

    def test_temporary_watched_movie_is_imported_without_watchlist_membership(self):
        user = get_user_model().objects.create_user("user@example.com", password="pw")
        movie = Movie.objects.create(
            imdb_id="tt0137523",
            title="Fight Club",
            external_id="550",
            tmdb_id="550",
        )
        remote = normalize_items(
            [
                {
                    "_id": "tt0137523",
                    "type": "movie",
                    "temp": True,
                    "state": {
                        "timesWatched": 1,
                        "lastWatched": "2026-08-01T10:00:00Z",
                    },
                }
            ],
            cinemeta_getter=lambda _imdb_id: {},
        )

        _apply_remote(
            user,
            remote,
            LocalSnapshot([], [], [], [], []),
            [],
            SyncReport(),
            initial=True,
            getter=lambda _imdb_id: {},
        )

        state = UserMovie.objects.get(user=user, movie=movie)
        self.assertTrue(state.is_seen)
        self.assertEqual(state.seen_at, datetime(2026, 8, 1, 10, tzinfo=timezone.utc))
        self.assertFalse(state.on_watchlist)

    def test_temporary_watched_series_is_imported_without_watchlist_membership(self):
        user = get_user_model().objects.create_user("user@example.com", password="pw")
        video_id = "tt0944947:1:2"
        watched = encode_watched_bitfield({video_id}, [video_id])
        imported_show = Show.objects.create(
            provider="tmdb",
            external_id="1399",
            tmdb_id="1399",
            name="Game of Thrones",
        )
        remote = normalize_items(
            [
                {
                    "_id": "tt0944947",
                    "type": "series",
                    "name": "Game of Thrones",
                    "temp": True,
                    "state": {"watched": watched},
                }
            ],
            cinemeta_getter=lambda _imdb_id: {
                "videos": [{"id": video_id, "season": 1, "episode": 2}]
            },
        )

        with patch("apps.stremio.sync._ensure_show", return_value=(imported_show, True)) as ensure_show:
            _apply_remote(
                user,
                remote,
                LocalSnapshot([], [], [], [], []),
                [],
                SyncReport(),
                initial=True,
                getter=lambda _imdb_id: {
                    "videos": [{"id": video_id, "season": 1, "episode": 2}]
                },
            )

        ensure_show.assert_called_once_with("tt0944947", "Game of Thrones", user=user)
        self.assertTrue(UserEpisode.objects.filter(user=user, episode__show=imported_show).exists())
        self.assertFalse(UserShow.objects.get(user=user, show=imported_show).on_watchlist)

    def test_temporary_unwatched_movie_is_not_projected(self):
        user = get_user_model().objects.create_user("user@example.com", password="pw")
        remote = normalize_items(
            [
                {
                    "_id": "tt0137523",
                    "type": "movie",
                    "temp": True,
                    "state": {"timesWatched": 0},
                }
            ],
            cinemeta_getter=lambda _imdb_id: {},
        )

        _apply_remote(
            user,
            remote,
            LocalSnapshot([], [], [], [], []),
            [],
            SyncReport(),
            initial=True,
            getter=lambda _imdb_id: {},
        )

        self.assertFalse(Movie.objects.exists())
        self.assertFalse(UserMovie.objects.exists())

    def test_temporary_unwatched_series_is_not_projected(self):
        user = get_user_model().objects.create_user("user@example.com", password="pw")
        remote = normalize_items(
            [
                {
                    "_id": "tt0944947",
                    "type": "series",
                    "name": "Game of Thrones",
                    "temp": True,
                    "state": {},
                }
            ],
            cinemeta_getter=lambda _imdb_id: {},
        )

        _apply_remote(
            user,
            remote,
            LocalSnapshot([], [], [], [], []),
            [],
            SyncReport(),
            initial=True,
            getter=lambda _imdb_id: {},
        )

        self.assertFalse(Show.objects.exists())
        self.assertFalse(UserShow.objects.exists())

    def test_watched_movie_is_removed_from_local_watchlist(self):
        user = get_user_model().objects.create_user("user@example.com", password="pw")
        movie = Movie.objects.create(
            imdb_id="tt0137523",
            title="Fight Club",
            external_id="550",
            tmdb_id="550",
        )
        added_at = datetime(2026, 8, 1, 10, tzinfo=timezone.utc)
        UserMovie.objects.create(
            user=user,
            movie=movie,
            on_watchlist=True,
            watchlist_added_at=added_at,
        )
        remote = normalize_items(
            [{"_id": "tt0137523", "type": "movie", "state": {"timesWatched": 1}}],
            cinemeta_getter=lambda _imdb_id: {},
        )

        _apply_remote(
            user,
            remote,
            LocalSnapshot([], [], [], [], []),
            [],
            SyncReport(),
            initial=False,
            getter=lambda _imdb_id: {},
        )

        state = UserMovie.objects.get(user=user, movie=movie)
        self.assertTrue(state.is_seen)
        self.assertFalse(state.on_watchlist)
        self.assertIsNone(state.watchlist_added_at)

    def test_movie_tombstone_preserves_local_watch_history(self):
        user = get_user_model().objects.create_user("user@example.com", password="pw")
        movie = Movie.objects.create(
            imdb_id="tt0137523",
            title="Fight Club",
            external_id="550",
            tmdb_id="550",
        )
        seen_at = datetime(2026, 8, 1, 10, tzinfo=timezone.utc)
        UserMovie.objects.create(user=user, movie=movie, is_seen=True, seen_at=seen_at)
        remote = normalize_items(
            [{"_id": "tt0137523", "type": "movie", "removed": True, "temp": True, "state": {}}],
            cinemeta_getter=lambda _imdb_id: {},
        )

        _apply_remote(
            user,
            remote,
            LocalSnapshot([], [], [], [], []),
            [],
            SyncReport(),
            initial=False,
            getter=lambda _imdb_id: {},
        )

        state = UserMovie.objects.get(user=user, movie=movie)
        self.assertTrue(state.is_seen)
        self.assertEqual(state.seen_at, seen_at)

    def test_series_tombstone_preserves_local_episode_history(self):
        user = get_user_model().objects.create_user("user@example.com", password="pw")
        show = Show.objects.create(
            imdb_id="tt0944947",
            name="Game of Thrones",
            external_id="1399",
            tmdb_id="1399",
        )
        season = Season.objects.create(show=show, season_number=1)
        episode = Episode.objects.create(
            show=show,
            season=season,
            season_number=1,
            episode_number=2,
            name="The Kingsroad",
        )
        UserEpisode.objects.create(user=user, episode=episode)
        video_id = "tt0944947:1:2"
        watched = encode_watched_bitfield(set(), [video_id])
        remote = normalize_items(
            [
                {
                    "_id": "tt0944947",
                    "type": "series",
                    "removed": True,
                    "temp": True,
                    "state": {"watched": watched},
                }
            ],
            cinemeta_getter=lambda _imdb_id: {
                "videos": [{"id": video_id, "season": 1, "episode": 2}]
            },
        )

        _apply_remote(
            user,
            remote,
            LocalSnapshot([], [], [], [], []),
            [],
            SyncReport(),
            initial=False,
            getter=lambda _imdb_id: {
                "videos": [{"id": video_id, "season": 1, "episode": 2}]
            },
        )

        self.assertTrue(UserEpisode.objects.filter(user=user, episode=episode).exists())
    def test_incomplete_series_state_does_not_delete_local_episode_history(self):
        user = get_user_model().objects.create_user("user@example.com", password="pw")
        show = Show.objects.create(
            imdb_id="tt0944947",
            name="Game of Thrones",
            external_id="1399",
            tmdb_id="1399",
        )
        season = Season.objects.create(show=show, season_number=1)
        episode = Episode.objects.create(
            show=show,
            season=season,
            season_number=1,
            episode_number=2,
            name="The Kingsroad",
        )
        UserEpisode.objects.create(user=user, episode=episode)
        remote = normalize_items(
            [{"_id": "tt0944947", "type": "series", "state": {}}],
            cinemeta_getter=lambda _imdb_id: {
                "videos": [{"id": "tt0944947:1:2", "season": 1, "episode": 2}]
            },
        )

        _apply_remote(
            user,
            remote,
            LocalSnapshot([], [], [], [], []),
            [],
            SyncReport(),
            initial=False,
            getter=lambda _imdb_id: {
                "videos": [{"id": "tt0944947:1:2", "season": 1, "episode": 2}]
            },
        )

        self.assertTrue(UserEpisode.objects.filter(user=user, episode=episode).exists())

    def test_invalid_bitfield_is_not_decoded_into_watched_episodes(self):
        video_ids = ["tt0944947:1:1", "tt0944947:1:2"]
        packed = base64.b64encode(zlib.compress(b"\x04")).decode("ascii")
        invalid = f"{video_ids[0]}:3:{packed}"

        snapshot = normalize_items(
            [{"_id": "tt0944947", "type": "series", "state": {"watched": invalid}}],
            cinemeta_getter=lambda _imdb_id: {
                "videos": [
                    {"id": video_ids[0], "season": 1, "episode": 1},
                    {"id": video_ids[1], "season": 1, "episode": 2},
                ]
            },
        )

        self.assertNotIn("tt0944947", snapshot.series_state_valid)
        self.assertEqual(snapshot.series_watched["tt0944947"], set())
        self.assertEqual(snapshot.watched_episodes, {})

    @patch("apps.stremio.sync._ensure_movie", side_effect=ValueError("should not import"))
    def test_movie_tombstone_does_not_create_catalog_or_user_state(self, ensure_movie):
        user = get_user_model().objects.create_user("user@example.com", password="pw")
        remote = normalize_items(
            [{"_id": "tmdb:999", "type": "movie", "removed": True, "temp": True, "state": {}}],
            cinemeta_getter=lambda _imdb_id: {},
        )
        report = SyncReport()

        _apply_remote(
            user,
            remote,
            LocalSnapshot([], [], [], [], []),
            [],
            report,
            initial=False,
            getter=lambda _imdb_id: {},
        )

        ensure_movie.assert_not_called()
        self.assertFalse(Movie.objects.exists())
        self.assertFalse(UserMovie.objects.exists())
        self.assertEqual(report.warnings, [])

    @patch("apps.stremio.sync._ensure_show", side_effect=ValueError("should not import"))
    def test_show_tombstone_does_not_create_catalog_or_user_state(self, ensure_show):
        user = get_user_model().objects.create_user("user@example.com", password="pw")
        remote = normalize_items(
            [{"_id": "tmdb:999", "type": "series", "removed": True, "temp": True, "state": {}}],
            cinemeta_getter=lambda _imdb_id: {},
        )
        report = SyncReport()

        _apply_remote(
            user,
            remote,
            LocalSnapshot([], [], [], [], []),
            [],
            report,
            initial=False,
            getter=lambda _imdb_id: {},
        )

        ensure_show.assert_not_called()
        self.assertFalse(Show.objects.exists())
        self.assertFalse(UserShow.objects.exists())
        self.assertEqual(report.warnings, [])

    def test_pending_episode_intent_matches_a_nonpreferred_identity_alias(self):
        user = get_user_model().objects.create_user("user@example.com", password="pw")
        show = Show.objects.create(
            tmdb_id="1399",
            name="Game of Thrones",
            external_id="1399",
        )
        video_ids = ["tt0944947:1:1"]
        watched = encode_watched_bitfield(set(video_ids), video_ids)
        remote = normalize_items(
            [{"_id": "tmdb:1399", "type": "series", "state": {"watched": watched}}],
            cinemeta_getter=lambda _imdb_id: {
                "videos": [{"id": video_ids[0], "season": 1, "episode": 1}]
            },
        )
        older_intent = StremioSyncIntent.objects.create(
            user=user,
            kind=StremioSyncIntent.Kind.EPISODE_HISTORY,
            identity_key="legacy:tt0944947:1:1",
            payload={
                "show": {"ids": {"imdb": "tt0944947", "tmdb": "1399"}},
                "seasons": [{"number": 1, "episodes": [{"number": 1}]}],
            },
            desired=True,
        )
        intent = StremioSyncIntent.objects.create(
            user=user,
            kind=StremioSyncIntent.Kind.EPISODE_HISTORY,
            identity_key="canonical:tt0944947:1:1",
            payload={
                "show": {"ids": {"imdb": "tt0944947", "tmdb": "1399"}},
                "seasons": [{"number": 1, "episodes": [{"number": 1}]}],
            },
            desired=False,
        )

        _apply_remote(
            user,
            remote,
            LocalSnapshot([], [], [], [], []),
            [older_intent, intent],
            SyncReport(),
            initial=True,
            getter=lambda _imdb_id: {
                "videos": [{"id": video_ids[0], "season": 1, "episode": 1}]
            },
        )

        self.assertFalse(UserEpisode.objects.filter(user=user).exists())

    def test_bitfield_with_anchor_after_declared_length_does_not_delete_history(self):
        user = get_user_model().objects.create_user("user@example.com", password="pw")
        show = Show.objects.create(
            imdb_id="tt0944947",
            name="Game of Thrones",
            external_id="1399",
            tmdb_id="1399",
        )
        season = Season.objects.create(show=show, season_number=1)
        episode = Episode.objects.create(
            show=show,
            season=season,
            season_number=1,
            episode_number=1,
            name="Winter Is Coming",
        )
        UserEpisode.objects.create(user=user, episode=episode)
        video_ids = ["tt0944947:1:1", "tt0944947:1:2"]
        valid = encode_watched_bitfield({video_ids[0]}, video_ids)
        malformed = valid.replace(f"{video_ids[0]}:1:", f"{video_ids[1]}:1:", 1)
        remote = normalize_items(
            [{"_id": "tt0944947", "type": "series", "state": {"watched": malformed}}],
            cinemeta_getter=lambda _imdb_id: {
                "videos": [
                    {"id": video_ids[0], "season": 1, "episode": 1},
                    {"id": video_ids[1], "season": 1, "episode": 2},
                ]
            },
        )

        _apply_remote(
            user,
            remote,
            LocalSnapshot([], [], [], [], []),
            [],
            SyncReport(),
            initial=False,
            getter=lambda _imdb_id: {
                "videos": [
                    {"id": video_ids[0], "season": 1, "episode": 1},
                    {"id": video_ids[1], "season": 1, "episode": 2},
                ]
            },
        )

        self.assertTrue(UserEpisode.objects.filter(user=user, episode=episode).exists())


class StremioMetadataRetryTests(TestCase):
    def test_provider_series_ids_use_local_imdb_alias_for_cinemeta(self):
        user = get_user_model().objects.create_user("user@example.com", password="pw")
        Show.objects.create(
            imdb_id="tt0944947",
            tmdb_id="1399",
            tvdb_id="121361",
            name="Game of Thrones",
            external_id="1399",
        )
        account = StremioAccount.objects.create(user=user, auth_key="auth-key")

        class FakeClient:
            def __init__(self):
                self.lookups = []

            def datastore_get(self, *, ids=None, all_items=False):
                return [
                    {"_id": "tmdb:1399", "type": "series", "name": "Game of Thrones", "state": {}},
                    {"_id": "tvdb:121361", "type": "series", "name": "Game of Thrones", "state": {}},
                ]

            def get_cinemeta_series(self, imdb_id):
                self.lookups.append(imdb_id)
                if imdb_id != "tt0944947":
                    raise AssertionError(f"Cinemeta requires IMDb IDs, got {imdb_id}")
                return {"videos": []}

            def datastore_put(self, changes):
                self.changes = changes

        client = FakeClient()
        report = sync_account(account.id, client_factory=lambda _account: client)

        account.refresh_from_db()
        self.assertEqual(set(client.lookups), {"tt0944947"})
        self.assertEqual(report.warnings, [])
        self.assertTrue(account.initial_sync_complete)

    def test_cinemeta_failure_keeps_library_cursor_pending(self):
        user = get_user_model().objects.create_user("user@example.com", password="pw")
        Show.objects.create(
            imdb_id="tt0944947",
            name="Game of Thrones",
            external_id="1399",
            tmdb_id="1399",
        )
        account = StremioAccount.objects.create(user=user, auth_key="auth-key")

        class FakeClient:
            def datastore_get(self, *, ids=None, all_items=False):
                return [{"_id": "tt0944947", "type": "series", "name": "Game of Thrones", "state": {}}]

            def get_cinemeta_series(self, _imdb_id):
                raise TimeoutError("Cinemeta unavailable")

            def datastore_put(self, changes):
                self.changes = changes

        report = sync_account(account.id, client_factory=lambda _account: FakeClient())

        account.refresh_from_db()
        self.assertTrue(report.warnings)
        self.assertFalse(account.initial_sync_complete)
        self.assertIsNone(account.library_synced_at)

    def test_episode_removal_intent_stays_queued_when_cinemeta_fails(self):
        user = get_user_model().objects.create_user("user@example.com", password="pw")
        intent = StremioSyncIntent.objects.create(
            user=user,
            kind=StremioSyncIntent.Kind.EPISODE_HISTORY,
            identity_key="tt0944947:1:1",
            payload={
                "show": {"ids": {"imdb": "tt0944947"}},
                "seasons": [{"number": 1, "episodes": [{"number": 1}]}],
            },
            desired=False,
        )

        _acknowledge_intents(
            [intent],
            [{"_id": "tt0944947", "type": "series", "state": {"watched": "unresolved"}}],
            lambda _imdb_id: (_ for _ in ()).throw(TimeoutError("Cinemeta unavailable")),
        )

        self.assertTrue(StremioSyncIntent.objects.filter(pk=intent.pk).exists())

    def test_watchlist_removal_intent_is_acknowledged_for_a_remote_tombstone(self):
        user = get_user_model().objects.create_user("user@example.com", password="pw")
        intent = StremioSyncIntent.objects.create(
            user=user,
            kind=StremioSyncIntent.Kind.SHOW_WATCHLIST,
            identity_key="tt0944947",
            payload={"ids": {"imdb": "tt0944947"}},
            desired=False,
        )

        _acknowledge_intents(
            [intent],
            [{"_id": "tt0944947", "type": "series", "removed": True, "temp": True}],
            lambda _imdb_id: {},
        )

        self.assertFalse(StremioSyncIntent.objects.filter(pk=intent.pk).exists())

    def test_local_episode_projection_keeps_cursor_pending_when_cinemeta_fails(self):
        user = get_user_model().objects.create_user("user@example.com", password="pw")
        show = Show.objects.create(
            imdb_id="tt0944947",
            name="Game of Thrones",
            external_id="1399",
            tmdb_id="1399",
        )
        season = Season.objects.create(show=show, season_number=1)
        episode = Episode.objects.create(
            show=show,
            season=season,
            season_number=1,
            episode_number=1,
            name="Winter Is Coming",
        )
        UserEpisode.objects.create(user=user, episode=episode)
        account = StremioAccount.objects.create(user=user, auth_key="auth-key")

        class FakeClient:
            def __init__(self):
                self.puts = []

            def datastore_get(self, *, ids=None, all_items=False):
                return []

            def get_cinemeta_series(self, _imdb_id):
                raise TimeoutError("Cinemeta unavailable")

            def datastore_put(self, changes):
                self.puts.append(changes)

        client = FakeClient()
        report = sync_account(account.id, client_factory=lambda _account: client)

        account.refresh_from_db()
        self.assertTrue(report.warnings)
        self.assertFalse(account.initial_sync_complete)
        self.assertIsNone(account.library_synced_at)
        self.assertEqual(client.puts, [])

    def test_watched_series_with_empty_cinemeta_videos_keeps_cursor_pending(self):
        user = get_user_model().objects.create_user("user@example.com", password="pw")
        Show.objects.create(
            imdb_id="tt0944947",
            name="Game of Thrones",
            external_id="1399",
            tmdb_id="1399",
        )
        account = StremioAccount.objects.create(user=user, auth_key="auth-key")

        class FakeClient:
            def datastore_get(self, *, ids=None, all_items=False):
                return [
                    {
                        "_id": "tt0944947",
                        "type": "series",
                        "name": "Game of Thrones",
                        "state": {"watched": "opaque-watched-state"},
                    }
                ]

            def get_cinemeta_series(self, _imdb_id):
                return {"meta": "without videos"}

            def datastore_put(self, changes):
                self.changes = changes

        report = sync_account(account.id, client_factory=lambda _account: FakeClient())

        account.refresh_from_db()
        self.assertTrue(report.warnings)
        self.assertFalse(account.initial_sync_complete)
        self.assertIsNone(account.library_synced_at)

    def test_acknowledgement_does_not_delete_a_newer_intent(self):
        user = get_user_model().objects.create_user("user@example.com", password="pw")
        intent = StremioSyncIntent.objects.create(
            user=user,
            kind=StremioSyncIntent.Kind.MOVIE_HISTORY,
            identity_key="tt0137523",
            payload={"ids": {"imdb": "tt0137523"}},
            desired=False,
        )
        original_updated_at = intent.updated_at
        newer_updated_at = original_updated_at + timedelta(seconds=1)
        StremioSyncIntent.objects.filter(pk=intent.pk).update(
            desired=True,
            updated_at=newer_updated_at,
        )

        _acknowledge_intents(
            [intent],
            [{"_id": "tt0137523", "type": "movie", "state": {"timesWatched": 0}}],
            lambda _imdb_id: {},
        )

        current = StremioSyncIntent.objects.get(pk=intent.pk)
        self.assertTrue(current.desired)
        self.assertEqual(current.updated_at, newer_updated_at)

    def test_acknowledgement_discards_older_conflicting_alias_intents(self):
        user = get_user_model().objects.create_user("user@example.com", password="pw")
        older_intent = StremioSyncIntent.objects.create(
            user=user,
            kind=StremioSyncIntent.Kind.MOVIE_WATCHLIST,
            identity_key="legacy:tt0137523",
            payload={"ids": {"imdb": "tt0137523", "tmdb": "550"}},
            desired=False,
        )
        newer_intent = StremioSyncIntent.objects.create(
            user=user,
            kind=StremioSyncIntent.Kind.MOVIE_WATCHLIST,
            identity_key="canonical:tmdb:550",
            payload={"ids": {"imdb": "tt0137523", "tmdb": "550"}},
            desired=True,
        )

        _acknowledge_intents(
            [older_intent, newer_intent],
            [{"_id": "tmdb:550", "type": "movie", "state": {}}],
            lambda _imdb_id: {},
        )

        self.assertFalse(StremioSyncIntent.objects.filter(user=user).exists())

    def test_movie_intent_acknowledgement_ignores_series_with_same_identity(self):
        user = get_user_model().objects.create_user("user@example.com", password="pw")
        intent = StremioSyncIntent.objects.create(
            user=user,
            kind=StremioSyncIntent.Kind.MOVIE_WATCHLIST,
            identity_key="tmdb:550",
            payload={"ids": {"tmdb": "550"}},
            desired=True,
        )

        _acknowledge_intents(
            [intent],
            [{"_id": "tmdb:550", "type": "series", "state": {}}],
            lambda _imdb_id: {},
        )

        self.assertTrue(StremioSyncIntent.objects.filter(pk=intent.pk).exists())

    def test_pending_movie_intent_uses_the_newest_alias_match(self):
        user = get_user_model().objects.create_user("user@example.com", password="pw")
        movie = Movie.objects.create(
            imdb_id="tt0137523",
            title="Fight Club",
            external_id="550",
            tmdb_id="550",
        )
        older_intent = StremioSyncIntent.objects.create(
            user=user,
            kind=StremioSyncIntent.Kind.MOVIE_WATCHLIST,
            identity_key="legacy:tt0137523",
            payload={"ids": {"imdb": "tt0137523", "tmdb": "550"}},
            desired=False,
        )
        newer_intent = StremioSyncIntent.objects.create(
            user=user,
            kind=StremioSyncIntent.Kind.MOVIE_WATCHLIST,
            identity_key="canonical:tmdb:550",
            payload={"ids": {"imdb": "tt0137523", "tmdb": "550"}},
            desired=True,
        )

        remote = normalize_items(
            [{"_id": "tmdb:550", "type": "movie", "state": {}}],
            cinemeta_getter=lambda _imdb_id: {},
        )
        _apply_remote(
            user,
            remote,
            LocalSnapshot([], [], [], [], []),
            [older_intent, newer_intent],
            SyncReport(),
            initial=True,
            getter=lambda _imdb_id: {},
        )

        self.assertTrue(UserMovie.objects.get(user=user, movie=movie).on_watchlist)


class StremioProjectionRetryTests(TestCase):
    @patch("apps.stremio.sync._ensure_movie", side_effect=ValueError("not imported"))
    def test_failed_remote_projection_keeps_initial_cursor_pending(self, _ensure_movie):
        user = get_user_model().objects.create_user("user@example.com", password="pw")
        account = StremioAccount.objects.create(user=user, auth_key="auth-key")

        class FakeClient:
            def datastore_get(self, *, ids=None, all_items=False):
                return [{"_id": "tt0137523", "type": "movie", "name": "Fight Club", "state": {}}]

            def get_cinemeta_series(self, _imdb_id):
                return {}

            def datastore_put(self, changes):
                raise AssertionError("failed projections should not push")

        report = sync_account(account.id, client_factory=lambda _account: FakeClient())

        account.refresh_from_db()
        self.assertTrue(report.warnings)
        self.assertFalse(account.initial_sync_complete)
        self.assertIsNone(account.library_synced_at)


class StremioIncrementalSyncTests(TestCase):
    def test_incremental_sync_does_not_fetch_the_full_library(self):
        user = get_user_model().objects.create_user("user@example.com", password="pw")
        movie = Movie.objects.create(
            imdb_id="tt0137523",
            title="Fight Club",
            external_id="550",
            tmdb_id="550",
        )
        UserMovie.objects.create(user=user, movie=movie, is_seen=True)
        account = StremioAccount.objects.create(
            user=user,
            auth_key="auth-key",
            initial_sync_complete=True,
            library_synced_at=datetime(2026, 8, 8, 10, tzinfo=timezone.utc),
        )

        class FakeClient:
            def __init__(self):
                self.get_calls = []

            def datastore_meta(self):
                return [["tt0137523", "2026-08-08T10:01:00Z"]]

            def datastore_get(self, *, ids=None, all_items=False):
                if all_items:
                    raise AssertionError("incremental sync must not fetch all library items")
                self.get_calls.append(ids)
                return [
                    {
                        "_id": "tt0137523",
                        "type": "movie",
                        "name": "Fight Club",
                        "state": {"timesWatched": 1},
                    }
                ]

            def get_cinemeta_series(self, _imdb_id):
                return {}

            def datastore_put(self, changes):
                raise AssertionError("unchanged incremental state should not be pushed")

        client = FakeClient()
        report = sync_account(account.id, client_factory=lambda _account: client)

        account.refresh_from_db()
        self.assertEqual(client.get_calls, [["tt0137523"]])
        self.assertFalse(report.warnings)
        self.assertTrue(account.initial_sync_complete)


class StremioIncrementalSyncWithEpisodeHistoryTests(TestCase):
    def test_incremental_sync_survives_episode_history_and_pulls_watched_movie(self):
        user = get_user_model().objects.create_user("user@example.com", password="pw")
        show = Show.objects.create(
            imdb_id="tt0903747",
            name="Breaking Bad",
            external_id="1396",
            tmdb_id="1396",
        )
        season = Season.objects.create(show=show, season_number=1)
        episode = Episode.objects.create(
            show=show,
            season=season,
            season_number=1,
            episode_number=1,
            name="Pilot",
        )
        UserEpisode.objects.create(user=user, episode=episode)
        movie = Movie.objects.create(
            imdb_id="tt0137523",
            title="Fight Club",
            external_id="550",
            tmdb_id="550",
        )
        account = StremioAccount.objects.create(
            user=user,
            auth_key="auth-key",
            initial_sync_complete=True,
            library_synced_at=datetime(2026, 8, 8, 10, tzinfo=timezone.utc),
        )
        show_video_id = "tt0959621:1:1"

        class FakeClient:
            def __init__(self):
                self.get_calls = []
                self.puts = []

            def datastore_meta(self):
                return [["tt0137523", "2026-08-08T12:00:00Z"]]

            def datastore_get(self, *, ids=None, all_items=False):
                self.get_calls.append(list(ids or []))
                items = [
                    {
                        "_id": "tt0137523",
                        "type": "movie",
                        "name": "Fight Club",
                        "temp": True,
                        "state": {"flaggedWatched": 1},
                    }
                ]
                if ids and "tt0903747" in ids:
                    items.append(
                        {
                            "_id": "tt0903747",
                            "type": "series",
                            "name": "Breaking Bad",
                            "temp": True,
                            "state": {
                                "watched": encode_watched_bitfield(
                                    {show_video_id}, [show_video_id]
                                ),
                            },
                        }
                    )
                return items

            def get_cinemeta_series(self, _imdb_id):
                return {
                    "videos": [
                        {"id": show_video_id, "season": 1, "episode": 1},
                    ]
                }

            def datastore_put(self, changes):
                self.puts.append(changes)

        client = FakeClient()
        report = sync_account(account.id, client_factory=lambda _account: client)

        account.refresh_from_db()
        self.assertTrue(UserMovie.objects.get(user=user, movie=movie).is_seen)
        self.assertIn(
            True,
            ["tt0903747" in call for call in client.get_calls],
        )
        self.assertEqual(report.warnings, [])
        self.assertTrue(account.library_synced_at)
        self.assertEqual(account.sync_status, StremioAccount.SyncStatus.OK)


class StremioPullTests(TestCase):
    def test_initial_pull_marks_existing_movie_watched_without_pushing_back(self):
        user = get_user_model().objects.create_user("user@example.com", password="pw")
        movie = Movie.objects.create(
            imdb_id="tt0137523",
            title="Fight Club",
            external_id="550",
            tmdb_id="550",
        )
        account = StremioAccount.objects.create(user=user, auth_key="auth-key")

        class FakeClient:
            def __init__(self):
                self.puts = []

            def datastore_get(self, *, ids=None, all_items=False):
                return [
                    {
                        "_id": "tt0137523",
                        "type": "movie",
                        "name": "Fight Club",
                        "state": {
                            "timesWatched": 1,
                            "lastWatched": "2026-08-01T10:00:00Z",
                        },
                    }
                ]

            def datastore_meta(self):
                return []

            def get_cinemeta_series(self, _imdb_id):
                return {}

            def datastore_put(self, changes):
                self.puts.append(changes)

        client = FakeClient()
        report = sync_account(account.id, client_factory=lambda _account: client)

        state = UserMovie.objects.get(user=user, movie=movie)
        self.assertTrue(state.is_seen)
        self.assertEqual(state.seen_at, datetime(2026, 8, 1, 10, tzinfo=timezone.utc))
        self.assertEqual(client.puts, [])
        self.assertTrue(report.initial_sync_complete)

    def test_initial_series_pull_does_not_write_synthetic_last_watched(self):
        user = get_user_model().objects.create_user("user@example.com", password="pw")
        show = Show.objects.create(
            imdb_id="tt0944947",
            name="Game of Thrones",
            external_id="1399",
            tmdb_id="1399",
        )
        season = Season.objects.create(show=show, season_number=1)
        episode = Episode.objects.create(
            show=show,
            season=season,
            season_number=1,
            episode_number=2,
            name="The Kingsroad",
        )
        video_id = "tt0944947:1:2"
        watched = encode_watched_bitfield({video_id}, [video_id])
        account = StremioAccount.objects.create(user=user, auth_key="auth-key")

        class FakeClient:
            def __init__(self):
                self.puts = []

            def datastore_get(self, *, ids=None, all_items=False):
                self.all_items = all_items
                return [
                    {
                        "_id": "tt0944947",
                        "type": "series",
                        "name": "Game of Thrones",
                        "state": {"watched": watched},
                    }
                ]

            def get_cinemeta_series(self, _imdb_id):
                return {"videos": [{"id": video_id, "season": 1, "episode": 2}]}

            def datastore_put(self, changes):
                self.puts.append(changes)

        client = FakeClient()
        report = sync_account(account.id, client_factory=lambda _account: client)

        self.assertTrue(UserEpisode.objects.filter(user=user, episode=episode).exists())
        self.assertTrue(client.puts)
        self.assertTrue(
            all("lastWatched" not in change["state"] for batch in client.puts for change in batch)
        )
        self.assertTrue(report.initial_sync_complete)
