from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import call, patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.catalog.models import SyncStatus
from apps.catalog.providers.base import LanguageOptionDTO
from apps.catalog.providers.exceptions import ProviderError
from apps.tv.models import Show, UserShow


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

    @patch("apps.tv.tasks.hydrate_show_translations")
    @patch("apps.tv.tasks.tv_services.import_show")
    def test_sync_show_refreshes_metadata_and_returns_translation_task_id(
        self,
        import_show,
        hydrate_show_translations,
    ):
        from apps.tv.tasks import sync_show

        show = Show.objects.create(external_id="123", name="Show")
        import_show.return_value = show
        hydrate_show_translations.return_value = SimpleNamespace(id="translation-1")

        result = sync_show.call_local(show.id)

        import_show.assert_called_once_with("123", language="eng")
        hydrate_show_translations.assert_called_once_with(show.id)
        self.assertEqual(
            result,
            {"item_id": show.id, "translation_task_id": "translation-1"},
        )

    @patch("apps.tv.tasks.tv_services.import_show")
    def test_sync_show_marks_provider_failures_as_error(self, import_show):
        from apps.tv.tasks import sync_show

        show = Show.objects.create(
            external_id="123",
            name="Show",
            sync_status=SyncStatus.OK,
        )
        import_show.side_effect = ProviderError("provider down")

        with self.assertRaises(ProviderError):
            sync_show.call_local(show.id)

        self.assertEqual(
            Show.objects.get(id=show.id).sync_status,
            SyncStatus.ERROR,
        )

    @override_settings(
        CATALOG_SHOW_SYNC_INTERVAL_DAYS=2,
        CATALOG_ENDED_SHOW_SYNC_INTERVAL_DAYS=30,
    )
    @patch("apps.tv.tasks.sync_show")
    def test_sync_tv_uses_continuing_and_ended_intervals(self, sync_show):
        from apps.tv.tasks import sync_tv

        user = get_user_model().objects.create_user("user@example.com")
        now = timezone.now()
        continuing_stale = Show.objects.create(
            external_id="1",
            name="Continuing stale",
            status="Continuing",
            last_synced_at=now - timedelta(days=3),
        )
        ended_stale = Show.objects.create(
            external_id="2",
            name="Ended stale",
            status="Ended",
            last_synced_at=now - timedelta(days=31),
        )
        continuing_fresh = Show.objects.create(
            external_id="3",
            name="Continuing fresh",
            status="Continuing",
            last_synced_at=now - timedelta(days=1),
        )
        ended_fresh = Show.objects.create(
            external_id="4",
            name="Ended fresh",
            status="Ended",
            last_synced_at=now - timedelta(days=10),
        )
        untracked_stale = Show.objects.create(
            external_id="5",
            name="Untracked stale",
            status="Continuing",
            last_synced_at=now - timedelta(days=10),
        )
        UserShow.objects.create(user=user, show=continuing_stale)
        UserShow.objects.create(user=user, show=ended_stale)
        UserShow.objects.create(user=user, show=continuing_fresh)
        UserShow.objects.create(user=user, show=ended_fresh)
        sync_show.side_effect = [
            SimpleNamespace(id="task-1"),
            SimpleNamespace(id="task-2"),
        ]

        result = sync_tv.call_local()

        self.assertEqual(result, ["task-1", "task-2"])
        self.assertCountEqual(
            [call.args[0] for call in sync_show.call_args_list],
            [continuing_stale.id, ended_stale.id],
        )
        self.assertNotIn(
            untracked_stale.id,
            [call.args[0] for call in sync_show.call_args_list],
        )

    @patch("apps.tv.tasks.sync_show")
    def test_sync_tv_force_all_enqueues_every_tvdb_show(self, sync_show):
        from apps.tv.tasks import sync_tv

        first = Show.objects.create(external_id="1", name="Tracked")
        second = Show.objects.create(external_id="2", name="Untracked")
        Show.objects.create(provider="other", external_id="3", name="Other provider")
        sync_show.side_effect = [
            SimpleNamespace(id="task-1"),
            SimpleNamespace(id="task-2"),
        ]

        result = sync_tv.call_local(force_all=True)

        self.assertEqual(result, ["task-1", "task-2"])
        self.assertCountEqual(
            [call.args[0] for call in sync_show.call_args_list],
            [first.id, second.id],
        )

    @patch("apps.tv.tasks.sync_tv")
    def test_daily_tv_sync_queues_default_dispatch(self, sync_tv):
        from apps.tv.tasks import daily_tv_sync

        daily_tv_sync.call_local()

        sync_tv.assert_called_once_with()
