from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.test import TestCase
from django.utils import timezone

from apps.catalog.models import MediaRating
from apps.movies.models import Movie, UserMovie
from apps.movies.services import mark_seen, unmark_seen
from apps.simkl.models import SimklAccount, SimklLibraryItem, SimklSyncIntent
from apps.simkl.sync import normalize_items, sync_account
from apps.tv.models import Episode, Season, Show, UserEpisode, UserShow
from apps.tv.services import pause_show


EMPTY_WRITE = {"added": {"movies": 0, "shows": 0, "episodes": 0, "statuses": []}, "not_found": {"movies": [], "shows": []}}

ACTIVITIES_V1 = {
    "all": "2026-05-01T00:00:00Z",
    "settings": {"all": "2026-01-01T00:00:00Z"},
    "tv_shows": {"all": "2026-05-01T00:00:00Z", "removed_from_list": None},
    "movies": {"all": "2026-05-01T00:00:00Z", "removed_from_list": None},
    "anime": {"all": None, "removed_from_list": None},
}


def activities(stamp, *, tv=None, movies=None, anime=None, removed_tv=None, removed_movies=None):
    return {
        "all": stamp,
        "settings": {"all": "2026-01-01T00:00:00Z"},
        "tv_shows": {"all": tv or stamp, "removed_from_list": removed_tv},
        "movies": {"all": movies or stamp, "removed_from_list": removed_movies},
        "anime": {"all": anime, "removed_from_list": None},
    }


def movie_entry(simkl_id, status, *, tmdb=None, imdb=None, watched_at=None, rating=None, title="Movie"):
    ids = {"simkl": simkl_id, "slug": "movie"}
    if tmdb:
        ids["tmdb"] = str(tmdb)
    if imdb:
        ids["imdb"] = imdb
    return {
        "added_to_watchlist_at": "2026-04-01T00:00:00Z",
        "last_watched_at": watched_at,
        "user_rating": rating,
        "status": status,
        "watched_episodes_count": 0,
        "total_episodes_count": 0,
        "movie": {"title": title, "year": 2020, "ids": ids},
    }


def show_entry(simkl_id, status, *, tvdb=None, tmdb=None, episodes=(), rating=None, title="Show", watched_at=None, seasons_present=True, anime=False):
    ids = {"simkl": simkl_id, "slug": "show"}
    if tvdb:
        ids["tvdb"] = str(tvdb)
    if tmdb:
        ids["tmdb"] = str(tmdb)
    seasons: dict[int, list] = {}
    for season_number, episode_number, ts in episodes:
        seasons.setdefault(season_number, []).append({"number": episode_number, "watched_at": ts})
    entry = {
        "added_to_watchlist_at": "2026-04-01T00:00:00Z",
        "last_watched_at": watched_at or (episodes[-1][2] if episodes else None),
        "user_rating": rating,
        "status": status,
        "watched_episodes_count": len(episodes),
        "total_episodes_count": 10,
        "show": {"title": title, "year": 2010, "ids": ids},
    }
    if seasons_present:
        entry["seasons"] = [
            {"number": number, "episodes": items} for number, items in sorted(seasons.items())
        ]
    if anime:
        entry["anime_type"] = "tv"
    return entry


class FakeSimklClient:
    def __init__(self, *, activities_responses, library=None, deltas=None, ids_only=None):
        self.activities_responses = list(activities_responses)
        self.library = library or {}
        self.deltas = deltas or {}
        self.ids_only = ids_only or {}
        self.calls = []
        self.history_add = []
        self.history_remove = []
        self.list_moves = []
        self.ratings_add = []
        self.ratings_remove = []
        self.write_response = dict(EMPTY_WRITE)
        self.profile = {"user": {"name": "simkluser"}, "account": {"id": 77, "type": "free"}}

    def get_user_settings(self):
        self.calls.append(("settings",))
        return self.profile

    def get_activities(self):
        self.calls.append(("activities",))
        if len(self.activities_responses) > 1:
            return self.activities_responses.pop(0)
        return self.activities_responses[0]

    def get_all_items(self, media_type=None, status=None, *, date_from=None, extended=None, episode_watched_at=False, include_all_episodes=False):
        self.calls.append(("all_items", media_type, date_from, extended))
        if extended == "ids_only":
            return self.ids_only.get(media_type, {media_type: []})
        if date_from:
            if media_type is None:
                combined = {}
                for payload in self.deltas.values():
                    combined.update(payload)
                return combined
            return self.deltas.get(media_type, {})
        return self.library.get(media_type, {})

    def add_to_history(self, payload):
        self.history_add.append(payload)
        return self.write_response

    def remove_from_history(self, payload):
        self.history_remove.append(payload)
        return self.write_response

    def add_to_list(self, payload):
        self.list_moves.append(payload)
        return self.write_response

    def add_ratings(self, payload):
        self.ratings_add.append(payload)
        return self.write_response

    def remove_ratings(self, payload):
        self.ratings_remove.append(payload)
        return self.write_response


def factory(client):
    return lambda _account: client


def rating_for(user, media):
    return MediaRating.objects.filter(
        user=user,
        content_type=ContentType.objects.get_for_model(type(media)),
        object_id=media.pk,
    ).first()


class SimklSyncTestCase(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("user@example.com", password="password")
        self.account = SimklAccount.objects.create(user=self.user, access_token="token")
        self.movie = Movie.objects.create(
            external_id="550",
            tmdb_id="550",
            imdb_id="tt0137523",
            title="Fight Club",
            last_synced_at=timezone.now(),
        )
        self.show = Show.objects.create(
            external_id="73739",
            tvdb_id="73739",
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

    def complete_initial_sync(self, client=None):
        client = client or FakeSimklClient(activities_responses=[ACTIVITIES_V1])
        self.run_sync(client)
        return client


class InitialSyncTests(SimklSyncTestCase):
    def test_full_pull_imports_remote_state_and_stores_cursor(self):
        client = FakeSimklClient(
            activities_responses=[ACTIVITIES_V1],
            library={
                "movies": {"movies": [movie_entry(1, "completed", tmdb=550, watched_at="2026-03-01T20:00:00Z", rating=8)]},
                "shows": {
                    "shows": [
                        show_entry(
                            2,
                            "watching",
                            tvdb=73739,
                            episodes=[(1, 1, "2026-03-02T20:00:00Z"), (1, 2, "2026-03-03T20:00:00Z")],
                            rating=9,
                        )
                    ]
                },
            },
        )

        report = self.run_sync(client)

        state = UserMovie.objects.get(user=self.user, movie=self.movie)
        self.assertTrue(state.is_seen)
        self.assertEqual(state.seen_at.isoformat(), "2026-03-01T20:00:00+00:00")
        self.assertEqual(rating_for(self.user, self.movie).score, Decimal("4.0"))

        user_show = UserShow.objects.get(user=self.user, show=self.show)
        self.assertEqual(user_show.status, UserShow.Status.TRACKED)
        self.assertFalse(user_show.on_watchlist)
        self.assertEqual(
            set(UserEpisode.objects.filter(user=self.user).values_list("episode__episode_number", flat=True)),
            {1, 2},
        )
        self.assertEqual(rating_for(self.user, self.show).score, Decimal("4.5"))
        self.assertEqual(report.episodes_marked, 2)

        self.assertEqual(self.account.activities_cursor, "2026-05-01T00:00:00Z")
        self.assertTrue(self.account.initial_sync_complete)
        self.assertEqual(self.account.simkl_username, "simkluser")
        self.assertEqual(self.account.account_type, "free")
        rows = {(row.media_type, row.simkl_id): row for row in SimklLibraryItem.objects.all()}
        self.assertEqual(rows[("show", 2)].watched_episodes.keys(), {"1:1", "1:2"})
        self.assertEqual(rows[("movie", 1)].status, "completed")

        pull_calls = [call for call in client.calls if call[0] == "all_items"]
        self.assertEqual([call[1] for call in pull_calls], ["shows", "movies", "anime"])
        self.assertEqual(pull_calls[0][3], "full")
        self.assertEqual(pull_calls[2][3], "full_anime_seasons")
        # A library that already matches local state pushes nothing.
        self.assertEqual(client.history_add, [])

    def test_local_state_is_pushed_when_missing_remotely(self):
        seen_at = timezone.now() - timedelta(days=2)
        UserMovie.objects.create(user=self.user, movie=self.movie, is_seen=True, seen_at=seen_at)
        other = Movie.objects.create(external_id="27205", tmdb_id="27205", title="Inception")
        UserMovie.objects.create(user=self.user, movie=other, on_watchlist=True)
        UserShow.objects.create(user=self.user, show=self.show)
        UserEpisode.objects.create(user=self.user, episode=self.episodes[1], seen_at=seen_at)
        paused = Show.objects.create(external_id="81189", tvdb_id="81189", name="Breaking Bad")
        UserShow.objects.create(user=self.user, show=paused, status=UserShow.Status.PAUSED)
        MediaRating.objects.create(
            user=self.user,
            media_type=MediaRating.MediaType.MOVIE,
            content_type=ContentType.objects.get_for_model(Movie),
            object_id=self.movie.pk,
            score=Decimal("4.5"),
        )
        client = FakeSimklClient(activities_responses=[ACTIVITIES_V1])

        report = self.run_sync(client)

        self.assertEqual(len(client.history_add), 1)
        history = client.history_add[0]
        self.assertEqual(history["movies"][0]["ids"], {"imdb": "tt0137523", "tmdb": "550"})
        self.assertEqual(history["movies"][0]["watched_at"], seen_at.isoformat())
        self.assertEqual(history["shows"][0]["ids"], {"imdb": "tt0411008", "tvdb": "73739"})
        self.assertTrue(history["shows"][0]["use_tvdb_anime_seasons"])
        self.assertEqual(history["shows"][0]["seasons"][0]["episodes"][0]["number"], 1)

        moves = client.list_moves[0]
        self.assertEqual(moves["movies"], [{"ids": {"tmdb": "27205"}, "title": "Inception", "to": "plantowatch"}])
        self.assertEqual(
            [(item["ids"], item["to"]) for item in moves["shows"]],
            [({"tvdb": "81189"}, "hold")],
        )
        self.assertEqual(client.ratings_add[0]["movies"], [{"ids": {"imdb": "tt0137523", "tmdb": "550"}, "title": "Fight Club", "rating": 9}])
        self.assertGreater(report.items_pushed, 0)
        # Everything sent is remembered until SIMKL echoes it back.
        self.assertIn("imdb:tt0137523", self.account.pending_pushes)
        self.assertIn("episode:imdb:tt0411008:s1:e1", self.account.pending_pushes)

    def test_local_paused_and_dropped_win_on_first_sync(self):
        UserShow.objects.create(user=self.user, show=self.show, status=UserShow.Status.PAUSED)
        client = FakeSimklClient(
            activities_responses=[ACTIVITIES_V1],
            library={"shows": {"shows": [show_entry(2, "watching", tvdb=73739, episodes=[(1, 1, "2026-03-02T20:00:00Z")])]}},
        )

        self.run_sync(client)

        self.assertEqual(UserShow.objects.get(show=self.show).status, UserShow.Status.PAUSED)
        self.assertEqual(client.list_moves[0]["shows"][0]["to"], "hold")

    def test_anime_episodes_use_tvdb_coordinates(self):
        entry = show_entry(5, "watching", tvdb=73739, anime=True, seasons_present=False, watched_at="2026-03-02T20:00:00Z")
        entry["mapped_tvdb_seasons"] = [2]
        entry["seasons"] = [
            {
                "number": 1,
                "episodes": [
                    {"number": 1, "watched_at": "2026-03-02T20:00:00Z", "tvdb": {"season": 2, "episode": 1}},
                    {"number": 2, "watched_at": "2026-03-03T20:00:00Z"},
                ],
            }
        ]
        client = FakeSimklClient(
            activities_responses=[ACTIVITIES_V1],
            library={"anime": {"anime": [entry]}},
        )

        self.run_sync(client)

        positions = set(
            UserEpisode.objects.filter(user=self.user).values_list(
                "episode__season_number", "episode__episode_number"
            )
        )
        self.assertEqual(positions, {(2, 1), (2, 2)})
        self.assertEqual(Show.objects.get(pk=self.show.pk).name, "Lost")

    def test_normalize_items_reads_flat_and_nested_shapes(self):
        payload = {
            "movies": [movie_entry(1, "plantowatch", imdb="tt1")],
            "shows": [show_entry(2, "completed", tvdb=1, episodes=[(1, 1, "2026-01-01T00:00:00Z")])],
        }

        items = normalize_items(payload)

        self.assertEqual(set(items), {("movie", 1), ("show", 2)})
        self.assertEqual(items[("show", 2)].episodes.keys(), {"1:1"})
        self.assertTrue(items[("show", 2)].has_episode_data)
        self.assertFalse(items[("movie", 1)].has_episode_data)


class ContinuousSyncTests(SimklSyncTestCase):
    def test_unchanged_activities_skip_library_reads(self):
        client = self.complete_initial_sync()
        client.calls.clear()

        self.run_sync(client)

        self.assertEqual([call[0] for call in client.calls], ["activities"])

    def test_intents_are_pushed_and_acknowledged(self):
        client = self.complete_initial_sync()
        client.calls.clear()
        mark_seen(self.user, self.movie)
        self.assertTrue(SimklSyncIntent.objects.exists())

        self.run_sync(client)

        self.assertEqual(client.history_add[0]["movies"][0]["ids"], {"imdb": "tt0137523", "tmdb": "550"})
        self.assertEqual(client.list_moves, [])
        self.assertFalse(SimklSyncIntent.objects.exists())

    def test_delta_marks_new_remote_episodes_and_unmarks_missing_ones(self):
        library = {"shows": {"shows": [show_entry(2, "watching", tvdb=73739, episodes=[(1, 1, "2026-03-02T20:00:00Z"), (1, 2, "2026-03-03T20:00:00Z")])]}}
        client = FakeSimklClient(
            activities_responses=[ACTIVITIES_V1, activities("2026-05-02T00:00:00Z", movies=ACTIVITIES_V1["movies"]["all"])],
            library=library,
            deltas={"shows": {"shows": [show_entry(2, "watching", tvdb=73739, episodes=[(1, 1, "2026-03-02T20:00:00Z"), (1, 3, "2026-05-01T20:00:00Z")])]}},
        )
        self.run_sync(client)
        self.assertEqual(set(UserEpisode.objects.values_list("episode__episode_number", flat=True)), {1, 2})
        client.calls.clear()

        report = self.run_sync(client)

        self.assertEqual(set(UserEpisode.objects.values_list("episode__episode_number", flat=True)), {1, 3})
        self.assertEqual(report.episodes_unmarked, 1)
        delta_calls = [call for call in client.calls if call[0] == "all_items"]
        self.assertEqual(delta_calls, [("all_items", None, "2026-05-01T00:00:00Z", "full")])
        self.assertEqual(self.account.activities_cursor, "2026-05-02T00:00:00Z")
        # The mirror follows the delta so the next diff starts from truth.
        self.assertEqual(SimklLibraryItem.objects.get(simkl_id=2).watched_episodes.keys(), {"1:1", "1:3"})

    def test_remote_hold_pauses_and_remote_watching_resumes(self):
        client = FakeSimklClient(
            activities_responses=[
                ACTIVITIES_V1,
                activities("2026-05-02T00:00:00Z"),
                activities("2026-05-03T00:00:00Z"),
            ],
            library={"shows": {"shows": [show_entry(2, "watching", tvdb=73739, episodes=[(1, 1, "2026-03-02T20:00:00Z")])]}},
            deltas={"shows": {"shows": [show_entry(2, "hold", tvdb=73739, episodes=[(1, 1, "2026-03-02T20:00:00Z")])]}},
        )
        self.run_sync(client)
        self.run_sync(client)
        self.assertEqual(UserShow.objects.get(show=self.show).status, UserShow.Status.PAUSED)

        client.deltas = {"shows": {"shows": [show_entry(2, "watching", tvdb=73739, episodes=[(1, 1, "2026-03-02T20:00:00Z")])]}}
        self.run_sync(client)

        self.assertEqual(UserShow.objects.get(show=self.show).status, UserShow.Status.TRACKED)
        # Nothing was pushed back: the change came from SIMKL.
        self.assertEqual(client.list_moves, [])

    def test_local_pause_pushes_hold(self):
        client = self.complete_initial_sync(
            FakeSimklClient(
                activities_responses=[ACTIVITIES_V1],
                library={"shows": {"shows": [show_entry(2, "watching", tvdb=73739, episodes=[(1, 1, "2026-03-02T20:00:00Z")])]}},
            )
        )
        pause_show(self.user, self.show)

        self.run_sync(client)

        self.assertEqual(client.list_moves[-1]["shows"], [{"ids": {"imdb": "tt0411008", "tvdb": "73739"}, "title": "Lost", "use_tvdb_anime_seasons": True, "to": "hold"}])

    def test_remote_unwatch_of_a_movie_is_mirrored(self):
        client = FakeSimklClient(
            activities_responses=[ACTIVITIES_V1, activities("2026-05-02T00:00:00Z")],
            library={"movies": {"movies": [movie_entry(1, "completed", tmdb=550, watched_at="2026-03-01T20:00:00Z")]}},
            deltas={"movies": {"movies": [movie_entry(1, "plantowatch", tmdb=550)]}},
        )
        self.run_sync(client)
        self.assertTrue(UserMovie.objects.get(movie=self.movie).is_seen)

        self.run_sync(client)

        state = UserMovie.objects.get(movie=self.movie)
        self.assertFalse(state.is_seen)
        self.assertTrue(state.on_watchlist)
        self.assertEqual(client.history_add, [])

    def test_local_unwatch_removes_history_and_restores_watchlist(self):
        client = self.complete_initial_sync(
            FakeSimklClient(
                activities_responses=[ACTIVITIES_V1],
                library={"movies": {"movies": [movie_entry(1, "completed", tmdb=550, watched_at="2026-03-01T20:00:00Z")]}},
            )
        )
        unmark_seen(self.user, self.movie)

        self.run_sync(client)

        self.assertEqual(client.history_remove[0]["movies"], [{"ids": {"imdb": "tt0137523", "tmdb": "550"}, "title": "Fight Club"}])
        self.assertEqual(client.list_moves[0]["movies"][0]["to"], "plantowatch")
        self.assertEqual(client.history_add, [])

    def test_removed_items_are_detected_by_diffing_ids(self):
        client = FakeSimklClient(
            activities_responses=[
                ACTIVITIES_V1,
                activities("2026-05-02T00:00:00Z", removed_movies="2026-05-02T00:00:00Z", removed_tv="2026-05-02T00:00:00Z"),
            ],
            library={
                "movies": {"movies": [movie_entry(1, "completed", tmdb=550, watched_at="2026-03-01T20:00:00Z", rating=8)]},
                "shows": {"shows": [show_entry(2, "watching", tvdb=73739, episodes=[(1, 1, "2026-03-02T20:00:00Z")])]},
            },
            deltas={},
            ids_only={"movies": {"movies": []}, "shows": {"shows": []}},
        )
        self.run_sync(client)
        self.assertTrue(UserMovie.objects.get(movie=self.movie).is_seen)
        self.assertIsNotNone(rating_for(self.user, self.movie))

        self.run_sync(client)

        self.assertFalse(UserMovie.objects.get(movie=self.movie).is_seen)
        self.assertIsNone(rating_for(self.user, self.movie))
        self.assertFalse(UserEpisode.objects.exists())
        self.assertFalse(UserShow.objects.filter(show=self.show).exists())
        self.assertFalse(SimklLibraryItem.objects.exists())

    def test_mass_removal_is_refused(self):
        entries = [movie_entry(index, "completed", tmdb=str(1000 + index), watched_at="2026-03-01T20:00:00Z", title=f"M{index}") for index in range(1, 40)]
        for index in range(1, 40):
            Movie.objects.create(external_id=str(1000 + index), tmdb_id=str(1000 + index), title=f"M{index}", last_synced_at=timezone.now())
        client = FakeSimklClient(
            activities_responses=[ACTIVITIES_V1, activities("2026-05-02T00:00:00Z", removed_movies="2026-05-02T00:00:00Z")],
            library={"movies": {"movies": entries}},
            ids_only={"movies": {"movies": []}},
        )
        self.run_sync(client)

        report = self.run_sync(client)

        self.assertEqual(UserMovie.objects.filter(is_seen=True).count(), 39)
        self.assertTrue(any("safety" in warning for warning in report.warnings))

    def test_pending_pushes_are_not_repeated_until_echoed(self):
        seen_at = timezone.now() - timedelta(days=2)
        UserMovie.objects.create(user=self.user, movie=self.movie, is_seen=True, seen_at=seen_at)
        client = FakeSimklClient(activities_responses=[ACTIVITIES_V1])
        self.run_sync(client)
        self.assertEqual(len(client.history_add), 1)

        self.run_sync(client)
        self.assertEqual(len(client.history_add), 1)

        # The echo arrives: SIMKL now lists the movie, the pending mark clears.
        client.activities_responses = [activities("2026-05-02T00:00:00Z")]
        client.deltas = {"movies": {"movies": [movie_entry(1, "completed", tmdb=550, watched_at=seen_at.isoformat())]}}
        self.run_sync(client)
        self.assertEqual(self.account.pending_pushes, {})
        self.assertEqual(len(client.history_add), 1)

    def test_not_found_items_are_remembered_and_reported(self):
        UserMovie.objects.create(user=self.user, movie=self.movie, is_seen=True, seen_at=timezone.now())
        client = FakeSimklClient(activities_responses=[ACTIVITIES_V1])
        client.write_response = {
            "added": {"movies": 0, "shows": 0, "episodes": 0, "statuses": []},
            "not_found": {"movies": [{"ids": {"imdb": "tt0137523", "tmdb": "550"}, "title": "Fight Club"}], "shows": []},
        }

        report = self.run_sync(client)

        self.assertTrue(any("Fight Club" in warning for warning in report.warnings))
        self.assertEqual(self.account.pending_pushes["imdb:tt0137523"]["reason"], "not_found")
        self.assertIn("Fight Club", self.account.last_warning)

    def test_remote_rating_changes_flow_both_ways(self):
        client = FakeSimklClient(
            activities_responses=[ACTIVITIES_V1, activities("2026-05-02T00:00:00Z")],
            library={"movies": {"movies": [movie_entry(1, "completed", tmdb=550, watched_at="2026-03-01T20:00:00Z", rating=6)]}},
            deltas={"movies": {"movies": [movie_entry(1, "completed", tmdb=550, watched_at="2026-03-01T20:00:00Z", rating=None)]}},
        )
        self.run_sync(client)
        self.assertEqual(rating_for(self.user, self.movie).score, Decimal("3.0"))

        self.run_sync(client)
        self.assertIsNone(rating_for(self.user, self.movie))

        # A local rating on a watched movie is pushed on the SIMKL scale.
        MediaRating.objects.create(
            user=self.user,
            media_type=MediaRating.MediaType.MOVIE,
            content_type=ContentType.objects.get_for_model(Movie),
            object_id=self.movie.pk,
            score=Decimal("2.5"),
        )
        client.activities_responses = [activities("2026-05-02T00:00:00Z")]
        self.run_sync(client)
        self.assertEqual(client.ratings_add[-1]["movies"][0]["rating"], 5)
