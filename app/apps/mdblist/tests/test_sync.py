from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.test import TestCase
from django.utils import timezone

from apps.catalog.models import MediaRating
from apps.mdblist.models import MdblistAccount, MdblistLibraryItem, MdblistSyncIntent
from apps.mdblist.sync import sync_account
from apps.movies.models import Movie, UserMovie
from apps.movies.services import mark_seen, unmark_seen
from apps.tv.models import Episode, Season, Show, UserEpisode, UserShow
from apps.tv.services import drop_show, pause_show


EMPTY_WRITE = {"updated": {"movies": 0, "shows": 0, "episodes": 0}, "not_found": {"movies": [], "shows": [], "episodes": []}}


def activities(stamp, *, watchlisted=None, dropped=None, journal=None):
    """``/sync/last_activities`` where every bucket moved at ``stamp`` unless
    told otherwise."""
    return {
        "watchlisted_at": watchlisted or stamp,
        "watched_at": stamp,
        "season_watched_at": None,
        "episode_watched_at": stamp,
        "rated_at": stamp,
        "journal_at": journal or stamp,
        "dropped_at": dropped or stamp,
        "server_time": stamp,
    }


def watched_movie(tmdb, watched_at, *, title="Movie", imdb=None):
    ids = {"tmdb": tmdb, "mdblist": f"m{tmdb}"}
    if imdb:
        ids["imdb"] = imdb
    return {"last_watched_at": watched_at, "movie": {"title": title, "year": 2000, "ids": ids}}


def watched_episode(show_tmdb, season, number, watched_at, *, title="Show"):
    return {
        "last_watched_at": watched_at,
        "episode": {
            "season": season,
            "number": number,
            "ids": {"tmdb": show_tmdb * 100 + number},
            "show": {"title": title, "year": 2004, "ids": {"tmdb": show_tmdb, "mdblist": f"s{show_tmdb}"}},
        },
    }


def rated(media_type, tmdb, rating):
    return {"rated_at": "2026-05-01T00:00:00Z", "rating": rating, media_type: {"title": "X", "ids": {"tmdb": tmdb}}}


def journal_row(category, item_type, tmdb, status, *, value_at=None, season=None, episode=None, rating=None, action_at="2026-06-01T00:00:00Z"):
    row = {
        "category": category,
        "item_type": item_type,
        "ids": {"tmdb": tmdb, "mdblist": "x"},
        "status": status,
        "action_at": action_at,
        "value_at": value_at,
    }
    if season is not None:
        row["season"] = season
        row["episode"] = episode
    if rating is not None:
        row["rating"] = rating
    return row


class FakeMdblistClient:
    def __init__(self, *, activities_responses, watched=None, ratings=None, watchlist=None, dropped=None, journal=None):
        self.activities_responses = list(activities_responses)
        self.watched = watched or {}
        self.ratings = ratings or {}
        self.watchlist = watchlist or {}
        self.dropped = dropped or {}
        self.journal = journal if journal is not None else {"journal": []}
        self.calls = []
        self.writes: dict[str, list] = {}
        self.write_response = dict(EMPTY_WRITE)
        self.profile = {"username": "mdbuser", "user_id": 7, "plan": "Free", "is_supporter": False}

    def get_user(self):
        self.calls.append("user")
        return self.profile

    def get_activities(self):
        self.calls.append("activities")
        if len(self.activities_responses) > 1:
            return self.activities_responses.pop(0)
        return self.activities_responses[0]

    def get_journal(self, since):
        self.calls.append(("journal", since))
        return {"requires_full_sync": False, **self.journal}

    def get_watched(self, *, ids_only=False):
        self.calls.append("watched")
        return {"movies": [], "episodes": [], **self.watched}

    def get_ratings(self):
        self.calls.append("ratings")
        return {"movies": [], "shows": [], **self.ratings}

    def get_watchlist(self, *, ids_only=True):
        self.calls.append("watchlist")
        return {"movies": [], "shows": [], **self.watchlist}

    def get_dropped(self):
        self.calls.append("dropped")
        return {"shows": [], **self.dropped}

    def _write(self, name, payload):
        self.writes.setdefault(name, []).append(payload)
        return self.write_response

    def add_watched(self, payload):
        return self._write("add_watched", payload)

    def remove_watched(self, payload):
        return self._write("remove_watched", payload)

    def add_ratings(self, payload):
        return self._write("add_ratings", payload)

    def remove_ratings(self, payload):
        return self._write("remove_ratings", payload)

    def add_dropped(self, payload):
        return self._write("add_dropped", payload)

    def remove_dropped(self, payload):
        return self._write("remove_dropped", payload)

    def add_to_watchlist(self, payload):
        return self._write("add_to_watchlist", payload)

    def remove_from_watchlist(self, payload):
        return self._write("remove_from_watchlist", payload)


def factory(client):
    return lambda _account: client


def rating_for(user, media):
    return MediaRating.objects.filter(
        user=user,
        content_type=ContentType.objects.get_for_model(type(media)),
        object_id=media.pk,
    ).first()


V1 = "2026-05-01T00:00:00.000Z"
V2 = "2026-06-01T00:00:00.000Z"


class MdblistSyncTestCase(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("user@example.com", password="password")
        self.account = MdblistAccount.objects.create(
            user=self.user,
            access_token="key",
            auth_method=MdblistAccount.AuthMethod.API_KEY,
        )
        self.movie = Movie.objects.create(
            external_id="550",
            tmdb_id="550",
            imdb_id="tt0137523",
            title="Fight Club",
            last_synced_at=timezone.now(),
        )
        # A TVDB-sourced show: MDBList only knows it by its TMDB id.
        self.show = Show.objects.create(
            external_id="73739",
            tvdb_id="73739",
            tmdb_id="4607",
            imdb_id="tt0411008",
            name="Lost",
            last_synced_at=timezone.now(),
        )
        self.season = Season.objects.create(show=self.show, season_number=1)
        self.episodes = {
            number: Episode.objects.create(
                show=self.show,
                season=self.season,
                season_number=1,
                episode_number=number,
                name=f"Episode {number}",
            )
            for number in (1, 2, 3)
        }

    def run_sync(self, client):
        report = sync_account(self.account.id, client_factory=factory(client))
        self.account.refresh_from_db()
        return report

    def complete_initial_sync(self, **library):
        client = FakeMdblistClient(activities_responses=[activities(V1)], **library)
        self.run_sync(client)
        return client

    def local_episode_numbers(self):
        return set(
            UserEpisode.objects.filter(user=self.user).values_list("episode__episode_number", flat=True)
        )


class InitialSyncTests(MdblistSyncTestCase):
    def test_full_pull_imports_remote_state_and_stores_cursor(self):
        client = FakeMdblistClient(
            activities_responses=[activities(V1)],
            watched={
                "movies": [watched_movie(550, "2026-03-01T20:00:00Z", imdb="tt0137523")],
                "episodes": [
                    watched_episode(4607, 1, 1, "2026-03-02T20:00:00Z"),
                    watched_episode(4607, 1, 2, "2026-03-03T20:00:00Z"),
                ],
            },
            ratings={"movies": [rated("movie", 550, 8)], "shows": [rated("show", 4607, 9)]},
        )

        report = self.run_sync(client)

        state = UserMovie.objects.get(user=self.user, movie=self.movie)
        self.assertTrue(state.is_seen)
        self.assertEqual(state.seen_at.isoformat(), "2026-03-01T20:00:00+00:00")
        self.assertEqual(rating_for(self.user, self.movie).score, Decimal("4.0"))
        user_show = UserShow.objects.get(user=self.user, show=self.show)
        self.assertEqual(user_show.status, UserShow.Status.TRACKED)
        self.assertEqual(self.local_episode_numbers(), {1, 2})
        self.assertEqual(rating_for(self.user, self.show).score, Decimal("4.5"))
        self.assertEqual(report.episodes_marked, 2)

        self.assertEqual(self.account.activities_cursor, V1)
        self.assertTrue(self.account.initial_sync_complete)
        self.assertEqual(self.account.mdblist_username, "mdbuser")
        rows = {(row.media_type, row.tmdb_id): row for row in MdblistLibraryItem.objects.all()}
        self.assertEqual(rows[("show", 4607)].watched_episodes.keys(), {"1:1", "1:2"})
        self.assertIsNotNone(rows[("movie", 550)].watched_at)
        self.assertEqual(rows[("movie", 550)].user_rating, 8)
        # A library that already matches local state pushes nothing.
        self.assertEqual(client.writes, {})

    def test_local_state_is_pushed_when_missing_remotely(self):
        seen_at = timezone.now() - timedelta(days=2)
        UserMovie.objects.create(user=self.user, movie=self.movie, is_seen=True, seen_at=seen_at)
        other = Movie.objects.create(external_id="27205", tmdb_id="27205", title="Inception")
        UserMovie.objects.create(user=self.user, movie=other, on_watchlist=True)
        UserShow.objects.create(user=self.user, show=self.show)
        UserEpisode.objects.create(user=self.user, episode=self.episodes[1], seen_at=seen_at)
        dropped = Show.objects.create(external_id="81189", tvdb_id="81189", tmdb_id="1396", name="Breaking Bad")
        UserShow.objects.create(user=self.user, show=dropped, status=UserShow.Status.DROPPED)
        MediaRating.objects.create(
            user=self.user,
            media_type=MediaRating.MediaType.MOVIE,
            content_type=ContentType.objects.get_for_model(Movie),
            object_id=self.movie.pk,
            score=Decimal("3.5"),
        )

        client = self.complete_initial_sync()

        [watched] = client.writes["add_watched"]
        self.assertEqual(watched["movies"][0]["ids"]["tmdb"], 550)
        self.assertTrue(watched["movies"][0]["watched_at"])
        [show] = watched["shows"]
        self.assertEqual(show["ids"], {"imdb": "tt0411008", "tmdb": 4607, "tvdb": 73739})
        self.assertEqual(show["seasons"], [{"number": 1, "episodes": [{"number": 1, "watched_at": show["seasons"][0]["episodes"][0]["watched_at"]}]}])
        self.assertEqual(client.writes["add_to_watchlist"][0]["movies"][0]["ids"]["tmdb"], 27205)
        self.assertEqual(client.writes["add_dropped"][0], {"shows": [{"ids": {"tmdb": 1396, "tvdb": 81189}, "title": "Breaking Bad"}]})
        self.assertEqual(client.writes["add_ratings"][0]["movies"][0]["rating"], 7)

        # Nothing is re-sent before MDBList echoes it back.
        client.writes.clear()
        client.activities_responses = [activities(V1)]
        self.run_sync(client)
        self.assertEqual(client.writes, {})

    def test_paused_show_is_not_dropped_by_the_first_pull(self):
        pause_show(self.user, self.show)
        UserEpisode.objects.create(user=self.user, episode=self.episodes[1], seen_at=timezone.now())

        self.complete_initial_sync(
            watched={"episodes": [watched_episode(4607, 1, 1, "2026-03-02T20:00:00Z")]},
            dropped={"shows": [{"dropped_at": V1, "show": {"title": "Lost", "ids": {"tmdb": 4607}}}]},
        )

        self.assertEqual(UserShow.objects.get(user=self.user, show=self.show).status, UserShow.Status.PAUSED)


class DeltaSyncTests(MdblistSyncTestCase):
    def test_nothing_is_read_when_no_stamp_moved(self):
        self.complete_initial_sync()
        client = FakeMdblistClient(activities_responses=[activities(V1)])

        self.run_sync(client)

        self.assertEqual(client.calls, ["activities"])

    def test_journal_replays_additions_and_removals(self):
        UserMovie.objects.create(user=self.user, movie=self.movie, is_seen=True, seen_at=timezone.now())
        self.complete_initial_sync(
            watched={
                "movies": [watched_movie(550, "2026-03-01T20:00:00Z")],
                "episodes": [watched_episode(4607, 1, 1, "2026-03-02T20:00:00Z")],
            },
        )
        self.assertEqual(self.local_episode_numbers(), {1})

        client = FakeMdblistClient(
            activities_responses=[activities(V2, watchlisted=V1, dropped=V1)],
            journal={
                "journal": [
                    journal_row("watched", "movie", 550, "removed"),
                    journal_row("watched", "episode", 4607, "removed", season=1, episode=1),
                    journal_row("watched", "episode", 4607, "added", season=1, episode=2, value_at="2026-06-01T10:00:00Z"),
                    journal_row("rated", "movie", 550, "added", rating=6),
                ]
            },
        )
        report = self.run_sync(client)

        self.assertEqual(client.calls, ["activities", ("journal", V1)])
        self.assertFalse(UserMovie.objects.filter(user=self.user, movie=self.movie, is_seen=True).exists())
        self.assertEqual(self.local_episode_numbers(), {2})
        self.assertEqual(report.episodes_unmarked, 1)
        # A movie that is no longer watched cannot keep a rating locally.
        self.assertIsNone(rating_for(self.user, self.movie))
        row = MdblistLibraryItem.objects.get(media_type="movie", tmdb_id=550)
        self.assertIsNone(row.watched_at)
        self.assertEqual(row.user_rating, 6)
        self.assertEqual(self.account.activities_cursor, V2)
        self.assertEqual(client.writes, {})

    def test_rating_removal_clears_the_local_rating(self):
        UserMovie.objects.create(user=self.user, movie=self.movie, is_seen=True, seen_at=timezone.now())
        self.complete_initial_sync(
            watched={"movies": [watched_movie(550, "2026-03-01T20:00:00Z")]},
            ratings={"movies": [rated("movie", 550, 8)]},
        )
        self.assertEqual(rating_for(self.user, self.movie).score, Decimal("4.0"))

        self.run_sync(
            FakeMdblistClient(
                activities_responses=[activities(V2)],
                journal={"journal": [journal_row("rated", "movie", 550, "removed")]},
            )
        )

        self.assertIsNone(rating_for(self.user, self.movie))

    def test_expired_journal_falls_back_to_a_full_pull(self):
        self.complete_initial_sync(watched={"episodes": [watched_episode(4607, 1, 1, "2026-03-02T20:00:00Z")]})
        client = FakeMdblistClient(
            activities_responses=[activities(V2)],
            journal={"journal": [], "requires_full_sync": True},
            watched={"episodes": [watched_episode(4607, 1, 3, "2026-06-01T00:00:00Z")]},
        )

        self.run_sync(client)

        self.assertIn("watched", client.calls)
        self.assertEqual(self.local_episode_numbers(), {3})

    def test_season_level_journal_rows_reread_the_watched_snapshot(self):
        self.complete_initial_sync()
        client = FakeMdblistClient(
            activities_responses=[activities(V2)],
            journal={"journal": [journal_row("watched", "season", 4607, "added", season=1)]},
            watched={"episodes": [watched_episode(4607, 1, n, "2026-06-01T00:00:00Z") for n in (1, 2, 3)]},
        )

        self.run_sync(client)

        self.assertEqual(self.local_episode_numbers(), {1, 2, 3})

    def test_watchlist_and_dropped_snapshots_are_read_when_their_stamps_move(self):
        UserShow.objects.create(user=self.user, show=self.show)
        UserEpisode.objects.create(user=self.user, episode=self.episodes[1], seen_at=timezone.now())
        self.complete_initial_sync(
            watched={"episodes": [watched_episode(4607, 1, 1, "2026-03-02T20:00:00Z")]},
            watchlist={"movies": [{"ids": {"tmdb": 550}, "title": "Fight Club"}]},
        )
        self.assertTrue(UserMovie.objects.get(user=self.user, movie=self.movie).on_watchlist)

        client = FakeMdblistClient(
            activities_responses=[{**activities(V1), "watchlisted_at": V2, "dropped_at": V2, "server_time": V2}],
            dropped={"shows": [{"dropped_at": V2, "show": {"title": "Lost", "ids": {"tmdb": 4607}}}]},
        )
        self.run_sync(client)

        self.assertEqual(client.calls, ["activities", "watchlist", "dropped"])
        # Removed from the watchlist without a watch: gone locally too.
        self.assertFalse(UserMovie.objects.filter(user=self.user, movie=self.movie).exists())
        self.assertEqual(UserShow.objects.get(user=self.user, show=self.show).status, UserShow.Status.DROPPED)

        self.run_sync(
            FakeMdblistClient(
                activities_responses=[{**activities(V2), "dropped_at": "2026-07-01T00:00:00Z"}],
            )
        )
        self.assertEqual(UserShow.objects.get(user=self.user, show=self.show).status, UserShow.Status.TRACKED)

    def test_watching_a_listed_movie_on_mdblist_marks_it_seen(self):
        self.complete_initial_sync(watchlist={"movies": [{"ids": {"tmdb": 550}}]})

        # MDBList drops a movie from the watchlist when it is watched.
        self.run_sync(
            FakeMdblistClient(
                activities_responses=[activities(V2)],
                journal={"journal": [journal_row("watched", "movie", 550, "added", value_at="2026-06-01T00:00:00Z")]},
            )
        )

        state = UserMovie.objects.get(user=self.user, movie=self.movie)
        self.assertTrue(state.is_seen)
        self.assertFalse(state.on_watchlist)

    def test_removal_guard_ignores_a_suspicious_wipe(self):
        watchlist = {"movies": [{"ids": {"tmdb": 1000 + number}} for number in range(30)]}
        for number in range(30):
            movie = Movie.objects.create(external_id=str(1000 + number), tmdb_id=str(1000 + number), title=f"M{number}")
            UserMovie.objects.create(user=self.user, movie=movie, on_watchlist=True)
        self.complete_initial_sync(watchlist=watchlist)

        report = self.run_sync(
            FakeMdblistClient(activities_responses=[{**activities(V1), "watchlisted_at": V2}])
        )

        self.assertEqual(UserMovie.objects.filter(user=self.user, on_watchlist=True).count(), 30)
        self.assertTrue(any("safety" in warning for warning in report.warnings))


class LocalChangeTests(MdblistSyncTestCase):
    def test_local_unwatch_removes_history_and_relists(self):
        mark_seen(self.user, self.movie)
        self.complete_initial_sync(watched={"movies": [watched_movie(550, "2026-03-01T20:00:00Z")]})
        MdblistSyncIntent.objects.all().delete()

        unmark_seen(self.user, self.movie)
        client = FakeMdblistClient(activities_responses=[activities(V1)])
        self.run_sync(client)

        self.assertEqual(client.writes["remove_watched"], [{"movies": [{"ids": {"imdb": "tt0137523", "tmdb": 550}, "title": "Fight Club"}]}])
        self.assertEqual(client.writes["add_to_watchlist"][0]["movies"][0]["ids"]["tmdb"], 550)
        self.assertFalse(MdblistSyncIntent.objects.exists())

    def test_local_drop_and_rating_are_pushed(self):
        UserShow.objects.create(user=self.user, show=self.show)
        UserEpisode.objects.create(user=self.user, episode=self.episodes[1], seen_at=timezone.now())
        self.complete_initial_sync(watched={"episodes": [watched_episode(4607, 1, 1, "2026-03-02T20:00:00Z")]})

        drop_show(self.user, self.show)
        from apps.catalog.ratings import rate_media

        rate_media(self.user, "show", self.show, Decimal("2.5"))
        client = FakeMdblistClient(activities_responses=[activities(V1)])
        self.run_sync(client)

        self.assertEqual(client.writes["add_dropped"][0]["shows"][0]["ids"]["tmdb"], 4607)
        self.assertEqual(client.writes["add_ratings"][0]["shows"][0]["rating"], 5)

    def test_pending_unwatch_survives_a_stale_journal_echo(self):
        mark_seen(self.user, self.movie)
        self.complete_initial_sync(watched={"movies": [watched_movie(550, "2026-03-01T20:00:00Z")]})
        unmark_seen(self.user, self.movie)

        # The journal still reports the old watch (it predates the unwatch).
        client = FakeMdblistClient(
            activities_responses=[activities(V2)],
            journal={"journal": [journal_row("watched", "movie", 550, "added", value_at="2026-03-02T20:00:00Z")]},
        )
        self.run_sync(client)

        self.assertFalse(UserMovie.objects.get(user=self.user, movie=self.movie).is_seen)
        self.assertIn("remove_watched", client.writes)

    def test_unmatched_titles_are_remembered_until_a_manual_sync(self):
        other = Movie.objects.create(external_id="999", tmdb_id="999", title="Unknown")
        UserMovie.objects.create(user=self.user, movie=other, is_seen=True, seen_at=timezone.now())
        client = FakeMdblistClient(activities_responses=[activities(V1)])
        client.write_response = {"updated": {}, "not_found": {"movies": [{"ids": {"tmdb": 999}}]}}

        report = self.run_sync(client)

        self.assertTrue(any("could not match" in warning for warning in report.warnings))
        self.assertEqual(self.account.pending_pushes["tmdb:999"]["reason"], "not_found")
        client.writes.clear()
        client.write_response = dict(EMPTY_WRITE)
        client.activities_responses = [activities(V2)]
        self.run_sync(client)
        self.assertEqual(client.writes, {})

    def test_large_episode_pushes_are_split_by_show_count(self):
        shows = []
        for number in range(205):
            show = Show.objects.create(external_id=f"t{number}", tmdb_id=str(50000 + number), name=f"S{number}", provider="tmdb")
            season = Season.objects.create(show=show, season_number=1)
            episode = Episode.objects.create(show=show, season=season, season_number=1, episode_number=1)
            UserEpisode.objects.create(user=self.user, episode=episode, seen_at=timezone.now())
            shows.append(show)

        client = self.complete_initial_sync()

        sizes = [len(body.get("shows", [])) for body in client.writes["add_watched"]]
        self.assertEqual(sizes, [200, 5])
