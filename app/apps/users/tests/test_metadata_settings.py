from django.contrib.auth import get_user_model
from django.test import TestCase


class MetadataSettingsDefaultsTests(TestCase):
    def test_provider_metadata_languages_default_independently_from_interface(self):
        user = get_user_model().objects.create_user(
            "user@example.com",
            password="password",
        )

        self.assertEqual(user.settings.language, "auto")
        self.assertEqual(user.settings.tvdb_metadata_language, "eng")
        self.assertEqual(user.settings.tmdb_metadata_language, "en-US")
