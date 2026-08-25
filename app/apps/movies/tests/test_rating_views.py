from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.catalog.models import MediaRating
from apps.catalog.ratings import build_rating_url
from apps.movies.models import Movie, UserMovie


@override_settings(
    STORAGES={
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {
            "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"
        },
    },
    DJANGO_VITE_DEV_MODE=True,
)
class MovieRatingViewTests(TestCase):
    def setUp(self):
        from django_vite.core.asset_loader import DjangoViteAssetLoader

        DjangoViteAssetLoader._instance = None
        self.user = get_user_model().objects.create_user(
            "user@example.com", password="password"
        )
        self.client.login(username="user@example.com", password="password")
        self.movie = Movie.objects.create(external_id="550", title="Fight Club")
        self.rating_url = reverse(
            "media-rating", kwargs={"media_type": "movie", "external_id": "550"}
        )

    def tearDown(self):
        from django_vite.core.asset_loader import DjangoViteAssetLoader

        DjangoViteAssetLoader._instance = None

    def _mark_seen(self):
        UserMovie.objects.create(user=self.user, movie=self.movie, is_seen=True)

    def test_requires_authentication(self):
        self.client.logout()

        response = self.client.post(
            self.rating_url, {"score": "4.5"}, HTTP_HX_REQUEST="true"
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("HX-Redirect", response.headers)

    def test_requires_htmx_header(self):
        response = self.client.post(self.rating_url, {"score": "4.5"})

        self.assertEqual(response.status_code, 403)

    def test_get_is_not_allowed(self):
        response = self.client.get(self.rating_url, HTTP_HX_REQUEST="true")

        self.assertEqual(response.status_code, 405)

    def test_rate_persists_and_renders_checked_state(self):
        self._mark_seen()

        response = self.client.post(
            self.rating_url, {"score": "3.5"}, HTTP_HX_REQUEST="true"
        )

        self.assertEqual(response.status_code, 200)
        rating = MediaRating.objects.get(user=self.user)
        self.assertEqual(rating.score, Decimal("3.5"))
        content = response.content.decode()
        self.assertIn('name="score"', content)
        self.assertIn('value="3.5"', content)
        self.assertIn("checked", content)
        self.assertIn('data-score="3.5"', content)

    def test_editing_rating_updates_the_same_row(self):
        self._mark_seen()
        self.client.post(self.rating_url, {"score": "2.0"}, HTTP_HX_REQUEST="true")

        self.client.post(self.rating_url, {"score": "4.5"}, HTTP_HX_REQUEST="true")

        self.assertEqual(MediaRating.objects.count(), 1)
        self.assertEqual(
            MediaRating.objects.get(user=self.user).score, Decimal("4.5")
        )

    def test_saved_rating_survives_reload_in_decimal_comma_locales(self):
        import re

        self._mark_seen()
        MediaRating.objects.create(
            user=self.user,
            media_type="movie",
            content_type=ContentType.objects.get_for_model(Movie),
            object_id=self.movie.pk,
            score=Decimal("4.5"),
        )

        response = self.client.get(
            reverse("movie-detail-content", kwargs={"external_id": "550"}),
            HTTP_HX_REQUEST="true",
            HTTP_ACCEPT_LANGUAGE="pt-PT,pt;q=0.9",
        )

        content = response.content.decode()
        self.assertIn('data-score="4.5"', content)
        self.assertNotIn('data-score="4,', content)
        self.assertRegex(content, r'name="score" value="4\.5"[^>]*checked')

    def test_unwatched_movie_cannot_be_rated(self):
        response = self.client.post(
            self.rating_url, {"score": "3.5"}, HTTP_HX_REQUEST="true"
        )

        self.assertEqual(response.status_code, 400)
        self.assertFalse(MediaRating.objects.exists())

    def test_invalid_score_is_rejected(self):
        self._mark_seen()

        for score in ("6", "4.3", "abc"):
            response = self.client.post(
                self.rating_url, {"score": score}, HTTP_HX_REQUEST="true"
            )
            self.assertEqual(response.status_code, 400)

        self.assertFalse(MediaRating.objects.exists())

    def test_minimum_half_star_rating_is_accepted(self):
        self._mark_seen()

        response = self.client.post(
            self.rating_url, {"score": "0.5"}, HTTP_HX_REQUEST="true"
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            MediaRating.objects.get(user=self.user).score, Decimal("0.5")
        )
        content = response.content.decode()
        self.assertIn('data-score="0.5"', content)
        self.assertIn('value="0.5"', content)

    def test_unknown_movie_returns_404(self):
        url = reverse(
            "media-rating", kwargs={"media_type": "movie", "external_id": "nope"}
        )
        response = self.client.post(url, {"score": "4"}, HTTP_HX_REQUEST="true")

        self.assertEqual(response.status_code, 404)

    def test_clearing_the_rating_removes_it(self):
        self._mark_seen()
        self.client.post(self.rating_url, {"score": "4.5"}, HTTP_HX_REQUEST="true")
        self.assertEqual(MediaRating.objects.count(), 1)

        response = self.client.post(
            self.rating_url, {"score": ""}, HTTP_HX_REQUEST="true"
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(MediaRating.objects.exists())
        content = response.content.decode()
        self.assertIn('data-score=""', content)
        self.assertIn('value=""', content)
        self.assertNotRegex(content, r'name="score" value="[1-9.]+[^>]*checked')

    def test_clear_when_no_rating_exists_is_a_noop(self):
        self._mark_seen()

        response = self.client.post(
            self.rating_url, {"score": ""}, HTTP_HX_REQUEST="true"
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(MediaRating.objects.exists())

    def test_detail_fragment_shows_rating_when_watched(self):
        self._mark_seen()
        from apps.movies.views import _build_movie_context

        movie_context = _build_movie_context(self.user, "550")
        self.assertTrue(movie_context["is_seen"])
        self.assertIsNotNone(movie_context["rating_url"])

        response = self.client.get(
            reverse("movie-detail-content", kwargs={"external_id": "550"}),
            HTTP_HX_REQUEST="true",
        )

        self.assertContains(response, 'id="movie-rating"')
        self.assertContains(response, 'class="media-rating"')

    def test_marking_watched_delivers_oob_rating_slot(self):
        response = self.client.post(
            reverse("movie-detail-watched", kwargs={"external_id": "550"}),
            HTTP_HX_REQUEST="true",
        )

        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn('id="movie-rating"', content)
        self.assertIn("hx-swap-oob", content)
        self.assertIn('class="media-rating"', content)

    def test_unmarking_watched_clears_oob_rating_slot(self):
        self._mark_seen()

        response = self.client.delete(
            reverse("movie-detail-watched", kwargs={"external_id": "550"}),
            HTTP_HX_REQUEST="true",
        )

        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn('id="movie-rating"', content)
        self.assertIn("hx-swap-oob", content)
        self.assertNotIn('class="media-rating"', content)


class BuildRatingUrlTests(TestCase):
    def test_default_provider_omits_query_param(self):
        self.assertEqual(
            build_rating_url("movie", "550", "tmdb"), "/media/movie/550/rating/"
        )

    def test_non_default_provider_appends_query_param(self):
        self.assertEqual(
            build_rating_url("movie", "123", "tvdb"),
            "/media/movie/123/rating/?provider=tvdb",
        )

    def test_episode_urls_never_carry_provider(self):
        self.assertEqual(build_rating_url("episode", 42), "/media/episode/42/rating/")
