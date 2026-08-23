from django.test import TestCase

from apps.catalog.providers.base import DetailDTO
from apps.movies.models import Movie
from apps.movies.services import import_movie


class FakeProvider:
    def __init__(self, detail):
        self.detail = detail

    def fetch_detail(self, external_id, *, language, media_type="movie"):
        return self.detail


def detail(**overrides):
    defaults = {
        "provider": "tmdb",
        "external_id": "550",
        "title": "Twelve Monkeys",
        "original_title": "Twelve Monkeys",
        "original_language": "en",
    }
    defaults.update(overrides)
    return DetailDTO(**defaults)


class AuthoritativeMovieTranslationsTests(TestCase):
    def _import(self, dto):
        return import_movie(
            "tmdb",
            "550",
            provider_getter=lambda _name: FakeProvider(dto),
        )

    def test_codes_the_provider_no_longer_lists_are_dropped(self):
        self._import(
            detail(
                translations={
                    "en-US": {"title": "Twelve Monkeys"},
                    "ar-AE": {"title": "Twelve Monkeys"},
                    "ar-SA": {"title": "Arabic title"},
                }
            )
        )

        movie = self._import(
            detail(
                translations={
                    "en-US": {"title": "Twelve Monkeys"},
                    "ar-SA": {"title": "Arabic title"},
                }
            )
        )

        # The stale ar-AE entry would otherwise shadow ar-SA forever, because
        # resolution matches it exactly and never reaches the sibling.
        self.assertNotIn("ar-AE", movie.translations)
        self.assertEqual(movie.translations["ar-SA"]["title"], "Arabic title")

    def test_an_empty_response_keeps_what_is_already_stored(self):
        self._import(
            detail(translations={"en-US": {"title": "Twelve Monkeys"}, "ar-SA": {"title": "Arabic"}})
        )

        movie = self._import(detail(translations={}))

        self.assertEqual(movie.translations["ar-SA"]["title"], "Arabic")

    def test_the_default_language_title_is_always_present(self):
        movie = self._import(detail(translations={"pt-BR": {"title": "Os 12 Macacos"}}))

        self.assertEqual(movie.title, "Twelve Monkeys")
        self.assertEqual(movie.translations["en-US"]["title"], "Twelve Monkeys")
        self.assertEqual(movie.translations["pt-BR"]["title"], "Os 12 Macacos")

    def test_a_reimport_still_refreshes_changed_text(self):
        self._import(detail(translations={"pt-BR": {"title": "Antigo"}}))

        movie = self._import(detail(translations={"pt-BR": {"title": "Os 12 Macacos"}}))

        self.assertEqual(movie.translations["pt-BR"]["title"], "Os 12 Macacos")
        self.assertEqual(Movie.objects.count(), 1)
