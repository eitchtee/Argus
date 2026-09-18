from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from apps.catalog.providers.exceptions import NotFound, ProviderError


class DetailUnavailableTests(TestCase):
    """Untracked titles are rendered from the provider; a stale id must not 500."""

    def setUp(self):
        self.user = get_user_model().objects.create_user("user@example.com", password="password")
        self.client.login(username="user@example.com", password="password")

    @patch("apps.movies.views.get_movie_detail", side_effect=NotFound("TMDB item was not found."))
    def test_missing_movie_renders_not_found_fragment(self, _detail):
        response = self.client.get(
            reverse("movie-detail-content", kwargs={"external_id": "999999999"}),
            HTTP_HX_REQUEST="true",
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response["Argus-Swap-Error"], "true")
        self.assertContains(response, "could not be found", status_code=404)
        self.assertContains(response, "TMDB has no record", status_code=404)
        self.assertContains(response, reverse("catalog-search-page"), status_code=404)

    @patch("apps.movies.views.get_movie_detail", side_effect=ProviderError("boom"))
    def test_provider_outage_renders_unavailable_fragment(self, _detail):
        response = self.client.get(
            reverse("movie-detail-content", kwargs={"external_id": "550"}),
            HTTP_HX_REQUEST="true",
        )

        self.assertEqual(response.status_code, 502)
        self.assertContains(response, "unavailable right now", status_code=502)

    @patch("apps.tv.views.get_show_detail", side_effect=NotFound("TVDB item was not found."))
    def test_missing_show_renders_not_found_fragment(self, _detail):
        response = self.client.get(
            reverse("tv-detail-content", kwargs={"external_id": "999999999"}),
            HTTP_HX_REQUEST="true",
        )

        self.assertEqual(response.status_code, 404)
        self.assertContains(response, "TVDB has no record", status_code=404)
