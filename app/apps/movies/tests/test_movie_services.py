from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from apps.catalog.models import Tier
from apps.movies.models import Movie, UserMovie
from apps.movies.services import (
    clear_tier,
    get_watched_movies,
    get_watchlist_movies,
    mark_seen,
    remove_from_watchlist,
    set_tier,
    track_movie,
    unmark_seen,
)


class MovieServiceTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("user@example.com")

    def test_get_watchlist_movies_scopes_unwatched_entries_to_user(self):
        older = Movie.objects.create(external_id="1", title="Older")
        newer = Movie.objects.create(external_id="2", title="Newer")
        seen = Movie.objects.create(external_id="3", title="Seen")
        untracked = Movie.objects.create(external_id="4", title="Untracked")
        other = Movie.objects.create(external_id="5", title="Other user")
        now = timezone.now()

        UserMovie.objects.create(
            user=self.user,
            movie=older,
            on_watchlist=True,
            watchlist_added_at=now - timedelta(days=1),
        )
        UserMovie.objects.create(
            user=self.user,
            movie=newer,
            on_watchlist=True,
            watchlist_added_at=now,
        )
        UserMovie.objects.create(
            user=self.user,
            movie=seen,
            on_watchlist=True,
            is_seen=True,
            watchlist_added_at=now,
        )
        UserMovie.objects.create(user=self.user, movie=untracked, on_watchlist=False)
        UserMovie.objects.create(
            user=get_user_model().objects.create_user("other@example.com"),
            movie=other,
            on_watchlist=True,
            watchlist_added_at=now + timedelta(days=1),
        )

        self.assertEqual(get_watchlist_movies(self.user), [newer, older])

    def test_get_watchlist_movies_uses_deterministic_fallback_ordering(self):
        alpha = Movie.objects.create(external_id="2", title="Alpha")
        beta = Movie.objects.create(external_id="1", title="Beta")
        untimed = Movie.objects.create(external_id="3", title="Untimed")

        UserMovie.objects.create(user=self.user, movie=beta, on_watchlist=True)
        UserMovie.objects.create(user=self.user, movie=untimed, on_watchlist=True)
        UserMovie.objects.create(user=self.user, movie=alpha, on_watchlist=True)

        self.assertEqual(get_watchlist_movies(self.user), [alpha, beta, untimed])

    def test_get_watched_movies_scopes_seen_entries_to_user(self):
        older = Movie.objects.create(external_id="1", title="Older")
        newer = Movie.objects.create(external_id="2", title="Newer")
        unwatched = Movie.objects.create(external_id="3", title="Unwatched")
        other = Movie.objects.create(external_id="4", title="Other user")
        now = timezone.now()

        UserMovie.objects.create(
            user=self.user,
            movie=older,
            is_seen=True,
            seen_at=now - timedelta(days=1),
        )
        UserMovie.objects.create(
            user=self.user,
            movie=newer,
            is_seen=True,
            seen_at=now,
        )
        UserMovie.objects.create(
            user=self.user,
            movie=unwatched,
            on_watchlist=True,
            is_seen=False,
        )
        UserMovie.objects.create(
            user=get_user_model().objects.create_user("other@example.com"),
            movie=other,
            is_seen=True,
            seen_at=now + timedelta(days=1),
        )

        self.assertEqual(get_watched_movies(self.user), [newer, older])

    def test_get_watched_movies_uses_deterministic_fallback_ordering(self):
        alpha = Movie.objects.create(external_id="2", title="Alpha")
        beta = Movie.objects.create(external_id="1", title="Beta")
        untimed = Movie.objects.create(external_id="3", title="Untimed")

        UserMovie.objects.create(user=self.user, movie=beta, is_seen=True)
        UserMovie.objects.create(user=self.user, movie=untimed, is_seen=True)
        UserMovie.objects.create(user=self.user, movie=alpha, is_seen=True)

        self.assertEqual(get_watched_movies(self.user), [alpha, beta, untimed])

    def test_track_movie_imports_movie_and_adds_to_watchlist(self):
        movie = Movie.objects.create(external_id="550", title="Fight Club")
        self.user.settings.tmdb_metadata_language = "pt-BR"
        self.user.settings.save()
        import_calls = []
        hydration_calls = []

        def import_func(provider, external_id, *, language):
            import_calls.append((provider, external_id, language))
            return movie

        user_movie = track_movie(
            self.user,
            "tmdb",
            "550",
            import_func=import_func,
            hydrate_func=hydration_calls.append,
        )

        self.assertEqual(import_calls, [("tmdb", "550", "pt-BR")])
        self.assertEqual(hydration_calls, [movie.id])
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
            import_func=lambda provider, external_id, *, language: movie,
            hydrate_func=lambda _movie_id: None,
        )

        self.assertEqual(user_movie.id, existing.id)
        self.assertTrue(user_movie.on_watchlist)

    def test_track_movie_uses_the_selected_provider_language(self):
        movie = Movie.objects.create(provider="tvdb", external_id="42", title="A Movie")
        self.user.settings.tvdb_metadata_language = "por"
        self.user.settings.save()
        import_calls = []

        def import_func(provider, external_id, *, language):
            import_calls.append((provider, external_id, language))
            return movie

        track_movie(
            self.user,
            "tvdb",
            "42",
            import_func=import_func,
            hydrate_func=lambda _movie_id: None,
        )

        self.assertEqual(import_calls, [("tvdb", "42", "por")])

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
