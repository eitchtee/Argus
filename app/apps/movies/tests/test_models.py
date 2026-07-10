from django.contrib.auth import get_user_model
from django.db import IntegrityError
from django.test import TestCase

from apps.catalog.models import Genre, Tier
from apps.movies.models import Movie, UserMovie


class MovieModelTests(TestCase):
    def test_movie_provider_external_id_is_unique(self):
        Movie.objects.create(external_id="550", title="Fight Club")

        with self.assertRaises(IntegrityError):
            Movie.objects.create(external_id="550", title="Fight Club duplicate")

    def test_movie_allows_genres_and_exposes_tmdb_id(self):
        genre = Genre.objects.create(provider="tmdb", external_id="18", name="Drama")
        movie = Movie.objects.create(
            external_id="550",
            imdb_id="tt0137523",
            title="Fight Club",
            original_title="Fight Club",
        )

        movie.genres.add(genre)

        self.assertEqual(movie.provider, "tmdb")
        self.assertEqual(movie.tmdb_id, "550")
        self.assertEqual(list(movie.genres.all()), [genre])
        self.assertEqual(str(movie), "Fight Club")

    def test_user_movie_is_unique_per_user_movie(self):
        user = get_user_model().objects.create_user("user@example.com")
        movie = Movie.objects.create(external_id="550", title="Fight Club")
        UserMovie.objects.create(user=user, movie=movie)

        with self.assertRaises(IntegrityError):
            UserMovie.objects.create(user=user, movie=movie)

    def test_user_movie_defaults_match_watch_state_semantics(self):
        user = get_user_model().objects.create_user("user@example.com")
        movie = Movie.objects.create(external_id="550", title="Fight Club")

        user_movie = UserMovie.objects.create(user=user, movie=movie)

        self.assertFalse(user_movie.on_watchlist)
        self.assertIsNone(user_movie.watchlist_added_at)
        self.assertFalse(user_movie.is_seen)
        self.assertIsNone(user_movie.seen_at)
        self.assertIsNone(user_movie.tier)

    def test_user_movie_tier_uses_shared_tier_choices(self):
        field = UserMovie._meta.get_field("tier")

        self.assertEqual([choice[0] for choice in field.choices], Tier.values)

    def test_movie_poster_url_builds_full_tmdb_url(self):
        from django.test import override_settings

        movie = Movie.objects.create(
            external_id="550",
            title="Fight Club",
            poster_path="/abc.jpg",
        )

        with override_settings(TMDB_IMAGE_BASE_URL="https://image.tmdb.org/t/p/"):
            self.assertEqual(movie.poster_url, "https://image.tmdb.org/t/p/w342/abc.jpg")

    def test_movie_poster_url_is_none_without_poster_path(self):
        movie = Movie.objects.create(external_id="550", title="Fight Club")

        self.assertIsNone(movie.poster_url)

    def test_movie_backdrop_url_builds_full_tmdb_url(self):
        from django.test import override_settings

        movie = Movie.objects.create(
            external_id="550",
            title="Fight Club",
            backdrop_path="/backdrop.jpg",
        )

        with override_settings(TMDB_IMAGE_BASE_URL="https://image.tmdb.org/t/p/"):
            self.assertEqual(
                movie.backdrop_url, "https://image.tmdb.org/t/p/w1280/backdrop.jpg"
            )

    def test_movie_backdrop_url_is_none_without_backdrop_path(self):
        movie = Movie.objects.create(external_id="550", title="Fight Club")

        self.assertIsNone(movie.backdrop_url)

    def test_movie_cast_defaults_to_empty_list(self):
        movie = Movie.objects.create(external_id="550", title="Fight Club")

        self.assertEqual(movie.cast, [])
