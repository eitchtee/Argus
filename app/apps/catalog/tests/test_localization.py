from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase
from django.utils.formats import get_format
from django.utils import translation

from apps.catalog.localization import (
    LocalizedRecord,
    date_format_for_user,
    datetime_format_for_user,
    metadata_language_for_user,
    merge_translation_maps,
    resolve_field,
    resolve_from_map,
    resolve_title,
)
from apps.movies.models import Movie
from apps.tv.models import Episode, Season, Show


class ResolveFromMapTests(TestCase):
    def test_translation_merge_preserves_languages_and_merges_fields(self):
        self.assertEqual(
            merge_translation_maps(
                {"en-US": {"title": "English", "overview": "Old"}},
                {"en-US": {"overview": "New"}, "pt-BR": {"title": "Português"}},
            ),
            {
                "en-US": {"title": "English", "overview": "New"},
                "pt-BR": {"title": "Português"},
            },
        )

    def test_selected_language_wins_per_field(self):
        translations = {
            "en-US": {"title": "English", "overview": "English overview"},
            "pt-BR": {"title": "Português", "overview": ""},
        }

        self.assertEqual(
            resolve_from_map(translations, "title", "pt-BR", "en-US", "Scalar"),
            "Português",
        )
        self.assertEqual(
            resolve_from_map(
                translations,
                "overview",
                "pt-BR",
                "en-US",
                "Scalar overview",
            ),
            "English overview",
        )

    def test_scalar_is_final_fallback(self):
        self.assertEqual(resolve_from_map({}, "title", "pt-BR", "en-US", "Scalar"), "Scalar")
        self.assertEqual(resolve_from_map({}, "title", "pt-BR", "en-US"), "")


class ResolveFieldTests(SimpleTestCase):
    def test_movie_uses_tmdb_default_language(self):
        movie = Movie(
            title="Scalar",
            translations={"en-US": {"title": "English"}},
        )

        self.assertEqual(resolve_field(movie, "title", "de-DE"), "English")

    def test_tv_child_derives_provider_from_show(self):
        show = Show(external_id="1", name="Show")
        season = Season(show=show, season_number=1)
        episode = Episode(
            show=show,
            season=season,
            season_number=1,
            episode_number=1,
            name="Scalar",
            translations={"por": {"name": "Piloto"}},
        )

        self.assertEqual(resolve_field(episode, "name", "por"), "Piloto")

    def test_tv_numbered_names_fallback_when_provider_name_is_missing(self):
        show = Show(external_id="1", name="Show")
        season = Season(
            show=show,
            season_number=1,
            name="Provider season name",
            translations={"por": {"name": "Nome traduzido"}},
        )
        episode = Episode(
            show=show,
            season=season,
            season_number=1,
            episode_number=1,
            translations={"por": {}},
        )

        self.assertEqual(resolve_field(season, "name", "por"), "Season 1")
        self.assertEqual(resolve_field(episode, "name", "por"), "Episode 1")

    def test_proxy_resolves_known_fields_and_delegates_other_attributes(self):
        movie = Movie(
            external_id="550",
            title="Fight Club",
            translations={"pt-BR": {"title": "Clube da Luta"}},
        )
        localized = LocalizedRecord(movie, "pt-BR", overrides={"year": 1999})

        self.assertEqual(localized.title, "Clube da Luta")
        self.assertEqual(localized.search_title, "Clube da Luta")
        self.assertEqual(localized.external_id, "550")
        self.assertEqual(localized.year, 1999)

    def test_proxy_search_title_keeps_translation_when_original_title_is_visible(self):
        movie = Movie(
            title="Clube da Luta",
            original_title="Fight Club",
            translations={"pt-BR": {"title": "Clube da Luta"}},
        )
        localized = LocalizedRecord(movie, "pt-BR", use_original_title=True)

        self.assertEqual(localized.title, "Fight Club")
        self.assertEqual(localized.search_title, "Clube da Luta Fight Club")

    def test_title_resolver_uses_original_title_when_preference_is_enabled(self):
        movie = Movie(
            title="Clube da Luta",
            original_title="Fight Club",
            translations={"pt-BR": {"title": "Clube da Luta"}},
        )

        self.assertEqual(
            resolve_title(movie, "pt-BR", use_original_title=True),
            "Fight Club",
        )

    def test_title_resolver_keeps_translation_when_preference_is_disabled(self):
        movie = Movie(
            title="Clube da Luta",
            original_title="Fight Club",
            translations={"pt-BR": {"title": "Clube da Luta"}},
        )

        self.assertEqual(resolve_title(movie, "pt-BR"), "Clube da Luta")

    def test_title_resolver_falls_back_when_original_title_is_missing(self):
        movie = Movie(
            title="Clube da Luta",
            translations={"pt-BR": {"title": "Clube da Luta"}},
        )

        self.assertEqual(
            resolve_title(movie, "pt-BR", use_original_title=True),
            "Clube da Luta",
        )


class UserMetadataLanguageTests(TestCase):
    def test_provider_preferences_ignore_active_interface_language(self):
        user = get_user_model().objects.create_user("user@example.com")
        user.settings.language = "pt-br"
        user.settings.tvdb_metadata_language = "spa"
        user.settings.tmdb_metadata_language = "de-DE"
        user.settings.save()

        with translation.override("fr"):
            self.assertEqual(metadata_language_for_user(user, "tvdb"), "spa")
            self.assertEqual(metadata_language_for_user(user, "tmdb"), "de-DE")


class UserDateFormatTests(SimpleTestCase):
    def test_automatic_formats_follow_the_active_interface_language(self):
        user = type(
            "UserStub",
            (),
            {
                "settings": type(
                    "SettingsStub",
                    (),
                    {
                        "date_format": "SHORT_DATE_FORMAT",
                        "datetime_format": "SHORT_DATETIME_FORMAT",
                    },
                )(),
            },
        )()

        with translation.override("pt-br"):
            self.assertEqual(
                date_format_for_user(user),
                "d/m/Y",
            )
            self.assertEqual(
                datetime_format_for_user(user),
                "d/m/Y H:i",
            )

    def test_custom_datetime_format_is_preserved(self):
        user = type(
            "UserStub",
            (),
            {
                "settings": type(
                    "SettingsStub",
                    (),
                    {"datetime_format": "Y-m-d h:i A"},
                )(),
            },
        )()

        self.assertEqual(datetime_format_for_user(user), "Y-m-d h:i A")

    def test_custom_datetime_format_extracts_time_when_it_is_first(self):
        user = type(
            "UserStub",
            (object,),
            {
                "settings": type(
                    "SettingsStub",
                    (object,),
                    {"datetime_format": "H:i d-m-Y"},
                )(),
            },
        )()

        from apps.catalog.localization import time_format_for_user

        self.assertEqual(time_format_for_user(user), "H:i")

    def test_automatic_time_format_uses_the_active_locale_time_format(self):
        user = type(
            "UserStub",
            (object,),
            {
                "settings": type(
                    "SettingsStub",
                    (object,),
                    {"datetime_format": "SHORT_DATETIME_FORMAT"},
                )(),
            },
        )()

        from apps.catalog.localization import time_format_for_user

        with translation.override("vi"):
            self.assertEqual(
                time_format_for_user(user),
                get_format("TIME_FORMAT", use_l10n=True),
            )
