from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from apps.catalog.providers.base import SearchResultDTO


class SearchAPITests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = get_user_model().objects.create_user(
            email="user@example.com",
            password="password",
        )

    def test_search_requires_authentication(self):
        response = self.client.get("/api/search", {"q": "Fight Club", "type": "movie"})

        self.assertEqual(response.status_code, 401)

    @patch("apps.catalog.api.catalog_search")
    def test_search_returns_normalized_results_with_tracking_state(self, catalog_search):
        catalog_search.return_value = [
            SearchResultDTO(
                provider="tmdb",
                external_id="550",
                title="Fight Club",
                year=1999,
                poster_url="https://image.tmdb.org/t/p/w342/poster.jpg",
                overview="Overview",
            )
        ]
        self.client.force_authenticate(self.user)
        self.user.settings.tmdb_metadata_language = "pt-BR"
        self.user.settings.save()

        response = self.client.get("/api/search", {"q": "Fight Club", "type": "movie"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "results": [
                    {
                        "provider": "tmdb",
                        "external_id": "550",
                        "title": "Fight Club",
                        "year": 1999,
                        "poster_url": "https://image.tmdb.org/t/p/w342/poster.jpg",
                        "overview": "Overview",
                        "already_tracked": False,
                    }
                ]
            },
        )
        catalog_search.assert_called_once_with(
            "Fight Club", media_type="movie", language="pt-BR", page=1
        )

    def test_search_requires_query(self):
        self.client.force_authenticate(self.user)

        response = self.client.get("/api/search", {"type": "movie"})

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["q"], ["This query parameter is required."])

    def test_search_validates_type(self):
        self.client.force_authenticate(self.user)

        response = self.client.get("/api/search", {"q": "Naruto", "type": "anime"})

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["type"], ['Must be "movie" or "tv".'])

    @patch("apps.catalog.api.catalog_search")
    def test_search_validates_page_as_positive_integer(self, catalog_search):
        self.client.force_authenticate(self.user)

        response = self.client.get("/api/search", {"q": "Fight Club", "type": "movie", "page": "0"})

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["page"], ["Must be a positive integer."])
        catalog_search.assert_not_called()
