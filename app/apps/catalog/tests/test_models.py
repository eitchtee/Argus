from django.db import IntegrityError, connection, models
from django.test import TransactionTestCase

from apps.catalog.models import Genre, ProviderBackedModel, SyncStatus, Tier


class CatalogModelTests(TransactionTestCase):
    def test_genre_translations_default_to_empty_dict(self):
        genre = Genre.objects.create(provider="tmdb", external_id="18", name="Drama")

        self.assertEqual(genre.translations, {})

    def test_tier_choices_are_s_through_f(self):
        self.assertEqual(
            Tier.values,
            ["S", "A", "B", "C", "D", "E", "F"],
        )

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
