from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import call, patch

from django.contrib.auth import get_user_model
from django.test import TransactionTestCase, override_settings
from django.utils import timezone

from apps.catalog.models import SyncStatus
from apps.catalog.providers.base import DetailDTO
from apps.catalog.providers.exceptions import ProviderError
from apps.tv.models import Show, UserShow


class TVTranslationTaskTests(TransactionTestCase):
    @patch("apps.tv.services.get_provider")
    def test_hydration_reuses_english_payload_across_languages(self, get_provider):
        from apps.tv.services import hydrate_show_translations_sync

        show = Show.objects.create(external_id="123", name="Show")
        provider = get_provider.return_value
        provider.fetch_detail.return_value = DetailDTO(
            provider="tvdb",
            external_id="123",
            title="Show",
            translations={"eng": {}, "por": {}},
        )
        provider.fetch_episodes.return_value = []
        provider.fetch_seasons.return_value = []

        hydrate_show_translations_sync(show.id)

        provider.fetch_detail.assert_called_once_with(
            "123",
            language="eng",
            media_type="tv",
        )
        self.assertEqual(
            [call.kwargs["language"] for call in provider.fetch_episodes.call_args_list],
            ["eng", "por"],
        )
        self.assertEqual(
            [call.kwargs["language"] for call in provider.fetch_seasons.call_args_list],
            ["eng", "por"],
        )

    @patch("apps.tv.services.get_provider")
    @patch("apps.tv.tasks.tv_services.import_show")
    def test_hydration_uses_languages_advertised_by_the_show_then_reports_failures(
        self,
        import_show,
        get_provider,
    ):
        from apps.tv.tasks import hydrate_show_translations

        show = Show.objects.create(external_id="123", name="Show")
        provider = get_provider.return_value
        provider.fetch_detail.return_value = SimpleNamespace(
            translations={"eng": {}, "por": {}},
        )
        provider.list_languages.side_effect = AssertionError(
            "hydration must not enumerate the global language catalog"
        )
        import_show.side_effect = [show, ProviderError("failed")]

        with self.assertRaisesMessage(ProviderError, "por"):
            hydrate_show_translations.func(show.id)

        self.assertEqual(
            [call.kwargs["language"] for call in import_show.call_args_list],
            ["eng", "por"],
        )
        self.assertEqual(
            [call.kwargs["sync_artworks"] for call in import_show.call_args_list],
            [True, False],
        )
        provider.fetch_detail.assert_called_once_with(
            "123",
            language="eng",
            media_type="tv",
        )

    @patch(
        "apps.tv.tasks.tv_services.hydrate_show_translations_sync",
        create=True,
    )
    def test_procrastinate_task_delegates_to_synchronous_hydration(
        self,
        hydrate_show_translations_sync,
    ):
        from apps.tv.tasks import hydrate_show_translations

        show = Show.objects.create(external_id="123", name="Show")
        hydrate_show_translations_sync.return_value = show

        result = hydrate_show_translations.func(show.id)

        hydrate_show_translations_sync.assert_called_once_with(show.id)
        self.assertEqual(result, show)

    @patch("apps.tv.tasks.tv_services.track_show", create=True)
    @patch("apps.tv.tasks.hydrate_show_translations")
    @patch("apps.tv.tasks.tv_services.import_show")
    def test_track_show_task_imports_metadata_without_reapplying_tracking(
        self,
        import_show,
        hydrate_show_translations,
        track_show,
    ):
        from apps.tv.tasks import track_show as track_show_task

        user = get_user_model().objects.create_user("tracked@example.com")
        show = Show.objects.create(provider="tmdb", external_id="1399", name="Show")
        UserShow.objects.create(
            user=user,
            show=show,
            status=UserShow.Status.PAUSED,
            on_watchlist=False,
        )
        import_show.return_value = show
        hydrate_show_translations.defer.return_value = 41

        result = track_show_task.func(user.id, show.id)

        track_show.assert_not_called()
        import_show.assert_called_once_with(
            "1399",
            provider="tmdb",
            language="en-US",
        )
        hydrate_show_translations.defer.assert_called_once_with(show_id=show.id)
        self.assertEqual(
            result,
            {"item_id": show.id, "translation_task_id": 41},
        )
        state = UserShow.objects.get(user=user, show=show)
        self.assertEqual(state.status, UserShow.Status.PAUSED)
        self.assertFalse(state.on_watchlist)

    @patch("apps.tv.tasks.tv_services.switch_show_provider", create=True)
    def test_switch_show_provider_task_reloads_user_and_delegates(self, switch_show_provider):
        from apps.tv.tasks import switch_show_provider as switch_task

        user = get_user_model().objects.create_user("switched@example.com")
        switch_show_provider.return_value = "switched"

        result = switch_task.func(
            user.id,
            source_provider="tvdb",
            source_external_id="121361",
            target_provider="tmdb",
            target_external_id="1399",
            target_imdb_id="tt0944947",
        )

        switch_show_provider.assert_called_once_with(
            user,
            source_provider="tvdb",
            source_external_id="121361",
            target_provider="tmdb",
            target_external_id="1399",
            target_imdb_id="tt0944947",
        )
        self.assertEqual(result, "switched")

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
        hydrate_show_translations.defer.return_value = 41

        result = sync_show.func(show.id)

        import_show.assert_called_once_with(
            "123",
            provider="tvdb",
            language="eng",
        )
        hydrate_show_translations.defer.assert_called_once_with(show_id=show.id)
        self.assertEqual(
            result,
            {"item_id": show.id, "translation_task_id": 41},
        )

    @patch("apps.tv.tasks.tv_services.import_show")
    def test_sync_show_uses_the_stored_provider(self, import_show):
        from apps.tv.tasks import sync_show

        show = Show.objects.create(
            provider="tmdb",
            external_id="1399",
            name="Game of Thrones",
        )
        import_show.return_value = show

        sync_show.func(show.id)

        import_show.assert_called_once_with(
            "1399",
            provider="tmdb",
            language="en-US",
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
            sync_show.func(show.id)

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
            normalized_status=Show.NormalizedStatus.CONTINUING,
            last_synced_at=now - timedelta(days=3),
        )
        tmdb_stale = Show.objects.create(
            provider="tmdb",
            external_id="6",
            name="TMDB stale",
            status="Continuing",
            normalized_status=Show.NormalizedStatus.CONTINUING,
            last_synced_at=now - timedelta(days=3),
        )
        ended_stale = Show.objects.create(
            external_id="2",
            name="Ended stale",
            status="Ended",
            normalized_status=Show.NormalizedStatus.ENDED,
            last_synced_at=now - timedelta(days=31),
        )
        continuing_fresh = Show.objects.create(
            external_id="3",
            name="Continuing fresh",
            status="Continuing",
            normalized_status=Show.NormalizedStatus.CONTINUING,
            last_synced_at=now - timedelta(days=1),
        )
        ended_fresh = Show.objects.create(
            external_id="4",
            name="Ended fresh",
            status="Ended",
            normalized_status=Show.NormalizedStatus.ENDED,
            last_synced_at=now - timedelta(days=10),
        )
        untracked_stale = Show.objects.create(
            external_id="5",
            name="Untracked stale",
            status="Continuing",
            normalized_status=Show.NormalizedStatus.CONTINUING,
            last_synced_at=now - timedelta(days=10),
        )
        UserShow.objects.create(user=user, show=continuing_stale)
        UserShow.objects.create(user=user, show=tmdb_stale)
        UserShow.objects.create(user=user, show=ended_stale)
        UserShow.objects.create(user=user, show=continuing_fresh)
        UserShow.objects.create(user=user, show=ended_fresh)
        sync_show.defer.side_effect = [41, 42, 43]

        result = sync_tv.func()

        self.assertEqual(result, [41, 42, 43])
        self.assertCountEqual(
            [call.kwargs["show_id"] for call in sync_show.defer.call_args_list],
            [continuing_stale.id, ended_stale.id, tmdb_stale.id],
        )
        self.assertNotIn(
            untracked_stale.id,
            [call.kwargs["show_id"] for call in sync_show.defer.call_args_list],
        )

    @override_settings(
        CATALOG_SHOW_SYNC_INTERVAL_DAYS=2,
        CATALOG_ENDED_SHOW_SYNC_INTERVAL_DAYS=30,
    )
    @patch("apps.tv.tasks.sync_show")
    def test_sync_tv_uses_normalized_status_for_ended_interval(self, sync_show):
        from apps.tv.tasks import sync_tv

        user = get_user_model().objects.create_user("user@example.com")
        now = timezone.now()
        canceled = Show.objects.create(
            provider="tmdb",
            external_id="canceled",
            name="Canceled",
            status="Canceled",
            normalized_status=Show.NormalizedStatus.ENDED,
            last_synced_at=now - timedelta(days=10),
        )
        raw_status_mismatch = Show.objects.create(
            external_id="mismatch",
            name="Raw Status Mismatch",
            status="Continuing",
            normalized_status=Show.NormalizedStatus.ENDED,
            last_synced_at=now - timedelta(days=10),
        )
        UserShow.objects.create(user=user, show=canceled)
        UserShow.objects.create(user=user, show=raw_status_mismatch)

        self.assertEqual(sync_tv.func(), [])
        sync_show.defer.assert_not_called()

    @patch("apps.tv.tasks.sync_show")
    def test_sync_tv_force_all_enqueues_every_supported_provider_show(self, sync_show):
        from apps.tv.tasks import sync_tv

        first = Show.objects.create(external_id="1", name="Tracked")
        second = Show.objects.create(external_id="2", name="Untracked")
        alternate = Show.objects.create(
            provider="tmdb",
            external_id="4",
            name="TMDB show",
        )
        Show.objects.create(provider="other", external_id="3", name="Other provider")
        sync_show.defer.side_effect = [41, 42, 43]

        result = sync_tv.func(force_all=True)

        self.assertEqual(result, [41, 42, 43])
        self.assertCountEqual(
            [call.kwargs["show_id"] for call in sync_show.defer.call_args_list],
            [first.id, second.id, alternate.id],
        )

    @patch("apps.tv.tasks.sync_tv")
    def test_daily_tv_sync_queues_default_dispatch(self, sync_tv):
        from apps.tv.tasks import daily_tv_sync

        daily_tv_sync.func(timestamp=0)

        sync_tv.defer.assert_called_once_with()
