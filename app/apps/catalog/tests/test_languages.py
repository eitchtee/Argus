from unittest.mock import Mock, patch

from django.core.cache import cache
from django.test import TestCase

from apps.catalog.languages import (
    get_language_choices,
    language_catalog_cache_key,
    language_base_code,
    language_codes_match,
    language_display_name,
)
from apps.catalog.providers.base import LanguageOptionDTO
from apps.catalog.tasks import refresh_language_catalog


class LanguageCatalogTests(TestCase):
    def setUp(self):
        cache.clear()

    def tearDown(self):
        cache.clear()

    def test_cache_hit_returns_choices_without_queuing_refresh(self):
        cache.set(
            language_catalog_cache_key("tvdb"),
            [{"code": "eng", "name": "English"}, {"code": "por", "name": "Português"}],
            timeout=None,
        )

        with patch("apps.catalog.tasks.refresh_language_catalog") as refresh:
            choices = get_language_choices("tvdb")

        self.assertEqual(choices, (("eng", "English"), ("por", "Português")))
        refresh.assert_not_called()

    def test_cache_miss_returns_english_and_queues_one_refresh(self):
        with patch("apps.catalog.tasks.refresh_language_catalog") as refresh:
            first = get_language_choices("tmdb")
            second = get_language_choices("tmdb")

        self.assertEqual(first, (("en-US", "English (United States)"),))
        self.assertEqual(second, first)
        refresh.defer.assert_called_once_with(provider_name="tmdb")

    def test_display_name_normalizes_provider_language_codes(self):
        self.assertEqual(language_display_name("por"), "Portuguese")
        self.assertEqual(language_display_name("fra"), "French")
        self.assertEqual(language_display_name("pt-BR"), "Brazilian Portuguese")
        self.assertEqual(
            language_display_name("en-US", "English (United States)"),
            "English (United States)",
        )
        self.assertTrue(language_codes_match("por", "pt"))
        self.assertTrue(language_codes_match("eng", "en-US"))
        self.assertFalse(language_codes_match("pt-BR", "pt-PT"))
        self.assertEqual(language_base_code("en-GB"), "en")
        self.assertEqual(language_base_code("por"), "pt")

    @patch("apps.catalog.tasks.get_provider")
    def test_refresh_task_stores_normalized_choices_and_english_default(self, get_provider):
        provider = Mock()
        provider.list_languages.return_value = [
            LanguageOptionDTO(code="pt-BR", name="Português (Brasil)")
        ]
        get_provider.return_value = provider

        refresh_language_catalog.func("tmdb")

        self.assertEqual(
            get_language_choices("tmdb"),
            (
                ("en-US", "English (United States)"),
                ("pt-BR", "Português (Brasil)"),
            ),
        )
