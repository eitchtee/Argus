from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.test import TestCase
from django.utils import timezone

from apps.catalog.models import MediaRating
from apps.catalog.providers.exceptions import ProviderError
from apps.imports.trakt import (
    TraktSnapshot,
    _ensure_movie,
    _ensure_show,
    apply_remote_snapshot,
    normalize_snapshot,
)
from apps.movies.models import Movie, UserMovie
from apps.tv.models import Episode, Season, Show, UserEpisode, UserShow


def _rating_filter(media):
    return {
        "content_type": ContentType.objects.get_for_model(type(media)),
        "object_id": media.pk,
    }


class TraktSnapshotImportTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            "user@example.com",
            password="password",
        )

    def test_ratings_are_applied_onto_local_media(self):
        watched_at = timezone.now()
        movie = Movie.objects.create(
            external_id="550",
            trakt_id="5500",
            tmdb_id="550",
            title="Fight Club",
        )
        show = Show.objects.create(
            external_id="1000",
            trakt_id="1000",
            name="Lucifer",
        )
        season = Season.objects.create(show=show, season_number=1)
        episode = Episode.objects.create(
            show=show,
            season=season,
            season_number=1,
            episode_number=2,
            name="Episode",
        )
        UserEpisode.objects.create(user=self.user, episode=episode, seen_at=watched_at)

        snapshot = TraktSnapshot(
            watchlist_movies=[],
            watchlist_shows=[],
            watched_movies=[
                {
                    "watched_at": watched_at.isoformat(),
                    "movie": {"ids": {"trakt": 5500, "tmdb": 550}},
                }
            ],
            watched_shows=[{"show": {"ids": {"trakt": 1000}}}],
            dropped_shows=[],
            rated_movies=[
                {"rating": 7, "movie": {"ids": {"trakt": 5500, "tmdb": 550}}}
            ],
            rated_shows=[{"rating": 10, "show": {"ids": {"trakt": 1000}}}],
            rated_episodes=[
                {
                    "rating": 3,
                    "show": {"ids": {"trakt": 1000}},
                    "episode": {"season": 1, "number": 2},
                }
            ],
        )

        report = apply_remote_snapshot(self.user, snapshot)

        self.assertEqual(report.ratings_applied, 3)
        self.assertEqual(
            str(MediaRating.objects.get(**_rating_filter(movie)).score),
            "3.5",
        )
        self.assertEqual(
            str(MediaRating.objects.get(**_rating_filter(show)).score),
            "5.0",
        )
        self.assertEqual(
            str(MediaRating.objects.get(**_rating_filter(episode)).score),
            "1.5",
        )

    def test_ratings_overwrite_a_stale_local_score(self):
        watched_at = timezone.now()
        movie = Movie.objects.create(
            external_id="550",
            trakt_id="5500",
            title="Fight Club",
        )
        UserMovie.objects.create(
            user=self.user,
            movie=movie,
            is_seen=True,
            seen_at=watched_at,
        )
        MediaRating.objects.create(
            user=self.user,
            media_type=MediaRating.MediaType.MOVIE,
            score="1.0",
            **_rating_filter(movie),
        )

        snapshot = TraktSnapshot(
            watchlist_movies=[],
            watchlist_shows=[],
            watched_movies=[],
            watched_shows=[],
            dropped_shows=[],
            rated_movies=[{"rating": 9, "movie": {"ids": {"trakt": 5500}}}],
        )

        report = apply_remote_snapshot(self.user, snapshot)

        self.assertEqual(report.ratings_applied, 1)
        self.assertEqual(
            str(MediaRating.objects.get(**_rating_filter(movie)).score),
            "4.5",
        )

    def test_rating_for_media_in_neither_library_is_ignored(self):
        """Rated on Trakt, but not watched or watchlisted on either side."""
        snapshot = TraktSnapshot(
            watchlist_movies=[],
            watchlist_shows=[],
            watched_movies=[],
            watched_shows=[],
            dropped_shows=[],
            rated_movies=[
                {"rating": 8, "movie": {"ids": {"trakt": 4242, "tmdb": 4242}}}
            ],
        )

        with patch("apps.imports.trakt.movie_services.import_movie") as import_movie:
            report = apply_remote_snapshot(self.user, snapshot)

        import_movie.assert_not_called()
        self.assertEqual(report.ratings_applied, 0)
        self.assertFalse(Movie.objects.exists())
        self.assertFalse(MediaRating.objects.exists())

    def test_rated_episode_of_a_show_in_neither_library_is_ignored(self):
        """The Lucifer case: rated on Trakt, but the show is not in either library."""
        snapshot = TraktSnapshot(
            watchlist_movies=[],
            watchlist_shows=[],
            watched_movies=[],
            watched_shows=[],
            dropped_shows=[],
            rated_episodes=[
                {
                    "rating": 8,
                    "show": {"ids": {"trakt": 98990, "tvdb": 295685}},
                    "episode": {"season": 2, "number": 11, "title": "Stewardess"},
                }
            ],
        )

        with patch("apps.imports.trakt.tv_services.import_show") as import_show:
            report = apply_remote_snapshot(self.user, snapshot)

        import_show.assert_not_called()
        self.assertEqual(report.ratings_applied, 0)
        self.assertFalse(Show.objects.exists())
        self.assertFalse(Episode.objects.exists())
        self.assertFalse(MediaRating.objects.exists())

    def test_rating_applies_onto_media_tracked_only_in_argus(self):
        """Not in the Trakt snapshot's libraries, but already tracked locally."""
        movie = Movie.objects.create(
            external_id="550",
            trakt_id="5500",
            title="Fight Club",
        )
        UserMovie.objects.create(
            user=self.user,
            movie=movie,
            on_watchlist=True,
            watchlist_added_at=timezone.now(),
        )
        snapshot = TraktSnapshot(
            watchlist_movies=[],
            watchlist_shows=[],
            watched_movies=[],
            watched_shows=[],
            dropped_shows=[],
            rated_movies=[{"rating": 9, "movie": {"ids": {"trakt": 5500}}}],
        )

        report = apply_remote_snapshot(self.user, snapshot)

        self.assertEqual(report.ratings_applied, 1)
        self.assertEqual(
            str(MediaRating.objects.get(**_rating_filter(movie)).score),
            "4.5",
        )

    def test_rating_applies_without_changing_an_existing_show_status(self):
        show = Show.objects.create(
            external_id="1000",
            trakt_id="1000",
            name="Lucifer",
        )
        UserShow.objects.create(
            user=self.user,
            show=show,
            status=UserShow.Status.DROPPED,
        )
        snapshot = TraktSnapshot(
            watchlist_movies=[],
            watchlist_shows=[],
            watched_movies=[],
            watched_shows=[],
            dropped_shows=[],
            rated_shows=[{"rating": 10, "show": {"ids": {"trakt": 1000}}}],
        )

        apply_remote_snapshot(self.user, snapshot)

        state = UserShow.objects.get(user=self.user, show=show)
        self.assertEqual(state.status, UserShow.Status.DROPPED)
        self.assertEqual(
            str(MediaRating.objects.get(**_rating_filter(show)).score),
            "5.0",
        )

    def test_duplicate_movie_watches_keep_latest_timestamp(self):
        first = timezone.now() - timedelta(days=1)
        second = timezone.now()
        movie = Movie.objects.create(
            external_id="550",
            trakt_id="5500",
            tmdb_id="550",
            title="Fight Club",
        )
        snapshot = TraktSnapshot(
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

        apply_remote_snapshot(self.user, snapshot)

        state = UserMovie.objects.get(user=self.user, movie=movie)
        self.assertTrue(state.is_seen)
        self.assertAlmostEqual(state.seen_at.timestamp(), second.timestamp(), places=3)

    def test_duplicate_episode_history_keeps_latest_watch(self):
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

    def test_import_uses_provider_default_language(self):
        self.user.settings.tmdb_metadata_language = "pt-BR"
        self.user.settings.save(update_fields=["tmdb_metadata_language"])
        movie = Movie.objects.create(external_id="999", title="Fight Club")

        with patch("apps.imports.trakt.movie_services.import_movie", return_value=movie) as import_movie:
            _ensure_movie(
                self.user,
                {"ids": {"tmdb": 550, "trakt": 5500}},
            )

        import_movie.assert_called_once_with("tmdb", "550", language="en-US")

    def test_show_import_prefers_tvdb_and_falls_back_to_tmdb(self):
        fallback_show = Show.objects.create(
            provider="tmdb",
            external_id="9999",
            name="The Series",
        )

        with patch(
            "apps.imports.trakt.tv_services.import_show",
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
        snapshot = TraktSnapshot(
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

        apply_remote_snapshot(self.user, snapshot)

        self.assertAlmostEqual(
            UserEpisode.objects.get(user=self.user, episode=episode).seen_at.timestamp(),
            remote_seen_at.timestamp(),
            places=3,
        )

    def test_duplicate_episode_records_keep_latest_timestamp(self):
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
        snapshot = TraktSnapshot([], [], [], [watched_show(first), watched_show(second)], [])

        apply_remote_snapshot(self.user, snapshot)

        self.assertAlmostEqual(
            UserEpisode.objects.get(user=self.user, episode=episode).seen_at.timestamp(),
            second.timestamp(),
            places=3,
        )

    def test_watched_special_episode_in_season_zero_is_imported(self):
        show = Show.objects.create(external_id="100", trakt_id="1000", name="The Show")
        snapshot = TraktSnapshot(
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

        apply_remote_snapshot(self.user, snapshot)

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
        snapshot = TraktSnapshot(
            [],
            [{"show": {"ids": {"trakt": 1000}, "title": "The Show"}}],
            [],
            [],
            [],
        )

        apply_remote_snapshot(self.user, snapshot)

        user_show = UserShow.objects.get(user=self.user, show=show)
        self.assertEqual(user_show.status, UserShow.Status.TRACKED)
        self.assertTrue(user_show.on_watchlist)
        self.assertFalse(UserEpisode.objects.filter(user=self.user).exists())

    def test_watchlist_preserves_local_paused_status(self):
        show = Show.objects.create(external_id="100", trakt_id="1000", name="The Show")
        UserShow.objects.create(
            user=self.user,
            show=show,
            status=UserShow.Status.PAUSED,
        )
        snapshot = TraktSnapshot(
            [],
            [{"show": {"ids": {"trakt": 1000}, "title": "The Show"}}],
            [],
            [],
            [],
        )

        apply_remote_snapshot(self.user, snapshot)

        user_show = UserShow.objects.get(user=self.user, show=show)
        self.assertEqual(user_show.status, UserShow.Status.PAUSED)
        self.assertTrue(user_show.on_watchlist)

    def test_watched_episode_preserves_local_paused_status(self):
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
        snapshot = TraktSnapshot(
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

        apply_remote_snapshot(self.user, snapshot)

        user_show = UserShow.objects.get(user=self.user, show=show)
        self.assertEqual(user_show.status, UserShow.Status.PAUSED)
        self.assertTrue(UserEpisode.objects.filter(user=self.user, episode=episode).exists())

    def test_dropped_show_preserves_local_paused_status(self):
        show = Show.objects.create(external_id="100", trakt_id="1000", name="The Show")
        UserShow.objects.create(
            user=self.user,
            show=show,
            status=UserShow.Status.PAUSED,
        )
        snapshot = TraktSnapshot(
            [],
            [],
            [],
            [],
            [{"show": {"ids": {"trakt": 1000}, "title": "The Show"}}],
        )

        apply_remote_snapshot(self.user, snapshot)

        self.assertEqual(
            UserShow.objects.get(user=self.user, show=show).status,
            UserShow.Status.PAUSED,
        )

    def test_dropped_show_keeps_history_and_dropped_status(self):
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
        snapshot = TraktSnapshot(
            [],
            [],
            [],
            [],
            [{"show": {"ids": {"trakt": 1000}, "title": "The Show"}}],
        )

        apply_remote_snapshot(self.user, snapshot)

        self.assertEqual(
            UserShow.objects.get(user=self.user, show=show).status,
            UserShow.Status.DROPPED,
        )
        self.assertTrue(UserEpisode.objects.filter(user=self.user, episode=episode).exists())

    def test_import_preserves_preexisting_local_watchlist(self):
        movie = Movie.objects.create(
            external_id="550",
            trakt_id="5500",
            title="Fight Club",
        )
        UserMovie.objects.create(user=self.user, movie=movie, on_watchlist=True)
        snapshot = TraktSnapshot([], [], [], [], [])

        apply_remote_snapshot(self.user, snapshot)

        self.assertTrue(UserMovie.objects.get(user=self.user, movie=movie).on_watchlist)

    def test_watchlist_import_keeps_the_original_watchlist_timestamp(self):
        movie = Movie.objects.create(
            external_id="550",
            trakt_id="5500",
            tmdb_id="550",
            title="Fight Club",
        )
        added_at = timezone.now() - timedelta(days=30)
        UserMovie.objects.create(
            user=self.user,
            movie=movie,
            on_watchlist=True,
            watchlist_added_at=added_at,
        )
        snapshot = TraktSnapshot(
            watchlist_movies=[{"movie": {"ids": {"trakt": 5500, "tmdb": 550}}}],
            watchlist_shows=[],
            watched_movies=[],
            watched_shows=[],
            dropped_shows=[],
        )

        apply_remote_snapshot(self.user, snapshot)

        state = UserMovie.objects.get(user=self.user, movie=movie)
        self.assertTrue(state.on_watchlist)
        self.assertAlmostEqual(
            state.watchlist_added_at.timestamp(),
            added_at.timestamp(),
            places=3,
        )

    def test_a_failing_show_import_is_only_attempted_once_per_import(self):
        snapshot = TraktSnapshot(
            watchlist_movies=[],
            watchlist_shows=[{"show": {"title": "Nope", "ids": {"trakt": 4242}}}],
            watched_movies=[],
            watched_shows=[{"show": {"title": "Nope", "ids": {"trakt": 4242}}}],
            dropped_shows=[],
            watched_episodes=[
                {
                    "watched_at": timezone.now().isoformat(),
                    "show": {"title": "Nope", "ids": {"trakt": 4242}},
                    "episode": {"season": 1, "number": 1, "ids": {"trakt": 91}},
                }
            ],
        )
        
        with patch(
            "apps.imports.trakt.tv_services.import_show",
            side_effect=ProviderError("missing"),
        ) as import_show:
            report = apply_remote_snapshot(self.user, snapshot)

        self.assertEqual(import_show.call_count, 0)
        self.assertEqual(len(report.warnings), 1)
        self.assertFalse(UserShow.objects.filter(user=self.user).exists())
