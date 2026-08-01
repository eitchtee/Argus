import json
from io import BytesIO
from unittest.mock import patch
from zipfile import ZIP_DEFLATED, ZipFile

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase

from apps.imports.services import (
    TraktExportError,
    import_trakt_export,
    load_trakt_export,
)
from apps.movies.models import Movie, UserMovie
from apps.tv.models import Episode, Show, UserEpisode, UserShow


def archive_with(**members):
    stream = BytesIO()
    with ZipFile(stream, "w", ZIP_DEFLATED) as archive:
        for name, payload in members.items():
            archive.writestr(name, json.dumps(payload))
    stream.seek(0)
    return stream


class TraktExportParserTests(SimpleTestCase):
    def test_load_trakt_export_merges_split_files_and_watchlist_entries(self):
        stream = archive_with(
            **{
                "watched-movies-2.json": [{"movie": {"ids": {"trakt": 2}}}],
                "watched-movies-1.json": [{"movie": {"ids": {"trakt": 1}}}],
                "watched-history-1.json": [{"type": "episode", "id": 10}],
                "lists-watchlist.json": [
                    {"type": "movie", "movie": {"ids": {"trakt": 3}}},
                    {"type": "show", "show": {"ids": {"trakt": 4}}},
                ],
                "ratings-movies-1.json": [{"movie": {"ids": {"trakt": 99}}}],
            }
        )

        snapshot = load_trakt_export(stream)

        self.assertEqual(
            [item["movie"]["ids"]["trakt"] for item in snapshot.watched_movies],
            [1, 2],
        )
        self.assertEqual(snapshot.watched_episodes, [{"type": "episode", "id": 10}])
        self.assertEqual(snapshot.watchlist_movies[0]["type"], "movie")
        self.assertEqual(snapshot.watchlist_shows[0]["type"], "show")
        self.assertEqual(snapshot.watched_shows, [])

    def test_load_trakt_export_rejects_an_archive_without_supported_data(self):
        stream = archive_with(**{"ratings-movies-1.json": []})

        with self.assertRaisesMessage(TraktExportError, "supported Trakt export data"):
            load_trakt_export(stream)

    @patch("apps.imports.services.MAX_MEMBER_UNCOMPRESSED_SIZE", 1)
    def test_load_trakt_export_rejects_an_oversized_member(self):
        stream = archive_with(**{"watched-shows.json": []})

        with self.assertRaisesMessage(TraktExportError, "too large"):
            load_trakt_export(stream)


class TraktExportImportTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("import@example.com")
        self.other_user = get_user_model().objects.create_user("other@example.com")
        self.watched_movie = Movie.objects.create(
            provider="tmdb",
            external_id="101",
            trakt_id="1",
            tmdb_id="101",
            title="Watched Movie",
        )
        self.watchlist_movie = Movie.objects.create(
            provider="tmdb",
            external_id="102",
            trakt_id="2",
            tmdb_id="102",
            title="Watchlist Movie",
        )
        self.show = Show.objects.create(
            provider="tvdb",
            external_id="201",
            trakt_id="3",
            tvdb_id="201",
            name="Watched Show",
        )

    def test_import_applies_history_and_watchlists_only_to_uploading_user(self):
        stream = archive_with(
            **{
                "watched-movies-1.json": [
                    {
                        "last_watched_at": "2026-07-20T12:00:00Z",
                        "movie": {"ids": {"trakt": 1}, "title": "Watched Movie"},
                    }
                ],
                "watched-shows.json": [
                    {
                        "last_watched_at": "2026-07-20T12:00:00Z",
                        "show": {"ids": {"trakt": 3}},
                    }
                ],
                "watched-history-1.json": [
                    {
                        "watched_at": "2026-07-20T12:00:00Z",
                        "type": "episode",
                        "show": {"ids": {"trakt": 3}},
                        "episode": {
                            "ids": {"trakt": 301},
                            "season": 1,
                            "number": 2,
                            "title": "Episode Two",
                        },
                    }
                ],
                "lists-watchlist.json": [
                    {"type": "movie", "movie": {"ids": {"trakt": 2}}},
                    {"type": "show", "show": {"ids": {"trakt": 3}}},
                ],
            }
        )

        report = import_trakt_export(self.user, stream)

        watched_movie_state = UserMovie.objects.get(
            user=self.user, movie=self.watched_movie
        )
        watchlist_movie_state = UserMovie.objects.get(
            user=self.user, movie=self.watchlist_movie
        )
        show_state = UserShow.objects.get(user=self.user, show=self.show)
        episode = Episode.objects.get(show=self.show, season_number=1, episode_number=2)

        self.assertTrue(watched_movie_state.is_seen)
        self.assertEqual(
            watched_movie_state.seen_at.isoformat(), "2026-07-20T12:00:00+00:00"
        )
        self.assertTrue(watchlist_movie_state.on_watchlist)
        self.assertEqual(show_state.status, UserShow.Status.TRACKED)
        self.assertTrue(show_state.on_watchlist)
        self.assertTrue(
            UserEpisode.objects.filter(user=self.user, episode=episode).exists()
        )
        self.assertEqual(report.episodes_marked, 1)
        self.assertFalse(UserMovie.objects.filter(user=self.other_user).exists())
        self.assertFalse(UserShow.objects.filter(user=self.other_user).exists())

    @patch("apps.trakt.sync._apply_remote_shows", side_effect=RuntimeError("broken"))
    def test_import_rolls_back_partial_state_when_reconciliation_fails(
        self, _apply_remote_shows
    ):
        stream = archive_with(
            **{
                "watched-movies-1.json": [
                    {
                        "last_watched_at": "2026-07-20T12:00:00Z",
                        "movie": {"ids": {"trakt": 1}},
                    }
                ]
            }
        )

        with self.assertRaisesMessage(RuntimeError, "broken"):
            import_trakt_export(self.user, stream)

        self.assertFalse(
            UserMovie.objects.filter(user=self.user, movie=self.watched_movie).exists()
        )
