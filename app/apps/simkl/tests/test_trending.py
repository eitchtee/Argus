from datetime import date, timedelta
from unittest.mock import Mock

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.movies.models import Movie, UserMovie
from apps.simkl import trending
from apps.simkl.client import SimklError
from apps.tv.models import Show, UserShow


ITEM = {
    "title": "King of the Hill",
    "url": "/tv/3437/king-of-the-hill",
    "poster": "12/12634613c2b41dc5a1",
    "ids": {"simkl_id": 3437, "slug": "king-of-the-hill", "imdb": "tt0118375", "tmdb": "1434", "tvdb": "73141"},
    "release_date": "01/12/1997",
    "rank": 1820,
    "watched": 24107,
    "plan_to_watch": 3391,
    "ratings": {"simkl": {"rating": 8.1, "votes": 942}, "imdb": {"rating": 8.0, "votes": 104218}},
    "status": "ongoing",
    "genres": ["Animation", "Comedy"],
    "network": "Hulu",
    "runtime": "22m",
}


@override_settings(SIMKL_CLIENT_ID="client")
class TrendingTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = get_user_model().objects.create_user("user@example.com")

    def tearDown(self):
        cache.clear()

    def test_entries_are_parsed_and_cached(self):
        client = Mock()
        client.get_trending.return_value = [ITEM]

        entries = trending.get_trending("tv", "week", client=client)
        again = trending.get_trending("tv", "week", client=client)

        client.get_trending.assert_called_once_with("tv", "week", size=100)
        self.assertEqual(len(again), 1)
        entry = entries[0]
        self.assertEqual(entry.title, "King of the Hill")
        self.assertEqual(entry.year, 1997)
        self.assertEqual(entry.simkl_id, 3437)
        self.assertEqual(entry.simkl_url, "https://simkl.com/tv/3437/king-of-the-hill")
        self.assertEqual(entry.argus_url, reverse("tv-detail", kwargs={"external_id": "73141"}))
        self.assertEqual(entry.watchers, 24107)
        self.assertEqual(entry.simkl_rating, 8.1)
        self.assertEqual(entry.imdb_rating, 8.0)
        self.assertIn("wsrv.nl", entry.poster_url)

    def test_failures_are_cached_briefly_and_return_nothing(self):
        client = Mock()
        client.get_trending.side_effect = SimklError("cdn down")

        self.assertEqual(trending.get_trending("movies", "today", client=client), [])
        self.assertEqual(trending.get_trending("movies", "today", client=client), [])
        client.get_trending.assert_called_once()

    def test_invalid_inputs_return_nothing(self):
        self.assertEqual(trending.get_trending("books", "today"), [])
        self.assertEqual(trending.get_trending("tv", "year"), [])

    def test_argus_urls_prefer_tvdb_for_shows_and_tmdb_for_movies(self):
        self.assertEqual(
            trending.argus_url_for("show", {"tmdb": "1434"}),
            reverse("tv-detail", kwargs={"external_id": "1434"}) + "?provider=tmdb",
        )
        self.assertEqual(
            trending.argus_url_for("movie", {"tmdb": "27205", "imdb": "tt1"}),
            reverse("movie-detail", kwargs={"external_id": "27205"}),
        )
        self.assertIsNone(trending.argus_url_for("movie", {"imdb": "tt1"}))

    def test_user_state_annotation_and_hiding(self):
        cache.set("simkl:trending:tv:today", [ITEM, {**ITEM, "title": "Other", "ids": {"simkl_id": 2, "tvdb": "2"}}], 60)
        show = Show.objects.create(external_id="73141", tvdb_id="73141", name="King of the Hill")
        UserShow.objects.create(user=self.user, show=show)
        movie = Movie.objects.create(external_id="1", tmdb_id="1", title="M")
        UserMovie.objects.create(user=self.user, movie=movie, on_watchlist=True)

        annotated = trending.annotate_user_state(self.user, trending.get_trending("tv", "today"))
        self.assertEqual([entry.user_state for entry in annotated], ["tracked", None])

        visible = trending.trending_section(self.user, "tv", "today")
        self.assertEqual([entry.title for entry in visible], ["Other"])

        movie_entries = trending.annotate_user_state(
            self.user,
            [trending._trending_entry({**ITEM, "ids": {"simkl_id": 9, "tmdb": "1"}}, "movie")],
        )
        self.assertEqual(movie_entries[0].user_state, "watchlist")

    def test_calendar_premieres_and_airing_today(self):
        now = timezone.now()
        today = timezone.localdate()
        payload = {
            "calendar": [
                {"simkl_id": 3437, "date": (now + timedelta(days=3)).isoformat(), "finale_type": None, "episode": {"season": 1, "episode": 1, "title": "Pilot"}},
                {"simkl_id": 3437, "date": (now + timedelta(days=10)).isoformat(), "finale_type": None, "episode": {"season": 1, "episode": 2, "title": "Two"}},
                {"simkl_id": 99, "date": now.isoformat(), "finale_type": 2, "episode": {"season": 4, "episode": 8, "title": "Finale"}},
                {"simkl_id": 100, "date": (now + timedelta(days=90)).isoformat(), "finale_type": None, "episode": {"season": 1, "episode": 1}},
            ],
            "metadata": {
                "3437": ITEM,
                "99": {"title": "Old Show", "url": "/tv/99/old-show", "ids": {"simkl_id": 99, "tvdb": "99"}},
                "100": {"title": "Far", "url": "/tv/100/far", "ids": {"simkl_id": 100}},
            },
        }
        client = Mock()
        client.get_calendar.return_value = payload

        premieres = trending.premieres(days=30, client=client)
        self.assertEqual([entry.title for entry in premieres], ["King of the Hill"])
        self.assertEqual(premieres[0].episode_label, "S01E01")
        self.assertEqual(premieres[0].episode_title, "Pilot")

        airing = trending.airing_on(today, client=client)
        self.assertEqual([entry.title for entry in airing], ["Old Show"])
        self.assertTrue(airing[0].finale)
        self.assertEqual(airing[0].simkl_url, "https://simkl.com/tv/99/old-show")
        client.get_calendar.assert_called_once_with("tv")

    def test_movie_releases_window(self):
        now = timezone.now()
        client = Mock()
        client.get_calendar.return_value = {
            "calendar": [
                {"simkl_id": 1, "date": (now + timedelta(days=2)).isoformat()},
                {"simkl_id": 2, "date": (now + timedelta(days=60)).isoformat()},
            ],
            "metadata": {
                "1": {"title": "Soon", "url": "/movies/1/soon", "ids": {"simkl_id": 1, "tmdb": "1"}},
                "2": {"title": "Later", "url": "/movies/2/later", "ids": {"simkl_id": 2}},
            },
        }

        releases = trending.movie_releases(days=30, client=client)

        self.assertEqual([entry.title for entry in releases], ["Soon"])
        self.assertEqual(releases[0].argus_url, reverse("movie-detail", kwargs={"external_id": "1"}))
        self.assertIsNone(releases[0].episode_label)
        self.assertIsInstance(releases[0].date.date(), date)
