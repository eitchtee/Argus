from datetime import date, timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.tv.models import Episode, Season, Show, UserEpisode, UserShow
from apps.catalog.models import SyncStatus, Tier
from apps.tv.services import (
    delete_show_data,
    drop_show,
    pause_show,
    refresh_show,
    switch_show_provider,
    track_show,
)


class TrackShowServiceTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("user@example.com")

    def test_track_show_imports_show_and_starts_tracking(self):
        show = Show.objects.create(external_id="123", name="Foo")
        self.user.settings.tvdb_metadata_language = "por"
        self.user.settings.save()
        import_calls = []
        hydration_calls = []

        def import_func(external_id, *, language, provider="tvdb"):
            import_calls.append((provider, external_id, language))
            return show

        user_show = track_show(
            self.user,
            "123",
            import_func=import_func,
            hydrate_func=hydration_calls.append,
        )

        self.assertEqual(import_calls, [("tvdb", "123", "por")])
        self.assertEqual(hydration_calls, [show.id])
        self.assertEqual(user_show.user, self.user)
        self.assertEqual(user_show.show, show)
        self.assertEqual(user_show.status, UserShow.Status.TRACKED)
        self.assertIsNotNone(user_show.tracking_started_at)

    def test_track_show_reuses_existing_user_show_row(self):
        show = Show.objects.create(external_id="123", name="Foo")
        existing = UserShow.objects.create(user=self.user, show=show, status=UserShow.Status.DROPPED)

        user_show = track_show(
            self.user,
            "123",
            import_func=lambda external_id, *, language, provider="tvdb": show,
            hydrate_func=lambda _show_id: None,
        )

        self.assertEqual(user_show.id, existing.id)
        self.assertEqual(user_show.status, UserShow.Status.TRACKED)

    def test_retracking_show_does_not_enqueue_translation_hydration_again(self):
        show = Show.objects.create(external_id="123", name="Foo")
        hydration_calls = []

        track_show(
            self.user,
            "123",
            import_func=lambda external_id, *, language, provider="tvdb": show,
            hydrate_func=hydration_calls.append,
        )
        track_show(
            self.user,
            "123",
            import_func=lambda external_id, *, language, provider="tvdb": show,
            hydrate_func=hydration_calls.append,
        )

        self.assertEqual(hydration_calls, [show.id])

    def test_track_show_uses_the_selected_provider_language(self):
        show = Show.objects.create(provider="tmdb", external_id="1399", name="Foo")
        self.user.settings.tmdb_metadata_language = "pt-BR"
        self.user.settings.save()
        import_calls = []

        def import_func(external_id, *, language, provider="tvdb"):
            import_calls.append((provider, external_id, language))
            return show

        track_show(
            self.user,
            "1399",
            provider="tmdb",
            import_func=import_func,
            hydrate_func=lambda _show_id: None,
        )

        self.assertEqual(import_calls, [("tmdb", "1399", "pt-BR")])

    def test_track_show_rejects_match_already_tracked_on_other_provider(self):
        source = Show.objects.create(
            provider="tvdb",
            external_id="121361",
            tmdb_id="1399",
            name="Game of Thrones",
        )
        target = Show.objects.create(
            provider="tmdb",
            external_id="1399",
            tvdb_id="121361",
            name="Game of Thrones",
        )
        UserShow.objects.create(user=self.user, show=source)

        with self.assertRaisesMessage(ValueError, "Tracked on another provider."):
            track_show(
                self.user,
                "1399",
                provider="tmdb",
                import_func=lambda external_id, *, language, provider="tvdb": target,
                hydrate_func=lambda _show_id: None,
            )

        self.assertFalse(UserShow.objects.filter(user=self.user, show=target).exists())

    def test_refresh_show_marks_pending_and_enqueues_sync(self):
        show = Show.objects.create(external_id="123", name="Foo")
        UserShow.objects.create(user=self.user, show=show, status=UserShow.Status.PAUSED)
        sync_calls = []

        refreshed = refresh_show(self.user, show, sync_func=sync_calls.append)

        self.assertEqual(refreshed.id, show.id)
        self.assertEqual(refreshed.sync_status, SyncStatus.PENDING)
        self.assertEqual(sync_calls, [show.id])

    def test_refresh_show_rejects_untracked_show(self):
        show = Show.objects.create(external_id="123", name="Foo")

        with self.assertRaisesMessage(ValueError, "Show is not tracked by this user."):
            refresh_show(self.user, show, sync_func=lambda _show_id: None)

    def test_switch_show_provider_moves_state_and_matching_watched_episodes(self):
        source = Show.objects.create(
            provider="tvdb",
            external_id="121361",
            tmdb_id="1399",
            name="Game of Thrones",
        )
        source_season = Season.objects.create(show=source, season_number=1, name="Season 1")
        source_episode = Episode.objects.create(
            show=source,
            season=source_season,
            season_number=1,
            episode_number=1,
            name="Winter Is Coming",
        )
        removed_episode = Episode.objects.create(
            show=source,
            season=source_season,
            season_number=1,
            episode_number=2,
            name="The Kingsroad",
        )
        source_season_2 = Season.objects.create(show=source, season_number=2, name="Season 2")
        removed_episode_2 = Episode.objects.create(
            show=source,
            season=source_season_2,
            season_number=2,
            episode_number=1,
            name="The North Remembers",
        )
        target = Show.objects.create(
            provider="tmdb",
            external_id="1399",
            tvdb_id="121361",
            name="Game of Thrones",
        )
        target_season = Season.objects.create(show=target, season_number=1, name="Season 1")
        target_episode = Episode.objects.create(
            show=target,
            season=target_season,
            season_number=1,
            episode_number=1,
            name="Winter Is Coming",
        )
        other_user = get_user_model().objects.create_user("other@example.com")
        UserShow.objects.create(
            user=self.user,
            show=source,
            status=UserShow.Status.PAUSED,
            tier=Tier.B,
        )
        UserShow.objects.create(user=other_user, show=source)
        seen_at = timezone.now() - timedelta(days=4)
        UserEpisode.objects.create(user=self.user, episode=source_episode, seen_at=seen_at)
        UserEpisode.objects.create(user=self.user, episode=removed_episode)
        UserEpisode.objects.create(user=self.user, episode=removed_episode_2)
        sync_calls = []

        switched = switch_show_provider(
            self.user,
            source_provider="tvdb",
            source_external_id="121361",
            target_provider="tmdb",
            target_external_id="1399",
            sync_func=sync_calls.append,
        )

        self.assertEqual(switched.id, target.id)
        self.assertEqual(switched.sync_status, SyncStatus.PENDING)
        moved = UserShow.objects.get(user=self.user, show=target)
        self.assertEqual(moved.status, UserShow.Status.PAUSED)
        self.assertEqual(moved.tier, Tier.B)
        self.assertTrue(
            UserEpisode.objects.filter(user=self.user, episode=target_episode).exists()
        )
        self.assertEqual(
            UserEpisode.objects.get(user=self.user, episode=target_episode).seen_at,
            seen_at,
        )
        self.assertFalse(
            UserEpisode.objects.filter(user=self.user, episode=removed_episode).exists()
        )
        self.assertFalse(
            UserEpisode.objects.filter(user=self.user, episode=removed_episode_2).exists()
        )
        self.assertEqual(sync_calls, [target.id])
        self.assertFalse(UserShow.objects.filter(user=self.user, show=source).exists())
        self.assertTrue(UserShow.objects.filter(user=other_user, show=source).exists())
        self.assertTrue(Show.objects.filter(id=source.id).exists())

    def test_switch_show_provider_clones_new_target_catalog(self):
        source = Show.objects.create(
            provider="tvdb",
            external_id="121361",
            tmdb_id="1399",
            name="Game of Thrones",
        )
        source_season = Season.objects.create(show=source, season_number=1, name="Season 1")
        source_episode = Episode.objects.create(
            show=source,
            season=source_season,
            season_number=1,
            episode_number=1,
            name="Winter Is Coming",
        )
        UserShow.objects.create(user=self.user, show=source)
        UserEpisode.objects.create(user=self.user, episode=source_episode)

        switched = switch_show_provider(
            self.user,
            source_provider="tvdb",
            source_external_id="121361",
            target_provider="tmdb",
            target_external_id="1399",
            sync_func=lambda _show_id: None,
        )

        target_episode = Episode.objects.get(
            show=switched,
            season_number=1,
            episode_number=1,
        )
        self.assertTrue(UserEpisode.objects.filter(user=self.user, episode=target_episode).exists())
        self.assertEqual(switched.seasons.count(), 1)
        self.assertEqual(switched.episodes.count(), 1)

    def test_switch_show_provider_preserves_tmdb_poster_url_until_sync(self):
        source = Show.objects.create(
            provider="tmdb",
            external_id="1399",
            tvdb_id="121361",
            name="Game of Thrones",
            poster_path="/tmdb-poster.jpg",
        )
        UserShow.objects.create(user=self.user, show=source)

        with override_settings(TMDB_IMAGE_BASE_URL="https://image.tmdb.org/t/p/"):
            switched = switch_show_provider(
                self.user,
                source_provider="tmdb",
                source_external_id="1399",
                target_provider="tvdb",
                target_external_id="121361",
                sync_func=lambda _show_id: None,
            )

        self.assertEqual(
            switched.poster_url,
            "https://image.tmdb.org/t/p/w342/tmdb-poster.jpg",
        )

    @patch("apps.tv.tasks.sync_show")
    def test_switch_show_provider_enqueues_default_background_sync(self, sync_show_mock):
        source = Show.objects.create(
            provider="tvdb",
            external_id="121361",
            tmdb_id="1399",
            name="Game of Thrones",
        )
        UserShow.objects.create(user=self.user, show=source)

        switched = switch_show_provider(
            self.user,
            source_provider="tvdb",
            source_external_id="121361",
            target_provider="tmdb",
            target_external_id="1399",
        )

        sync_show_mock.defer.assert_called_once_with(show_id=switched.id)


class DropShowServiceTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("user@example.com")

    def test_drop_show_stops_tracking_but_keeps_history(self):
        show = Show.objects.create(external_id="123", name="Foo")
        season = Season.objects.create(show=show, season_number=1, name="Season 1")
        episode = Episode.objects.create(
            show=show, season=season, season_number=1, episode_number=1, name="Pilot"
        )
        UserShow.objects.create(user=self.user, show=show, status=UserShow.Status.TRACKED)
        UserEpisode.objects.create(user=self.user, episode=episode)

        user_show = drop_show(self.user, show)

        self.assertEqual(user_show.status, UserShow.Status.DROPPED)
        self.assertTrue(UserShow.objects.filter(user=self.user, show=show).exists())
        self.assertTrue(UserEpisode.objects.filter(user=self.user, episode=episode).exists())


class PauseShowServiceTests(TestCase):
    def test_pause_show_keeps_history_and_sets_paused_status(self):
        user = get_user_model().objects.create_user("user@example.com")
        show = Show.objects.create(external_id="123", name="Foo")
        season = Season.objects.create(show=show, season_number=1, name="Season 1")
        episode = Episode.objects.create(
            show=show, season=season, season_number=1, episode_number=1, name="Pilot"
        )
        UserShow.objects.create(user=user, show=show, status=UserShow.Status.TRACKED)
        UserEpisode.objects.create(user=user, episode=episode)

        user_show = pause_show(user, show)

        self.assertEqual(user_show.status, UserShow.Status.PAUSED)
        self.assertTrue(UserEpisode.objects.filter(user=user, episode=episode).exists())


class DeleteShowDataServiceTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("user@example.com")
        self.other_user = get_user_model().objects.create_user("other@example.com")

    def test_delete_show_data_removes_user_show_and_user_episodes(self):
        show = Show.objects.create(external_id="123", name="Foo")
        season = Season.objects.create(show=show, season_number=1, name="Season 1")
        episode = Episode.objects.create(
            show=show, season=season, season_number=1, episode_number=1, name="Pilot"
        )
        UserShow.objects.create(user=self.user, show=show, status=UserShow.Status.TRACKED)
        UserEpisode.objects.create(user=self.user, episode=episode)

        delete_show_data(self.user, show)

        self.assertFalse(UserShow.objects.filter(user=self.user, show=show).exists())
        self.assertFalse(UserEpisode.objects.filter(user=self.user, episode=episode).exists())
        self.assertTrue(Show.objects.filter(id=show.id).exists())
        self.assertTrue(Episode.objects.filter(id=episode.id).exists())

    def test_delete_show_data_does_not_affect_other_users(self):
        show = Show.objects.create(external_id="123", name="Foo")
        season = Season.objects.create(show=show, season_number=1, name="Season 1")
        episode = Episode.objects.create(
            show=show, season=season, season_number=1, episode_number=1, name="Pilot"
        )
        UserShow.objects.create(user=self.user, show=show, status=UserShow.Status.TRACKED)
        UserShow.objects.create(user=self.other_user, show=show, status=UserShow.Status.TRACKED)
        UserEpisode.objects.create(user=self.other_user, episode=episode)

        delete_show_data(self.user, show)

        self.assertTrue(UserShow.objects.filter(user=self.other_user, show=show).exists())
        self.assertTrue(UserEpisode.objects.filter(user=self.other_user, episode=episode).exists())


class MarkEpisodeWatchedServiceTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("user@example.com")
        self.show = Show.objects.create(external_id="123", name="Foo")
        self.season = Season.objects.create(show=self.show, season_number=1, name="Season 1")
        self.episode = Episode.objects.create(
            show=self.show, season=self.season, season_number=1, episode_number=1, name="Pilot"
        )

    def test_mark_episode_watched_requires_tracking(self):
        from apps.tv.services import mark_episode_watched

        with self.assertRaises(ValueError):
            mark_episode_watched(self.user, self.episode)

    def test_mark_episode_watched_creates_user_episode(self):
        from apps.tv.services import mark_episode_watched

        UserShow.objects.create(user=self.user, show=self.show, status=UserShow.Status.TRACKED)

        mark_episode_watched(self.user, self.episode)

        self.assertTrue(UserEpisode.objects.filter(user=self.user, episode=self.episode).exists())

    def test_unmark_episode_watched_deletes_user_episode(self):
        from apps.tv.services import unmark_episode_watched

        UserShow.objects.create(user=self.user, show=self.show, status=UserShow.Status.TRACKED)
        UserEpisode.objects.create(user=self.user, episode=self.episode)

        unmark_episode_watched(self.user, self.episode)

        self.assertFalse(UserEpisode.objects.filter(user=self.user, episode=self.episode).exists())

    def test_unmark_episode_watched_requires_tracking(self):
        from apps.tv.services import unmark_episode_watched

        with self.assertRaises(ValueError):
            unmark_episode_watched(self.user, self.episode)


class MarkSeasonWatchedServiceTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("user@example.com")
        self.show = Show.objects.create(external_id="123", name="Foo")
        self.season = Season.objects.create(show=self.show, season_number=1, name="Season 1")
        self.aired_episode = Episode.objects.create(
            show=self.show,
            season=self.season,
            season_number=1,
            episode_number=1,
            name="Pilot",
            air_date=date.today() - timedelta(days=7),
        )
        self.unaired_episode = Episode.objects.create(
            show=self.show,
            season=self.season,
            season_number=1,
            episode_number=2,
            name="Upcoming",
            air_date=date.today() + timedelta(days=7),
        )
        self.no_date_episode = Episode.objects.create(
            show=self.show,
            season=self.season,
            season_number=1,
            episode_number=3,
            name="TBA",
            air_date=None,
        )

    def test_mark_season_watched_requires_tracking(self):
        from apps.tv.services import mark_season_watched

        with self.assertRaises(ValueError):
            mark_season_watched(self.user, self.season)

    def test_mark_season_watched_only_marks_aired_episodes(self):
        from apps.tv.services import mark_season_watched

        UserShow.objects.create(user=self.user, show=self.show, status=UserShow.Status.TRACKED)

        mark_season_watched(self.user, self.season)

        self.assertTrue(
            UserEpisode.objects.filter(user=self.user, episode=self.aired_episode).exists()
        )
        self.assertFalse(
            UserEpisode.objects.filter(user=self.user, episode=self.unaired_episode).exists()
        )
        self.assertFalse(
            UserEpisode.objects.filter(user=self.user, episode=self.no_date_episode).exists()
        )

    def test_unmark_season_watched_clears_all_episodes_regardless_of_air_date(self):
        from apps.tv.services import unmark_season_watched

        UserShow.objects.create(user=self.user, show=self.show, status=UserShow.Status.TRACKED)
        UserEpisode.objects.create(user=self.user, episode=self.aired_episode)
        UserEpisode.objects.create(user=self.user, episode=self.unaired_episode)

        unmark_season_watched(self.user, self.season)

        self.assertEqual(
            UserEpisode.objects.filter(user=self.user, episode__season=self.season).count(),
            0,
        )

    def test_unmark_season_watched_requires_tracking(self):
        from apps.tv.services import unmark_season_watched

        with self.assertRaises(ValueError):
            unmark_season_watched(self.user, self.season)


class MarkShowWatchedServiceTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("user@example.com")
        self.show = Show.objects.create(external_id="123", name="Foo")
        self.season_1 = Season.objects.create(show=self.show, season_number=1, name="Season 1")
        self.specials = Season.objects.create(show=self.show, season_number=0, name="Specials")
        self.aired_episode = Episode.objects.create(
            show=self.show,
            season=self.season_1,
            season_number=1,
            episode_number=1,
            name="Pilot",
            air_date=date.today() - timedelta(days=7),
        )
        self.unaired_episode = Episode.objects.create(
            show=self.show,
            season=self.season_1,
            season_number=1,
            episode_number=2,
            name="Upcoming",
            air_date=date.today() + timedelta(days=7),
        )
        self.special_episode = Episode.objects.create(
            show=self.show,
            season=self.specials,
            season_number=0,
            episode_number=1,
            name="Behind the scenes",
            air_date=date.today() - timedelta(days=7),
        )

    def test_mark_show_watched_requires_tracking(self):
        from apps.tv.services import mark_show_watched

        with self.assertRaises(ValueError):
            mark_show_watched(self.user, self.show)

    def test_mark_show_watched_only_marks_aired_numbered_season_episodes(self):
        from apps.tv.services import mark_show_watched

        UserShow.objects.create(user=self.user, show=self.show, status=UserShow.Status.TRACKED)

        mark_show_watched(self.user, self.show)

        self.assertTrue(
            UserEpisode.objects.filter(user=self.user, episode=self.aired_episode).exists()
        )
        self.assertFalse(
            UserEpisode.objects.filter(user=self.user, episode=self.unaired_episode).exists()
        )
        self.assertFalse(
            UserEpisode.objects.filter(user=self.user, episode=self.special_episode).exists()
        )

    def test_unmark_show_watched_excludes_specials(self):
        from apps.tv.services import unmark_show_watched

        UserShow.objects.create(user=self.user, show=self.show, status=UserShow.Status.TRACKED)
        UserEpisode.objects.create(user=self.user, episode=self.aired_episode)
        UserEpisode.objects.create(user=self.user, episode=self.special_episode)

        unmark_show_watched(self.user, self.show)

        self.assertFalse(
            UserEpisode.objects.filter(user=self.user, episode=self.aired_episode).exists()
        )
        self.assertTrue(
            UserEpisode.objects.filter(user=self.user, episode=self.special_episode).exists()
        )

    def test_unmark_show_watched_requires_tracking(self):
        from apps.tv.services import unmark_show_watched

        with self.assertRaises(ValueError):
            unmark_show_watched(self.user, self.show)
