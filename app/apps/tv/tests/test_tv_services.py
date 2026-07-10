from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.tv.models import Episode, Season, Show, UserEpisode, UserShow
from apps.tv.services import delete_show_data, drop_show, track_show


class TrackShowServiceTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("user@example.com")

    def test_track_show_imports_show_and_starts_tracking(self):
        show = Show.objects.create(external_id="123", name="Foo")
        import_calls = []

        def import_func(external_id):
            import_calls.append(external_id)
            return show

        user_show = track_show(self.user, "123", import_func=import_func)

        self.assertEqual(import_calls, ["123"])
        self.assertEqual(user_show.user, self.user)
        self.assertEqual(user_show.show, show)
        self.assertTrue(user_show.is_tracking)
        self.assertIsNotNone(user_show.tracking_started_at)

    def test_track_show_reuses_existing_user_show_row(self):
        show = Show.objects.create(external_id="123", name="Foo")
        existing = UserShow.objects.create(user=self.user, show=show, is_tracking=False)

        user_show = track_show(
            self.user,
            "123",
            import_func=lambda external_id: show,
        )

        self.assertEqual(user_show.id, existing.id)
        self.assertTrue(user_show.is_tracking)


class DropShowServiceTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("user@example.com")

    def test_drop_show_stops_tracking_but_keeps_history(self):
        show = Show.objects.create(external_id="123", name="Foo")
        season = Season.objects.create(show=show, season_number=1, name="Season 1")
        episode = Episode.objects.create(
            show=show, season=season, season_number=1, episode_number=1, name="Pilot"
        )
        UserShow.objects.create(user=self.user, show=show, is_tracking=True)
        UserEpisode.objects.create(user=self.user, episode=episode)

        user_show = drop_show(self.user, show)

        self.assertFalse(user_show.is_tracking)
        self.assertTrue(UserShow.objects.filter(user=self.user, show=show).exists())
        self.assertTrue(UserEpisode.objects.filter(user=self.user, episode=episode).exists())


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
        UserShow.objects.create(user=self.user, show=show, is_tracking=True)
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
        UserShow.objects.create(user=self.user, show=show, is_tracking=True)
        UserShow.objects.create(user=self.other_user, show=show, is_tracking=True)
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

        UserShow.objects.create(user=self.user, show=self.show, is_tracking=True)

        mark_episode_watched(self.user, self.episode)

        self.assertTrue(UserEpisode.objects.filter(user=self.user, episode=self.episode).exists())

    def test_unmark_episode_watched_deletes_user_episode(self):
        from apps.tv.services import unmark_episode_watched

        UserShow.objects.create(user=self.user, show=self.show, is_tracking=True)
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

        UserShow.objects.create(user=self.user, show=self.show, is_tracking=True)

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

        UserShow.objects.create(user=self.user, show=self.show, is_tracking=True)
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

        UserShow.objects.create(user=self.user, show=self.show, is_tracking=True)

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

        UserShow.objects.create(user=self.user, show=self.show, is_tracking=True)
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