from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from apps.movies.models import Movie, UserMovie
from apps.sync.identities import episode_payload, movie_payload, show_payload
from apps.sync.library import (
    WatchedEpisode,
    _collect_local_snapshot,
    _ensure_episodes_batch,
    _find_by_ids,
)
from apps.tv.models import Episode, Season, Show, UserEpisode, UserShow


class LibraryMatchingTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            "user@example.com",
            password="password",
        )

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

    def test_reused_episode_trakt_id_within_a_show_is_moved_to_the_new_position(self):
        show = Show.objects.create(
            provider="tvdb",
            external_id="7000",
            trakt_id="1000",
            name="The Series",
        )
        season = Season.objects.create(show=show, season_number=1, name="Season 1")
        Episode.objects.create(
            show=show,
            season=season,
            season_number=1,
            episode_number=1,
            trakt_id="55",
            name="First",
        )
        Episode.objects.create(
            show=show,
            season=season,
            season_number=1,
            episode_number=2,
            name="Second",
        )
        watched = WatchedEpisode(
            show={"ids": {"trakt": 1000}},
            episode={"ids": {"trakt": 55}, "title": "Second"},
            season_number=1,
            episode_number=2,
            watched_at=timezone.now(),
        )

        episodes = _ensure_episodes_batch([(watched, show)])

        moved = episodes[(show.id, 1, 2)]
        self.assertEqual(moved.trakt_id, "55")
        self.assertIsNone(Episode.objects.get(season_number=1, episode_number=1).trakt_id)


class LocalSnapshotTests(TestCase):
    def test_snapshot_serves_sync_fields_without_loading_catalog_blobs(self):
        user = get_user_model().objects.create_user("user@example.com", password="pw")
        heavy = {
            "translations": {"pt-BR": {"title": "Traduzido"}},
            "cast": [{"name": "Someone"}],
            "overview": "A long synopsis",
        }
        movie = Movie.objects.create(
            imdb_id="tt0137523",
            title="Fight Club",
            external_id="550",
            tmdb_id="550",
            poster_path="/poster.jpg",
            **heavy,
        )
        UserMovie.objects.create(user=user, movie=movie, is_seen=True, on_watchlist=True)
        show = Show.objects.create(
            imdb_id="tt0903747",
            name="Breaking Bad",
            external_id="81189",
            tvdb_id="81189",
            **heavy,
        )
        UserShow.objects.create(user=user, show=show, on_watchlist=True)
        season = Season.objects.create(show=show, season_number=1)
        episode = Episode.objects.create(
            show=show,
            season=season,
            season_number=1,
            episode_number=1,
            translations={"pt-BR": {"name": "Piloto"}},
            overview="A long synopsis",
        )
        UserEpisode.objects.create(user=user, episode=episode)

        snapshot = _collect_local_snapshot(user)

        watched = snapshot.episode_history[0]
        for obj in (
            snapshot.movie_history[0].movie,
            snapshot.show_watchlist[0].show,
            watched.episode.show,
        ):
            self.assertTrue({"translations", "cast", "overview"} <= obj.get_deferred_fields())
        self.assertTrue({"translations", "overview"} <= watched.episode.get_deferred_fields())
        with self.assertNumQueries(0):
            movie_payload(snapshot.movie_history[0].movie, watched_at=timezone.now())
            show_payload(snapshot.show_watchlist[0].show)
            episode_payload(watched.episode, watched_at=watched.seen_at)
            snapshot.movie_history[0].movie.poster_url
            watched.episode.show.poster_url
