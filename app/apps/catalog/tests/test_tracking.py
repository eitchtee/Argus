from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.catalog.providers.base import SearchResultDTO
from apps.catalog.tracking import (
    find_tracking_match,
    tracking_matches,
)
from apps.movies.models import Movie, UserMovie
from apps.tv.models import Show, UserShow


class TrackingMatchTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            "user@example.com",
            password="password",
        )
        self.other_user = get_user_model().objects.create_user(
            "other@example.com",
            password="password",
        )

    def test_matches_movie_on_other_provider_by_alternate_id(self):
        movie = Movie.objects.create(
            provider="tmdb",
            external_id="550",
            tvdb_id="42",
            title="Fight Club",
        )
        UserMovie.objects.create(user=self.user, movie=movie, on_watchlist=True)

        match = find_tracking_match(
            self.user,
            "movie",
            provider="tvdb",
            external_id="42",
        )

        self.assertEqual((match.provider, match.external_id), ("tmdb", "550"))
        self.assertFalse(match.same_provider)

    def test_matches_show_on_other_provider_by_imdb_id(self):
        show = Show.objects.create(
            provider="tmdb",
            external_id="1399",
            imdb_id="tt0944947",
            name="Game of Thrones",
        )
        UserShow.objects.create(user=self.user, show=show)

        match = find_tracking_match(
            self.user,
            "tv",
            provider="tvdb",
            external_id="121361",
            imdb_id="tt0944947",
        )

        self.assertEqual((match.provider, match.external_id), ("tmdb", "1399"))
        self.assertFalse(match.same_provider)

    def test_exact_provider_match_has_priority(self):
        movie = Movie.objects.create(
            provider="tmdb",
            external_id="550",
            tvdb_id="42",
            title="Fight Club",
        )
        UserMovie.objects.create(user=self.user, movie=movie)

        match = find_tracking_match(
            self.user,
            "movie",
            provider="tmdb",
            external_id="550",
            tvdb_id="42",
        )

        self.assertTrue(match.same_provider)
        self.assertEqual((match.provider, match.external_id), ("tmdb", "550"))

    def test_ignores_another_users_state(self):
        movie = Movie.objects.create(
            provider="tmdb",
            external_id="550",
            tvdb_id="42",
            title="Fight Club",
        )
        UserMovie.objects.create(user=self.other_user, movie=movie)

        match = find_tracking_match(
            self.user,
            "movie",
            provider="tvdb",
            external_id="42",
        )

        self.assertIsNone(match)

    def test_batch_returns_exact_and_other_provider_matches(self):
        exact_movie = Movie.objects.create(
            provider="tmdb",
            external_id="550",
            title="Fight Club",
        )
        alternate_movie = Movie.objects.create(
            provider="tvdb",
            external_id="42",
            tmdb_id="603",
            title="The Matrix",
        )
        UserMovie.objects.create(user=self.user, movie=exact_movie)
        UserMovie.objects.create(user=self.user, movie=alternate_movie)

        results = [
            SearchResultDTO("tmdb", "550", "Fight Club", 1999, None, ""),
            SearchResultDTO("tmdb", "603", "The Matrix", 1999, None, ""),
            SearchResultDTO("tmdb", "999", "Unknown", None, None, ""),
        ]

        matches = tracking_matches(self.user, "movie", results)

        self.assertTrue(matches[("tmdb", "550")].same_provider)
        self.assertFalse(matches[("tmdb", "603")].same_provider)
        self.assertIsNone(matches[("tmdb", "999")])
