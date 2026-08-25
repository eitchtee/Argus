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

    def test_load_trakt_export_includes_hidden_progress_shows_as_dropped(self):
        hidden_shows = [
            {
                "hidden_at": "2024-09-04T23:30:35Z",
                "type": "show",
                "show": {"ids": {"trakt": 204068}},
            }
        ]

        snapshot = load_trakt_export(
            archive_with(
                **{
                    "hidden-progress-watched.json": hidden_shows,
                    "watched-shows.json": [],
                }
            )
        )

        self.assertEqual(snapshot.dropped_shows, hidden_shows)

    def test_load_trakt_export_reads_ratings_members(self):
        snapshot = load_trakt_export(
            archive_with(
                **{
                    "ratings-movies-2.json": [
                        {"rating": 6, "movie": {"ids": {"trakt": 2}}}
                    ],
                    "ratings-movies-1.json": [
                        {"rating": 8, "movie": {"ids": {"trakt": 1}}}
                    ],
                    "ratings-shows.json": [
                        {"rating": 10, "show": {"ids": {"trakt": 3}}}
                    ],
                    "ratings-episodes.json": [
                        {
                            "rating": 7,
                            "show": {"ids": {"trakt": 3}},
                            "episode": {"season": 1, "number": 2},
                        }
                    ],
                }
            )
        )

        self.assertEqual(
            [item["movie"]["ids"]["trakt"] for item in snapshot.rated_movies],
            [1, 2],
        )
        self.assertEqual(snapshot.rated_shows[0]["rating"], 10)
        self.assertEqual(snapshot.rated_episodes[0]["episode"]["number"], 2)

    def test_load_trakt_export_rejects_an_archive_without_supported_data(self):
        stream = archive_with(**{"user-profile.json": []})

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

    def test_import_marks_hidden_progress_shows_as_dropped(self):
        stream = archive_with(
            **{
                "hidden-progress-watched.json": [
                    {
                        "hidden_at": "2024-09-04T23:30:35Z",
                        "type": "show",
                        "show": {
                            "title": "Watched Show",
                            "ids": {"trakt": 3},
                        },
                    }
                ],
                "watched-shows.json": [],
            }
        )

        import_trakt_export(self.user, stream)

        show_state = UserShow.objects.get(user=self.user, show=self.show)
        self.assertEqual(show_state.status, UserShow.Status.DROPPED)
        self.assertFalse(show_state.on_watchlist)

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
