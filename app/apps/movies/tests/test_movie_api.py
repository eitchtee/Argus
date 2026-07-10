from unittest.mock import patch

from cachalot.api import invalidate
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.catalog.models import Tier
from apps.movies.models import Movie, UserMovie


@override_settings(CACHALOT_ENABLED=False)
class MovieAPITests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = get_user_model().objects.create_user(
            email="user@example.com",
            password="password",
        )
        self.other_user = get_user_model().objects.create_user(
            email="other@example.com",
            password="password",
        )

    def test_list_requires_authentication(self):
        response = self.client.get("/api/movies/")

        self.assertEqual(response.status_code, 401)

    def test_list_returns_only_current_user_movie_state(self):
        user_movie = self._create_user_movie(
            self.user,
            title="Fight Club",
            external_id="550",
            on_watchlist=True,
        )
        self._create_user_movie(
            self.other_user,
            title="Alien",
            external_id="348",
            on_watchlist=True,
        )
        self.client.force_authenticate(self.user)

        response = self.client.get("/api/movies/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "results": [
                    self._expected_movie_payload(
                        user_movie,
                        on_watchlist=True,
                        is_seen=False,
                        tier=None,
                    )
                ]
            },
        )

    def test_list_filters_by_watchlist_seen_and_tier(self):
        watchlist_movie = self._create_user_movie(
            self.user,
            title="Watchlist",
            external_id="1",
            on_watchlist=True,
        )
        seen_movie = self._create_user_movie(
            self.user,
            title="Seen",
            external_id="2",
            is_seen=True,
            tier=Tier.S,
        )
        self.client.force_authenticate(self.user)

        watchlist_response = self.client.get("/api/movies/", {"watchlist": "true"})
        seen_response = self.client.get("/api/movies/", {"seen": "true"})
        tier_response = self.client.get("/api/movies/", {"tier": Tier.S})

        self.assertEqual(watchlist_response.status_code, 200)
        self.assertEqual(
            watchlist_response.json()["results"],
            [self._expected_movie_payload(watchlist_movie, on_watchlist=True)],
        )
        self.assertEqual(seen_response.status_code, 200)
        self.assertEqual(
            seen_response.json()["results"],
            [
                self._expected_movie_payload(
                    seen_movie,
                    is_seen=True,
                    tier=Tier.S,
                )
            ],
        )
        self.assertEqual(tier_response.status_code, 200)
        self.assertEqual(
            tier_response.json()["results"],
            [
                self._expected_movie_payload(
                    seen_movie,
                    is_seen=True,
                    tier=Tier.S,
                )
            ],
        )

    @patch("apps.movies.api.track_movie")
    def test_track_movie_returns_tracked_state(self, track_movie):
        user_movie = self._create_user_movie(
            self.user,
            title="Fight Club",
            external_id="550",
            on_watchlist=True,
        )
        track_movie.return_value = user_movie
        self.client.force_authenticate(self.user)

        response = self.client.post(
            "/api/movies/track",
            {"provider": "tmdb", "external_id": "550"},
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(
            response.json(),
            self._expected_movie_payload(user_movie, on_watchlist=True),
        )
        track_movie.assert_called_once_with(self.user, "tmdb", "550")

    def test_mark_seen_scopes_state_to_current_user(self):
        shared_movie = self._create_movie(title="Fight Club", external_id="550")
        UserMovie.objects.create(
            user=self.other_user,
            movie=shared_movie,
            on_watchlist=True,
        )
        self.client.force_authenticate(self.user)

        response = self.client.post(f"/api/movies/{shared_movie.id}/seen")

        self.assertEqual(response.status_code, 200)
        invalidate(UserMovie)
        user_movie = UserMovie.objects.get(user=self.user, movie=shared_movie)
        other_user_movie = UserMovie.objects.get(user=self.other_user, movie=shared_movie)
        self.assertTrue(user_movie.is_seen)
        self.assertFalse(user_movie.on_watchlist)
        self.assertTrue(other_user_movie.on_watchlist)
        self.assertFalse(other_user_movie.is_seen)

    def test_unmark_seen_clears_tier(self):
        user_movie = self._create_user_movie(
            self.user,
            title="Fight Club",
            external_id="550",
            is_seen=True,
            tier=Tier.S,
        )
        self.client.force_authenticate(self.user)

        response = self.client.delete(f"/api/movies/{user_movie.movie_id}/seen")

        self.assertEqual(response.status_code, 200)
        user_movie.refresh_from_db()
        self.assertFalse(user_movie.is_seen)
        self.assertIsNone(user_movie.tier)

    def test_set_tier_rejects_unseen_movie(self):
        user_movie = self._create_user_movie(
            self.user,
            title="Fight Club",
            external_id="550",
        )
        self.client.force_authenticate(self.user)

        response = self.client.put(
            f"/api/movies/{user_movie.movie_id}/tier",
            {"tier": Tier.S},
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"], "Cannot tier an unseen movie.")

    def test_set_and_clear_tier(self):
        user_movie = self._create_user_movie(
            self.user,
            title="Fight Club",
            external_id="550",
            is_seen=True,
        )
        self.client.force_authenticate(self.user)

        set_response = self.client.put(
            f"/api/movies/{user_movie.movie_id}/tier",
            {"tier": Tier.S},
            format="json",
        )
        clear_response = self.client.delete(f"/api/movies/{user_movie.movie_id}/tier")

        self.assertEqual(set_response.status_code, 200)
        self.assertEqual(set_response.json()["tier"], Tier.S)
        self.assertEqual(clear_response.status_code, 200)
        self.assertIsNone(clear_response.json()["tier"])

    def test_remove_from_watchlist_deletes_empty_user_state(self):
        user_movie = self._create_user_movie(
            self.user,
            title="Fight Club",
            external_id="550",
            on_watchlist=True,
        )
        self.client.force_authenticate(self.user)

        response = self.client.delete(f"/api/movies/{user_movie.movie_id}/watchlist")

        self.assertEqual(response.status_code, 204)
        self.assertFalse(
            UserMovie.objects.filter(user=self.user, movie=user_movie.movie).exists()
        )

    @override_settings(DEMO=True)
    def test_demo_mode_rejects_write_for_non_superuser(self):
        movie = self._create_movie(title="Fight Club", external_id="550")
        self.client.force_authenticate(self.user)

        response = self.client.post(f"/api/movies/{movie.id}/seen")

        self.assertEqual(response.status_code, 403)

    def _create_movie(self, *, title, external_id):
        return Movie.objects.create(
            provider="tmdb",
            external_id=external_id,
            title=title,
            original_title=title,
        )

    def _create_user_movie(self, user, *, title, external_id, **state):
        movie = self._create_movie(title=title, external_id=external_id)
        return UserMovie.objects.create(user=user, movie=movie, **state)

    def _expected_movie_payload(self, user_movie, **overrides):
        movie = user_movie.movie
        return {
            "id": movie.id,
            "provider": movie.provider,
            "external_id": movie.external_id,
            "title": movie.title,
            "poster_path": movie.poster_path,
            "release_date": None,
            "on_watchlist": overrides.get("on_watchlist", user_movie.on_watchlist),
            "is_seen": overrides.get("is_seen", user_movie.is_seen),
            "seen_at": None,
            "tier": overrides.get("tier", user_movie.tier),
        }
