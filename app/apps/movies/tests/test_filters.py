from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.http import QueryDict
from django.test import TestCase

from apps.catalog.models import Genre
from apps.catalog.ratings import rate_media
from apps.movies.filters import filter_watched, filter_watchlist
from apps.movies.models import Movie, UserMovie


class MovieFilterTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("user@example.com")
        self.horror = Genre.objects.create(provider="tmdb", external_id="27", name="Horror")

    def make_movie(self, title, *, status=Movie.NormalizedStatus.RELEASED, genres=(), release=None):
        movie = Movie.objects.create(
            title=title, external_id=title, normalized_status=status, release_date=release
        )
        movie.genres.set(genres)
        return movie

    def titles(self, context):
        return [movie.title for movie in context["movies"]]

    def test_watchlist_release_tab_filters_and_counts(self):
        movies = [
            self.make_movie("Out"),
            self.make_movie("Soon", status=Movie.NormalizedStatus.UPCOMING),
            self.make_movie("Unknown", status=Movie.NormalizedStatus.UNKNOWN),
        ]

        context = filter_watchlist(self.user, movies, QueryDict("release=Upcoming"))

        self.assertEqual(self.titles(context), ["Soon"])
        self.assertEqual(
            [(value, count) for value, _label, count in context["releases"]],
            [("all", 3), ("Released", 1), ("Upcoming", 1)],
        )

    def test_watchlist_keeps_added_order_by_default_and_filters_by_genre(self):
        movies = [
            self.make_movie("Newest", genres=[self.horror]),
            self.make_movie("Middle"),
            self.make_movie("Oldest", genres=[self.horror]),
        ]

        context = filter_watchlist(self.user, movies, QueryDict("genre=Horror"))

        self.assertEqual(self.titles(context), ["Newest", "Oldest"])
        self.assertEqual(context["genres"], ["Horror"])

    def test_watched_release_sort_puts_undated_movies_last(self):
        movies = [
            self.make_movie("Undated"),
            self.make_movie("Old", release=date(1999, 1, 1)),
            self.make_movie("New", release=date(2024, 1, 1)),
        ]

        context = filter_watched(self.user, movies, QueryDict("sort=release"))

        self.assertEqual(self.titles(context), ["New", "Old", "Undated"])

    def test_watched_rating_sort_puts_unrated_movies_last(self):
        unrated = self.make_movie("Unrated")
        low = self.make_movie("Low")
        high = self.make_movie("High")
        for movie in (low, high):
            UserMovie.objects.create(user=self.user, movie=movie, is_seen=True)
        rate_media(self.user, "movie", low, Decimal("2.0"))
        rate_media(self.user, "movie", high, Decimal("4.5"))

        context = filter_watched(self.user, [unrated, low, high], QueryDict("sort=rating"))

        self.assertEqual(self.titles(context), ["High", "Low", "Unrated"])

    def test_watched_rating_filter_matches_threshold_and_unrated(self):
        unrated = self.make_movie("Unrated")
        low = self.make_movie("Low")
        high = self.make_movie("High")
        for movie, score in ((low, "2.5"), (high, "4.0")):
            UserMovie.objects.create(user=self.user, movie=movie, is_seen=True)
            rate_media(self.user, "movie", movie, Decimal(score))
        movies = [unrated, low, high]

        self.assertEqual(
            self.titles(filter_watched(self.user, movies, QueryDict("rating=4"))), ["High"]
        )
        self.assertEqual(
            self.titles(filter_watched(self.user, movies, QueryDict("rating=rated"))),
            ["Low", "High"],
        )
        self.assertEqual(
            self.titles(filter_watched(self.user, movies, QueryDict("rating=unrated"))),
            ["Unrated"],
        )
