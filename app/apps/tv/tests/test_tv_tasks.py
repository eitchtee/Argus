from unittest.mock import patch

from django.test import TestCase

from apps.catalog.providers.base import LanguageOptionDTO
from apps.catalog.providers.exceptions import ProviderError
from apps.tv.models import Show


class TVTranslationTaskTests(TestCase):
    @patch("apps.tv.services.get_provider")
    @patch("apps.tv.tasks.tv_services.import_show")
    def test_hydration_processes_all_languages_then_reports_failures(
        self,
        import_show,
        get_provider,
    ):
        from apps.tv.tasks import hydrate_show_translations

        show = Show.objects.create(external_id="123", name="Show")
        provider = get_provider.return_value
        provider.list_languages.return_value = [
            LanguageOptionDTO("eng", "English"),
            LanguageOptionDTO("por", "Português"),
        ]
        import_show.side_effect = [show, ProviderError("failed")]

        with self.assertRaisesMessage(ProviderError, "por"):
            hydrate_show_translations.call_local(show.id)

        self.assertEqual(
            [call.kwargs["language"] for call in import_show.call_args_list],
            ["eng", "por"],
        )

    @patch(
        "apps.tv.tasks.tv_services.hydrate_show_translations_sync",
        create=True,
    )
    def test_huey_task_delegates_to_synchronous_hydration(
        self,
        hydrate_show_translations_sync,
    ):
        from apps.tv.tasks import hydrate_show_translations

        show = Show.objects.create(external_id="123", name="Show")
        hydrate_show_translations_sync.return_value = show

        result = hydrate_show_translations.call_local(show.id)

        hydrate_show_translations_sync.assert_called_once_with(show.id)
        self.assertEqual(result, show)
