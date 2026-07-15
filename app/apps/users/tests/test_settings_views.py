from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from apps.users.forms import UserSettingsForm


LANGUAGE_CHOICES = {
    "tvdb": (("eng", "English"), ("por", "Português")),
    "tmdb": (("en-US", "English (United States)"), ("pt-BR", "Português (Brasil)")),
}


def choices_for(provider):
    return LANGUAGE_CHOICES[provider]


class UserSettingsViewTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            "user@example.com",
            password="password",
        )
        self.client.login(username="user@example.com", password="password")
        self.url = reverse("user_settings")

    @patch("apps.users.forms.get_language_choices", side_effect=choices_for)
    def test_settings_render_provider_specific_metadata_choices(self, _choices):
        response = self.client.get(self.url, HTTP_HX_REQUEST="true")

        self.assertContains(response, "Metadata")
        self.assertContains(response, "TV metadata language")
        self.assertContains(response, "Movie metadata language")
        self.assertContains(response, "Português")
        self.assertContains(response, "Português (Brasil)")
        self.assertContains(response, "This does not change the interface language")

    @patch("apps.users.forms.get_language_choices", side_effect=choices_for)
    def test_settings_save_interface_and_metadata_languages_independently(self, _choices):
        response = self.client.post(
            self.url,
            {
                "language": "en",
                "tvdb_metadata_language": "por",
                "tmdb_metadata_language": "pt-BR",
                "timezone": "auto",
                "date_format": "SHORT_DATE_FORMAT",
            },
            HTTP_HX_REQUEST="true",
        )

        self.assertEqual(response.status_code, 204)
        self.user.settings.refresh_from_db()
        self.assertEqual(self.user.settings.language, "en")
        self.assertEqual(self.user.settings.tvdb_metadata_language, "por")
        self.assertEqual(self.user.settings.tmdb_metadata_language, "pt-BR")

    @patch("apps.users.forms.get_language_choices", side_effect=choices_for)
    def test_provider_choices_validate_independently(self, _choices):
        form = UserSettingsForm(
            data={
                "language": "auto",
                "tvdb_metadata_language": "pt-BR",
                "tmdb_metadata_language": "pt-BR",
                "timezone": "auto",
                "date_format": "SHORT_DATE_FORMAT",
            },
            instance=self.user.settings,
        )

        self.assertFalse(form.is_valid())
        self.assertIn("tvdb_metadata_language", form.errors)

    @patch("apps.users.forms.get_language_choices", side_effect=choices_for)
    def test_obsolete_saved_choice_remains_visible(self, _choices):
        self.user.settings.tvdb_metadata_language = "legacy"
        self.user.settings.save()

        form = UserSettingsForm(instance=self.user.settings)

        self.assertIn(("legacy", "legacy"), form.fields["tvdb_metadata_language"].choices)
