from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.mdblist import discovery
from apps.mdblist.enrichment import load_card, parse_media, refresh_media_batch, rating_url
from apps.mdblist.models import MdblistAccount, MdblistMediaInfo
from apps.movies.models import Movie, UserMovie


# Trimmed from a live ``GET /tmdb/movie/550/?append_to_response=recommendations``.
FIGHT_CLUB = {
    "title": "Fight Club",
    "year": 1999,
    "score": 78,
    "ids": {"imdb": "tt0137523", "trakt": 432, "tmdb": 550, "tvdb": 247, "mal": None, "mdblist": "a5as"},
    "type": "movie",
    "ratings": [
        {"source": "imdb", "value": 8.8, "score": 88, "votes": 2663967, "url": 138},
        {"source": "metacritic", "value": 67, "score": 67, "votes": 36, "url": "/fight-club"},
        {"source": "tomatoes", "value": 81, "score": 81, "votes": 251, "url": "/m/fight_club", "fresh": 1},
        {"source": "popcorn", "value": 96, "score": 96, "votes": 73863, "url": "/m/fight_club"},
        {"source": "letterboxd", "value": 4.3, "score": 86, "votes": 6080347, "url": "/film/fight-club/"},
        {"source": "myanimelist", "value": None, "score": None, "votes": None, "url": None},
    ],
    "streams": [{"id": 6, "name": "Hulu"}],
    "certification": "R",
    "trailer": "https://www.youtube.com/watch?v=dfeUzm6KF4g",
    "recommendations": [
        {
            "id": 1359,
            "mediatype": "movie",
            "ids": {"mdblist": "d2vy", "imdb": "tt0144084", "tmdb": 1359, "tvdb": 1182},
            "title": "American Psycho",
            "release_year": 2000,
            "poster": "https://image.tmdb.org/t/p/w200/x.jpg",
        }
    ],
}


class FakeCatalogClient:
    def __init__(self, detail=FIGHT_CLUB):
        self.detail = detail
        self.calls = []

    def get_media(self, provider, media_type, media_id, *, append=()):
        self.calls.append((provider, media_type, str(media_id), append))
        return self.detail

    def get_media_batch(self, provider, media_type, ids):
        self.calls.append(("batch", provider, media_type, list(ids)))
        return [self.detail]


class ParsingTests(TestCase):
    def test_ratings_without_a_value_are_dropped(self):
        data = parse_media(FIGHT_CLUB)

        self.assertEqual([rating["source"] for rating in data["ratings"]], ["imdb", "metacritic", "tomatoes", "popcorn", "letterboxd"])
        self.assertEqual(data["mdblist_id"], "a5as")
        self.assertEqual(data["slug"], "fight-club")
        self.assertEqual(data["streams"], ["Hulu"])
        self.assertEqual(data["recommendations"][0]["ids"]["tmdb"], "1359")

    def test_rating_links_point_at_the_source_site(self):
        self.assertEqual(rating_url("imdb", None, media_type="movie", imdb_id="tt1"), "https://www.imdb.com/title/tt1/")
        self.assertEqual(rating_url("tomatoes", "/m/fight_club", media_type="movie", imdb_id=None), "https://www.rottentomatoes.com/m/fight_club")
        self.assertEqual(rating_url("metacritic", "/ted-lasso", media_type="show", imdb_id=None), "https://www.metacritic.com/tv/ted-lasso")
        self.assertIsNone(rating_url("tmdb", None, media_type="movie", imdb_id=None))


@override_settings(MDBLIST_API_KEY="server-key")
class CardTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = get_user_model().objects.create_user("user@example.com", password="password")

    def tearDown(self):
        cache.clear()

    def test_untracked_titles_are_cached_not_stored(self):
        client = FakeCatalogClient()

        card = load_card("movie", "tmdb", "550", language="en", client=client)
        load_card("movie", "tmdb", "550", language="en", client=client)

        self.assertEqual(card["state"], "ok")
        self.assertEqual(card["score"], 78)
        self.assertEqual(card["url"], "https://mdblist.com/movie/a5as-fight-club")
        ratings = {rating.source: rating for rating in card["ratings"]}
        self.assertEqual(card["ratings"][0].label, "IMDb")
        self.assertEqual(ratings["imdb"].display, "8.8")
        self.assertEqual(ratings["tomatoes"].display, "81")
        self.assertEqual(ratings["letterboxd"].display, "4.3")
        self.assertEqual(card["recommendations"][0].argus_url, "/movies/1359/")
        self.assertEqual(len(client.calls), 1)
        self.assertFalse(MdblistMediaInfo.objects.exists())

    def test_tracked_titles_are_stored_and_batch_refreshed(self):
        movie = Movie.objects.create(external_id="550", tmdb_id="550", title="Fight Club", last_synced_at=timezone.now())
        UserMovie.objects.create(user=self.user, movie=movie, is_seen=True, seen_at=timezone.now())
        client = FakeCatalogClient()

        load_card("movie", "tmdb", "550", language="en", client=client)
        info = MdblistMediaInfo.objects.get()
        self.assertEqual(info.score, 78)
        self.assertEqual(len(info.recommendations), 1)

        refreshed = refresh_media_batch([movie], client=FakeCatalogClient({**FIGHT_CLUB, "score": 80, "recommendations": []}))
        info.refresh_from_db()
        self.assertEqual(refreshed, 1)
        self.assertEqual(info.score, 80)
        # Batch lookups carry no recommendations; the stored ones stay.
        self.assertEqual(len(info.recommendations), 1)

    @override_settings(MDBLIST_API_KEY="")
    def test_without_any_key_the_card_is_unavailable(self):
        self.assertEqual(load_card("movie", "tmdb", "550", language="en", user=self.user)["state"], "unavailable")

    @override_settings(MDBLIST_API_KEY="")
    def test_a_connected_account_stands_in_for_the_server_key(self):
        MdblistAccount.objects.create(user=self.user, access_token="user-key", auth_method=MdblistAccount.AuthMethod.API_KEY)

        with patch("apps.mdblist.client.MdblistClient.get_media", return_value=FIGHT_CLUB) as get_media:
            card = load_card("movie", "tmdb", "550", language="en", user=self.user)

        self.assertEqual(card["state"], "ok")
        self.assertTrue(get_media.called)


CHART = [
    {"rank": 1, "delta": 2, "title": "Tuner", "year": 2026, "poster": "p.jpg", "mediatype": "movie", "ids": {"mdblist": "3lis2", "imdb": "tt1", "tmdb": 1340206}, "score": 79},
    {"rank": 2, "delta": 0, "title": "Fight Club", "year": 1999, "mediatype": "movie", "ids": {"tmdb": 550}, "score": 78},
]


@override_settings(MDBLIST_API_KEY="server-key")
class DiscoveryTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = get_user_model().objects.create_user("user@example.com", password="password")

    def tearDown(self):
        cache.clear()

    def test_charts_are_cached_and_hide_what_the_viewer_watched(self):
        movie = Movie.objects.create(external_id="550", tmdb_id="550", title="Fight Club")
        UserMovie.objects.create(user=self.user, movie=movie, is_seen=True, seen_at=timezone.now())

        with patch("apps.mdblist.client.MdblistClient.get_streaming_chart", return_value=CHART) as chart:
            entries = discovery.streaming_chart(self.user, "movie", hide_seen=True)
            discovery.streaming_chart(self.user, "movie", hide_seen=False)

        self.assertEqual(chart.call_count, 1)
        self.assertEqual([entry.title for entry in entries], ["Tuner"])
        self.assertEqual(entries[0].argus_url, "/movies/1340206/")
        self.assertEqual(entries[0].mdblist_url, "https://mdblist.com/movie/3lis2-tuner")

    def test_recommendations_use_the_best_section_the_account_has(self):
        MdblistAccount.objects.create(user=self.user, access_token="user-key", auth_method=MdblistAccount.AuthMethod.API_KEY)
        items = {"movies": [], "shows": [{"id": 95350, "mediatype": "show", "title": "Lanterns", "ids": {"tmdb": 95350, "tvdb": 376098}, "rank": 1}]}

        with (
            patch("apps.mdblist.client.MdblistClient.get_recommendation_sections", return_value=[{"slug": "rising", "label": "Rising Fast"}]),
            patch("apps.mdblist.client.MdblistClient.get_recommendation_items", return_value=items) as get_items,
        ):
            section, entries = discovery.recommendations(self.user)

        self.assertEqual(get_items.call_args.args[0], "rising")
        self.assertEqual(section["label"], "Rising Fast")
        self.assertEqual(entries[0].argus_url, "/tv/376098/")
