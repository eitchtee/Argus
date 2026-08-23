from unittest.mock import patch

from django.test import TransactionTestCase

from apps.catalog.models import Genre
from apps.catalog.providers.base import GenreDTO, LanguageOptionDTO
from apps.catalog.tasks import refresh_genre_catalog


class FakeGenreProvider:
    def __init__(self, *, translates_genres, names_by_language):
        self.translates_genres = translates_genres
        self.names_by_language = names_by_language
        self.genre_calls = []

    def list_languages(self):
        return [LanguageOptionDTO(code, code) for code in self.names_by_language]

    def fetch_genres(self, *, media_type, language):
        self.genre_calls.append((media_type, language))
        return [
            GenreDTO(
                provider="tmdb",
                external_id=external_id,
                name=name,
                translations={language: {"name": name}},
            )
            for external_id, name in self.names_by_language[language].items()
        ]


class RefreshGenreCatalogTests(TransactionTestCase):
    def _run(self, provider, provider_name="tmdb"):
        with patch(
            "apps.catalog.tasks.get_provider",
            return_value=provider,
        ):
            return refresh_genre_catalog.func(provider_name)

    def test_it_stores_every_language_against_one_row_per_genre(self):
        provider = FakeGenreProvider(
            translates_genres=True,
            names_by_language={
                "en-US": {"28": "Action", "16": "Animation"},
                "pt-BR": {"28": "Ação", "16": "Animação"},
            },
        )

        self._run(provider)

        action = Genre.objects.get(provider="tmdb", external_id="28")
        self.assertEqual(action.name, "Action")
        self.assertEqual(action.translations["pt-BR"]["name"], "Ação")
        self.assertEqual(action.translations["en-US"]["name"], "Action")
        self.assertEqual(Genre.objects.count(), 2)

    def test_it_merges_into_rows_seeded_by_a_title_import(self):
        Genre.objects.create(
            provider="tmdb",
            external_id="28",
            name="Action",
            translations={"ja-JP": {"name": "アクション"}},
        )
        provider = FakeGenreProvider(
            translates_genres=True,
            names_by_language={"en-US": {"28": "Action"}, "pt-BR": {"28": "Ação"}},
        )

        self._run(provider)

        action = Genre.objects.get(provider="tmdb", external_id="28")
        self.assertEqual(action.translations["ja-JP"]["name"], "アクション")
        self.assertEqual(action.translations["pt-BR"]["name"], "Ação")
        self.assertEqual(Genre.objects.count(), 1)

    def test_a_provider_without_genre_translations_is_asked_once_per_media_type(self):
        provider = FakeGenreProvider(
            translates_genres=False,
            names_by_language={"eng": {"1": "Soap"}},
        )

        self._run(provider, provider_name="tvdb")

        self.assertEqual(provider.genre_calls, [("movie", "eng"), ("tv", "eng")])

    def test_a_translating_provider_is_asked_for_every_selectable_language(self):
        provider = FakeGenreProvider(
            translates_genres=True,
            names_by_language={
                "en-US": {"28": "Action"},
                "pt-BR": {"28": "Ação"},
            },
        )

        self._run(provider)

        self.assertEqual(
            sorted(provider.genre_calls),
            [
                ("movie", "en-US"),
                ("movie", "pt-BR"),
                ("tv", "en-US"),
                ("tv", "pt-BR"),
            ],
        )
