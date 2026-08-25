from django.contrib.auth import get_user_model
from django.db import IntegrityError, connection, models
from django.test import TransactionTestCase

from apps.catalog.models import (
    Genre,
    MediaArtwork,
    ProviderBackedModel,
    SyncStatus,
    UserMediaArtworkPreference,
)


class CatalogModelTests(TransactionTestCase):
    def test_genre_translations_default_to_empty_dict(self):
        genre = Genre.objects.create(provider="tmdb", external_id="18", name="Drama")

        self.assertEqual(genre.translations, {})

    def test_sync_status_choices_cover_import_lifecycle(self):
        self.assertEqual(
            SyncStatus.values,
            ["pending", "ok", "error"],
        )

    def test_genre_is_unique_per_provider_external_id(self):
        Genre.objects.create(provider="tmdb", external_id="28", name="Action")

        with self.assertRaises(IntegrityError):
            Genre.objects.create(provider="tmdb", external_id="28", name="Action copy")

    def test_genre_allows_same_external_id_from_different_providers(self):
        Genre.objects.create(provider="tmdb", external_id="28", name="Action")
        Genre.objects.create(provider="tvdb", external_id="28", name="Action")

        self.assertEqual(Genre.objects.count(), 2)

    def test_provider_backed_model_requires_unique_provider_external_id(self):
        class ProviderBackedTestItem(ProviderBackedModel):
            title = models.CharField(max_length=100)

            class Meta(ProviderBackedModel.Meta):
                app_label = "catalog"

        with connection.schema_editor() as schema_editor:
            schema_editor.create_model(ProviderBackedTestItem)

        try:
            ProviderBackedTestItem.objects.create(
                provider="tmdb",
                external_id="550",
                title="Fight Club",
            )

            with self.assertRaises(IntegrityError):
                ProviderBackedTestItem.objects.create(
                    provider="tmdb",
                    external_id="550",
                    title="Fight Club duplicate",
                )
        finally:
            with connection.schema_editor() as schema_editor:
                schema_editor.delete_model(ProviderBackedTestItem)

    def test_media_artwork_is_unique_per_media_kind_and_url(self):
        artwork = MediaArtwork.objects.create(
            provider="tmdb",
            media_type="movie",
            external_id="550",
            kind=MediaArtwork.Kind.POSTER,
            image_url="https://image.tmdb.org/t/p/original/poster.jpg",
        )

        with self.assertRaises(IntegrityError):
            MediaArtwork.objects.create(
                provider="tmdb",
                media_type="movie",
                external_id="550",
                kind=MediaArtwork.Kind.POSTER,
                image_url=artwork.image_url,
            )

    def test_user_media_artwork_preference_is_isolated_per_user(self):
        user_model = get_user_model()
        first_user = user_model.objects.create_user(email="first@example.com")
        second_user = user_model.objects.create_user(email="second@example.com")

        first = UserMediaArtworkPreference.objects.create(
            user=first_user,
            provider="tmdb",
            media_type="movie",
            external_id="550",
            language="pt-BR",
        )
        second = UserMediaArtworkPreference.objects.create(
            user=second_user,
            provider="tmdb",
            media_type="movie",
            external_id="550",
            language="en-US",
        )

        self.assertNotEqual(first.id, second.id)
        self.assertEqual(first.language, "pt-BR")
        self.assertEqual(second.language, "en-US")

    def test_deleting_selected_artwork_clears_only_the_selection(self):
        user = get_user_model().objects.create_user(email="viewer@example.com")
        poster = MediaArtwork.objects.create(
            provider="tvdb",
            media_type="tv",
            external_id="121361",
            kind=MediaArtwork.Kind.POSTER,
            image_url="https://artworks.thetvdb.com/poster.jpg",
        )
        preference = UserMediaArtworkPreference.objects.create(
            user=user,
            provider="tvdb",
            media_type="tv",
            external_id="121361",
            language="por",
            poster_artwork=poster,
        )

        poster.delete()
        preference.refresh_from_db()

        self.assertIsNone(preference.poster_artwork_id)
        self.assertEqual(preference.language, "por")
