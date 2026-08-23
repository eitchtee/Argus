from django.test import SimpleTestCase

from apps.catalog.localization import regional_siblings, resolve_from_map


class RegionalFallbackTests(SimpleTestCase):
    """TMDB publishes text per region, never per bare language, so a viewer
    reading ar-AE has no exact entry even when Arabic text exists."""

    translations = {
        "en-US": {"title": "Twelve Monkeys"},
        "ar-SA": {"title": "اثنا عشر قردا"},
        "pt-BR": {"title": "Os Doze Macacos"},
        "pt-PT": {"title": "Os 12 Macacos"},
    }

    def resolve(self, language):
        return resolve_from_map(
            self.translations,
            "title",
            language,
            "en-US",
            scalar="fallback scalar",
        )

    def test_an_exact_match_always_wins(self):
        self.assertEqual(self.resolve("pt-BR"), "Os Doze Macacos")
        self.assertEqual(self.resolve("pt-PT"), "Os 12 Macacos")

    def test_a_regional_sibling_is_preferred_over_the_provider_default(self):
        self.assertEqual(self.resolve("ar-AE"), "اثنا عشر قردا")

    def test_the_sibling_pick_is_stable_when_several_regions_exist(self):
        self.assertEqual(self.resolve("pt-AO"), "Os Doze Macacos")
        self.assertEqual(self.resolve("pt-AO"), "Os Doze Macacos")

    def test_it_falls_back_to_the_default_language_with_no_sibling(self):
        self.assertEqual(self.resolve("ja-JP"), "Twelve Monkeys")

    def test_it_falls_back_to_the_scalar_when_nothing_matches(self):
        self.assertEqual(
            resolve_from_map({}, "title", "ja-JP", "en-US", scalar="Raw Title"),
            "Raw Title",
        )

    def test_region_less_provider_codes_have_no_siblings(self):
        tvdb = {"eng": {"title": "Game of Thrones"}, "por": {"title": "A Guerra"}}

        self.assertEqual(regional_siblings(tvdb, "por"), ())
        self.assertEqual(
            resolve_from_map(tvdb, "title", "spa", "eng"),
            "Game of Thrones",
        )

    def test_a_sibling_never_leaks_across_base_languages(self):
        self.assertEqual(regional_siblings(self.translations, "pt-BR"), ("pt-PT",))
        self.assertEqual(regional_siblings(self.translations, "ar-AE"), ("ar-SA",))
        self.assertEqual(regional_siblings(self.translations, "ja-JP"), ())
