from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from apps.catalog.forms import MediaArtworkPreferenceForm
from apps.catalog.models import MediaArtwork, UserMediaArtworkPreference
from apps.movies.models import Movie
from apps.tv.models import Episode, Season, Show


@override_settings(
    STORAGES={
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {
            "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"
        },
    },
    DJANGO_VITE_DEV_MODE=True,
)
class MediaArtworkPreferenceViewTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            "viewer@example.com",
            password="password",
        )
        self.client.login(username="viewer@example.com", password="password")
        self.movie = Movie.objects.create(
            provider="tmdb",
            external_id="550",
            title="Fight Club",
        )
        self.default_poster = MediaArtwork.objects.create(
            provider="tmdb",
            media_type="movie",
            external_id="550",
            kind=MediaArtwork.Kind.POSTER,
            image_url="https://image.tmdb.org/t/p/original/default.jpg",
            is_default=True,
        )
        self.localized_poster = MediaArtwork.objects.create(
            provider="tmdb",
            media_type="movie",
            external_id="550",
            kind=MediaArtwork.Kind.POSTER,
            image_url="https://image.tmdb.org/t/p/original/portuguese.jpg",
            language="pt",
        )

    @patch("apps.catalog.forms.get_language_choices", return_value=(("en-US", "English"), ("pt-BR", "Português")))
    def test_get_renders_available_artwork_for_the_current_media(self, _choices):
        response = self.client.get(
            "/media/movie/550/artwork/?provider=tmdb",
            HTTP_HX_REQUEST="true",
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Fight Club")
        self.assertContains(response, self.default_poster.image_url)
        self.assertContains(response, self.localized_poster.image_url)
        self.assertContains(response, "Brazilian Portuguese")
        self.assertContains(response, '_="install init_tom_select"')

    @patch("apps.catalog.forms.get_language_choices", return_value=(("en-US", "English"), ("pt-BR", "Português")))
    def test_picker_uses_checked_radio_for_live_selection_feedback(self, _choices):
        response = self.client.get(
            "/media/movie/550/artwork/?provider=tmdb",
            HTTP_HX_REQUEST="true",
        )
        stylesheet = (
            Path(__file__).resolve().parents[4]
            / "frontend"
            / "src"
            / "styles"
            / "tailwind.css"
        ).read_text()

        self.assertContains(response, 'name="poster_artwork_id"')
        self.assertNotContains(response, "is-selected")
        self.assertIn(".artwork-picker-card:has(input:checked)", stylesheet)

    @patch("apps.catalog.forms.get_language_choices", return_value=(("en-US", "English"), ("pt-BR", "Português")))
    def test_artwork_pickers_are_open_collapsibles_without_nested_scroll(self, _choices):
        response = self.client.get(
            "/media/movie/550/artwork/?provider=tmdb",
            HTTP_HX_REQUEST="true",
        )
        stylesheet = (
            Path(__file__).resolve().parents[4]
            / "frontend"
            / "src"
            / "styles"
            / "tailwind.css"
        ).read_text()

        self.assertEqual(response.content.decode().count('x-data="{ expanded: true }"'), 2)
        self.assertEqual(
            response.content.decode().count(':class="{ \'collapse-open\': expanded }"'),
            2,
        )
        self.assertEqual(response.content.decode().count("x-collapse"), 2)
        self.assertNotIn("max-height: 25rem", stylesheet)

    @patch("apps.catalog.forms.get_language_choices", return_value=(("en-US", "English"),))
    def test_media_languages_are_available_before_the_catalog_refresh_finishes(self, _choices):
        self.movie.translations = {
            "en-US": {"title": "Fight Club"},
            "pt-BR": {"title": "Clube da Luta"},
        }
        self.movie.save(update_fields=["translations"])
        self.localized_poster.language = "spa"
        self.localized_poster.save(update_fields=["language"])

        response = self.client.get(
            "/media/movie/550/artwork/?provider=tmdb",
            HTTP_HX_REQUEST="true",
        )

        content = response.content.decode()
        self.assertIn('option value="pt-BR"', content)
        self.assertIn('option value="spa"', content)
        self.assertIn(">Spanish<", content)

    @patch("apps.catalog.forms.get_language_choices", return_value=(("eng", "English"),))
    def test_media_language_choices_dedupe_iso_code_aliases(self, _choices):
        self.movie.translations = {
            "por": {"title": "Portuguese title"},
            "pt": {"title": "Portuguese title"},
        }
        self.movie.save(update_fields=["translations"])

        response = self.client.get(
            "/media/movie/550/artwork/?provider=tmdb",
            HTTP_HX_REQUEST="true",
        )

        self.assertEqual(response.content.decode().count(">Portuguese<"), 1)

    @patch(
        "apps.catalog.forms.get_language_choices",
        return_value=(
            ("en-US", "English (US)"),
            ("en-AG", "English (AG)"),
            ("en-GB", "English (GB)"),
            ("pt-BR", "Portuguese (BR)"),
        ),
    )
    def test_media_language_choices_collapse_region_variants(self, _choices):
        self.movie.translations = {
            "en-US": {"title": "Sidewalls"},
            "en-GB": {"title": "Sidewalls"},
        }
        self.movie.save(update_fields=["translations"])

        form = MediaArtworkPreferenceForm(
            media=self.movie,
            user=self.user,
            artworks=MediaArtwork.objects.filter(
                provider="tmdb",
                media_type="movie",
                external_id="550",
            ),
        )
        choices = list(form.fields["language"].choices)

        self.assertEqual(
            [code for code, _label in choices if code.startswith("en-")],
            ["en-US"],
        )
        self.assertIn(("pt-BR", "Brazilian Portuguese"), choices)

    @patch(
        "apps.catalog.forms.get_language_choices",
        return_value=(("en-US", "English (US)"), ("en-GB", "English (GB)")),
    )
    def test_existing_regional_language_preference_remains_selectable(self, _choices):
        self.movie.translations = {
            "en-US": {"title": "Sidewalls"},
            "en-GB": {"title": "Sidewalls"},
        }
        self.movie.save(update_fields=["translations"])
        preference = UserMediaArtworkPreference.objects.create(
            user=self.user,
            provider="tmdb",
            media_type="movie",
            external_id="550",
            language="en-GB",
        )

        form = MediaArtworkPreferenceForm(
            media=self.movie,
            user=self.user,
            artworks=MediaArtwork.objects.filter(
                provider="tmdb",
                media_type="movie",
                external_id="550",
            ),
            preference=preference,
        )

        self.assertIn(("en-GB", "British English"), form.fields["language"].choices)

    @patch("apps.catalog.forms.get_language_choices", return_value=(("en-US", "English"), ("pt-BR", "Português")))
    def test_post_saves_choices_only_for_the_current_user(self, _choices):
        response = self.client.post(
            "/media/movie/550/artwork/?provider=tmdb",
            {
                "language": "pt-BR",
                "poster_artwork_id": str(self.localized_poster.id),
                "background_artwork_id": "",
            },
            HTTP_HX_REQUEST="true",
        )

        self.assertEqual(response.status_code, 204)
        preference = UserMediaArtworkPreference.objects.get(
            user=self.user,
            provider="tmdb",
            media_type="movie",
            external_id="550",
        )
        self.assertEqual(preference.language, "pt-BR")
        self.assertEqual(preference.poster_artwork_id, self.localized_poster.id)
        self.assertIsNone(preference.background_artwork_id)
        self.assertEqual(response["HX-Refresh"], "true")

    def test_movie_detail_uses_the_current_users_poster_selection(self):
        selected = MediaArtwork.objects.create(
            provider="tmdb",
            media_type="movie",
            external_id="550",
            kind=MediaArtwork.Kind.POSTER,
            image_url="https://image.tmdb.org/t/p/original/selected.jpg",
        )
        UserMediaArtworkPreference.objects.create(
            user=self.user,
            provider="tmdb",
            media_type="movie",
            external_id="550",
            poster_artwork=selected,
        )

        response = self.client.get("/movies/550/", HTTP_HX_REQUEST="true")

        self.assertContains(response, selected.image_url)

    def test_episode_detail_uses_the_show_background_selection(self):
        show = Show.objects.create(provider="tvdb", external_id="123", name="Show")
        season = Season.objects.create(show=show, season_number=1, name="Season 1")
        episode = Episode.objects.create(
            show=show,
            season=season,
            season_number=1,
            episode_number=1,
            name="Pilot",
        )
        selected = MediaArtwork.objects.create(
            provider="tvdb",
            media_type="tv",
            external_id="123",
            kind=MediaArtwork.Kind.BACKGROUND,
            image_url="https://artworks.thetvdb.com/selected-fanart.jpg",
        )
        UserMediaArtworkPreference.objects.create(
            user=self.user,
            provider="tvdb",
            media_type="tv",
            external_id="123",
            background_artwork=selected,
        )

        response = self.client.get(
            f"/tv/123/episodes/{episode.id}/",
            HTTP_HX_REQUEST="true",
        )

        self.assertContains(response, selected.image_url)
