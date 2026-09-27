from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.catalog.ratings import clear_rating, rate_media
from apps.mdblist.changes import mdblist_rating_from_score
from apps.mdblist.models import MdblistAccount, MdblistSyncIntent
from apps.movies.models import Movie
from apps.movies.services import mark_seen


class IntentRecordingTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("user@example.com", password="password")
        self.movie = Movie.objects.create(external_id="550", tmdb_id="550", imdb_id="tt0137523", title="Fight Club")

    def test_nothing_is_recorded_without_an_account(self):
        mark_seen(self.user, self.movie)

        self.assertFalse(MdblistSyncIntent.objects.exists())

    def test_library_and_rating_changes_are_recorded(self):
        MdblistAccount.objects.create(user=self.user, access_token="k")

        mark_seen(self.user, self.movie)
        rate_media(self.user, "movie", self.movie, Decimal("4.5"))

        kinds = dict(MdblistSyncIntent.objects.values_list("kind", "desired"))
        self.assertEqual(
            kinds,
            {"movie_history": True, "movie_watchlist": False, "movie_rating": True},
        )
        rating = MdblistSyncIntent.objects.get(kind="movie_rating")
        self.assertEqual(rating.identity_key, "tmdb:550")
        self.assertEqual(rating.payload["rating"], 9)

        clear_rating(self.user, self.movie)
        self.assertFalse(MdblistSyncIntent.objects.get(kind="movie_rating").desired)

    def test_half_stars_map_onto_ten_points(self):
        self.assertEqual(mdblist_rating_from_score(Decimal("0.5")), 1)
        self.assertEqual(mdblist_rating_from_score(Decimal("3.5")), 7)
        self.assertEqual(mdblist_rating_from_score(Decimal("5")), 10)
