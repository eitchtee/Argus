from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.movies.models import Movie, UserMovie
from apps.movies.services import get_watch_something


class GetWatchSomethingServiceTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("user@example.com")

    def _make_watchlist_movie(self, external_id, on_watchlist=True, is_seen=False):
        movie = Movie.objects.create(provider="tmdb", external_id=external_id, title=f"Movie {external_id}")
        UserMovie.objects.create(user=self.user, movie=movie, on_watchlist=on_watchlist, is_seen=is_seen)
        return movie

    def test_returns_empty_list_when_no_watchlist_movies(self):
        self.assertEqual(get_watch_something(self.user), [])

    def test_excludes_seen_movies(self):
        self._make_watchlist_movie("1", on_watchlist=True, is_seen=True)

        self.assertEqual(get_watch_something(self.user), [])

    def test_excludes_movies_not_on_watchlist(self):
        self._make_watchlist_movie("1", on_watchlist=False, is_seen=False)

        self.assertEqual(get_watch_something(self.user), [])

    def test_excludes_other_users_movies(self):
        other_user = get_user_model().objects.create_user("other@example.com")
        movie = Movie.objects.create(provider="tmdb", external_id="1", title="Movie 1")
        UserMovie.objects.create(user=other_user, movie=movie, on_watchlist=True)

        self.assertEqual(get_watch_something(self.user), [])

    def test_returns_at_most_count(self):
        for i in range(15):
            self._make_watchlist_movie(str(i))

        self.assertEqual(len(get_watch_something(self.user, count=10)), 10)

    def test_returns_all_eligible_when_fewer_than_count(self):
        movie = self._make_watchlist_movie("1")

        self.assertEqual(get_watch_something(self.user, count=10), [movie])
