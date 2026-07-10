from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.catalog.models import Tier
from apps.movies.models import Movie, UserMovie
from apps.movies.services import (
    clear_tier,
    mark_seen,
    remove_from_watchlist,
    set_tier,
    track_movie,
    unmark_seen,
)


class MovieServiceTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("user@example.com")

    def test_track_movie_imports_movie_and_adds_to_watchlist(self):
        movie = Movie.objects.create(external_id="550", title="Fight Club")
        import_calls = []

        def import_func(provider, external_id):
            import_calls.append((provider, external_id))
            return movie

        user_movie = track_movie(self.user, "tmdb", "550", import_func=import_func)

        self.assertEqual(import_calls, [("tmdb", "550")])
        self.assertEqual(user_movie.user, self.user)
        self.assertEqual(user_movie.movie, movie)
        self.assertTrue(user_movie.on_watchlist)
        self.assertIsNotNone(user_movie.watchlist_added_at)

    def test_track_movie_reuses_existing_user_movie_row(self):
        movie = Movie.objects.create(external_id="550", title="Fight Club")
        existing = UserMovie.objects.create(user=self.user, movie=movie, on_watchlist=False)

        user_movie = track_movie(
            self.user,
            "tmdb",
            "550",
            import_func=lambda provider, external_id: movie,
        )

        self.assertEqual(user_movie.id, existing.id)
        self.assertTrue(user_movie.on_watchlist)

    def test_remove_from_watchlist_deletes_empty_user_movie_row(self):
        movie = Movie.objects.create(external_id="550", title="Fight Club")
        UserMovie.objects.create(user=self.user, movie=movie, on_watchlist=True)

        remove_from_watchlist(self.user, movie)

        self.assertFalse(UserMovie.objects.filter(user=self.user, movie=movie).exists())

    def test_remove_from_watchlist_keeps_row_when_seen_state_remains(self):
        movie = Movie.objects.create(external_id="550", title="Fight Club")
        UserMovie.objects.create(
            user=self.user,
            movie=movie,
            on_watchlist=True,
            is_seen=True,
        )

        user_movie = remove_from_watchlist(self.user, movie)

        self.assertFalse(user_movie.on_watchlist)
        self.assertTrue(user_movie.is_seen)
        self.assertTrue(UserMovie.objects.filter(user=self.user, movie=movie).exists())

    def test_mark_seen_sets_seen_state_and_removes_from_watchlist(self):
        movie = Movie.objects.create(external_id="550", title="Fight Club")
        UserMovie.objects.create(user=self.user, movie=movie, on_watchlist=True)

        user_movie = mark_seen(self.user, movie)

        self.assertTrue(user_movie.is_seen)
        self.assertIsNotNone(user_movie.seen_at)
        self.assertFalse(user_movie.on_watchlist)
        self.assertIsNone(user_movie.watchlist_added_at)

    def test_unmark_seen_clears_seen_at_and_tier(self):
        movie = Movie.objects.create(external_id="550", title="Fight Club")
        UserMovie.objects.create(
            user=self.user,
            movie=movie,
            is_seen=True,
            tier=Tier.S,
        )

        user_movie = unmark_seen(self.user, movie)

        self.assertFalse(user_movie.is_seen)
        self.assertIsNone(user_movie.seen_at)
        self.assertIsNone(user_movie.tier)

    def test_set_tier_requires_seen_movie(self):
        movie = Movie.objects.create(external_id="550", title="Fight Club")
        UserMovie.objects.create(user=self.user, movie=movie, is_seen=False)

        with self.assertRaisesMessage(ValueError, "Cannot tier an unseen movie"):
            set_tier(self.user, movie, Tier.S)

    def test_set_tier_updates_seen_movie(self):
        movie = Movie.objects.create(external_id="550", title="Fight Club")
        UserMovie.objects.create(user=self.user, movie=movie, is_seen=True)

        user_movie = set_tier(self.user, movie, Tier.A)

        self.assertEqual(user_movie.tier, Tier.A)

    def test_clear_tier_sets_tier_to_none(self):
        movie = Movie.objects.create(external_id="550", title="Fight Club")
        UserMovie.objects.create(user=self.user, movie=movie, is_seen=True, tier=Tier.B)

        user_movie = clear_tier(self.user, movie)

        self.assertIsNone(user_movie.tier)
