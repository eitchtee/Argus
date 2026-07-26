from datetime import date, time, timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from apps.catalog.providers.base import CastMemberDTO, DetailDTO, EpisodeDTO
from apps.tv.models import Episode, Season, Show, UserEpisode, UserShow


@override_settings(
    STORAGES={
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {
            "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"
        },
    },
    DJANGO_VITE_DEV_MODE=True,
)
class ShowDetailViewTests(TestCase):
    def setUp(self):
        from django_vite.core.asset_loader import DjangoViteAssetLoader

        DjangoViteAssetLoader._instance = None

        self.user = get_user_model().objects.create_user("user@example.com", password="password")
        self.client.login(username="user@example.com", password="password")

    def tearDown(self):
        from django_vite.core.asset_loader import DjangoViteAssetLoader

        DjangoViteAssetLoader._instance = None

    def test_requires_auth(self):
        self.client.logout()
        response = self.client.get("/tv/123/")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login/", response["Location"])

    @patch("apps.tv.views._build_show_context")
    def test_boosted_page_shell_defers_show_context(self, build_show_context_mock):
        response = self.client.get(
            "/tv/123/",
            HTTP_HX_REQUEST="true",
            HTTP_HX_BOOSTED="true",
        )

        build_show_context_mock.assert_not_called()
        self.assertContains(response, 'id="tv-show-content"')
        self.assertContains(response, 'hx-get="/tv/123/"')
        self.assertContains(response, 'hx-trigger="load"')
        self.assertNotContains(response, "Foo")

    @patch("apps.tv.views.get_show_episodes")
    @patch("apps.tv.views.get_show_detail")
    def test_show_detail_fragment_defers_episode_data_to_secondary_request(
        self,
        get_show_detail_mock,
        get_show_episodes_mock,
    ):
        get_show_detail_mock.return_value = DetailDTO(
            provider="tvdb",
            external_id="123",
            title="Foo",
            overview="A show.",
        )

        response = self.client.get("/tv/123/", HTTP_HX_REQUEST="true")

        self.assertEqual(response.status_code, 200)
        get_show_detail_mock.assert_called_once_with(
            "123",
            language="eng",
            provider="tvdb",
        )
        get_show_episodes_mock.assert_not_called()
        self.assertContains(response, 'hx-get="/tv/123/episodes/"')
        self.assertContains(response, "Loading episodes")
        self.assertNotContains(response, "Pilot")

    @patch("apps.tv.views.get_show_episodes")
    def test_episode_fragment_loads_provider_episodes_and_labels_air_status(
        self,
        get_show_episodes_mock,
    ):
        get_show_episodes_mock.return_value = [
            EpisodeDTO(
                season_number=1,
                episode_number=1,
                name="Pilot",
                air_date="2020-01-01",
            ),
            EpisodeDTO(
                season_number=1,
                episode_number=2,
                name="Tomorrow",
                air_date="2999-01-01",
            ),
        ]

        response = self.client.get("/tv/123/episodes/", HTTP_HX_REQUEST="true")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Pilot")
        self.assertContains(response, "Aired")
        self.assertContains(response, "Upcoming")
        get_show_episodes_mock.assert_called_once_with(
            "123",
            language="eng",
            provider="tvdb",
        )

    @patch("apps.tv.views.get_show_episodes")
    @patch("apps.tv.views.get_show_detail")
    def test_renders_preview_from_provider_cache_when_not_imported(
        self,
        get_show_detail_mock,
        get_show_episodes_mock,
    ):
        get_show_detail_mock.return_value = DetailDTO(
            provider="tvdb",
            external_id="123",
            title="Foo",
            overview="A show.",
            backdrop_path="https://artworks.thetvdb.com/fanart.jpg",
            imdb_id="tt0944947",
            trailer_url="https://www.youtube.com/watch?v=abc123",
            average_runtime=57,
            airs_time="21:00",
            cast=[
                CastMemberDTO(
                    name="Emilia Clarke",
                    character="Daenerys Targaryen",
                    photo_url="https://artworks.thetvdb.com/clarke.jpg",
                ),
            ],
        )
        get_show_episodes_mock.return_value = [
            EpisodeDTO(season_number=1, episode_number=1, name="Pilot", air_date="2020-01-01"),
        ]
        response = self.client.get("/tv/123/", HTTP_HX_REQUEST="true")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Foo")
        self.assertNotContains(response, "Pilot")
        self.assertContains(response, "https://artworks.thetvdb.com/fanart.jpg")
        self.assertContains(response, "Emilia Clarke")
        self.assertContains(response, 'aria-label="Track show"')
        self.assertNotContains(response, 'aria-label="Refresh metadata"')
        self.assertNotContains(response, "checkbox-sm")
        episodes_response = self.client.get(
            "/tv/123/episodes/",
            HTTP_HX_REQUEST="true",
        )
        self.assertContains(episodes_response, "Pilot")
        get_show_episodes_mock.assert_called_once_with(
            "123",
            language="eng",
            provider="tvdb",
        )
        self.assertFalse(Show.objects.filter(external_id="123").exists())

    @patch("apps.tv.views.get_show_episodes")
    @patch("apps.tv.views.get_show_detail")
    def test_preview_uses_requested_provider_and_language(
        self,
        get_show_detail_mock,
        get_show_episodes_mock,
    ):
        get_show_detail_mock.return_value = DetailDTO(
            provider="tmdb",
            external_id="1399",
            title="Game of Thrones",
        )
        get_show_episodes_mock.return_value = []
        self.user.settings.tmdb_metadata_language = "en-US"
        self.user.settings.save()

        response = self.client.get("/tv/1399/?provider=tmdb", HTTP_HX_REQUEST="true")

        self.assertEqual(response.status_code, 200)
        get_show_detail_mock.assert_called_once_with(
            "1399",
            language="en-US",
            provider="tmdb",
        )
        get_show_episodes_mock.assert_not_called()
        self.client.get(
            "/tv/1399/episodes/?provider=tmdb",
            HTTP_HX_REQUEST="true",
        )
        get_show_episodes_mock.assert_called_once_with(
            "1399",
            language="en-US",
            provider="tmdb",
        )

    @patch("apps.tv.views.get_show_episodes")
    @patch("apps.tv.views.get_show_detail")
    def test_preview_uses_numbered_names_when_provider_names_are_empty(
        self,
        get_show_detail_mock,
        get_show_episodes_mock,
    ):
        get_show_detail_mock.return_value = DetailDTO(
            provider="tvdb",
            external_id="123",
            title="Sweetpea",
        )
        get_show_episodes_mock.return_value = [
            EpisodeDTO(season_number=1, episode_number=1, name="")
        ]
        self.user.settings.tvdb_metadata_language = "por"
        self.user.settings.save()

        response = self.client.get("/tv/123/", HTTP_HX_REQUEST="true")

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Episode 1")
        episodes_response = self.client.get(
            "/tv/123/episodes/",
            HTTP_HX_REQUEST="true",
        )
        self.assertContains(episodes_response, "Season 1")
        self.assertContains(episodes_response, "Episode 1")

    def test_renders_from_db_when_show_already_imported_by_any_user(self):
        other_user = get_user_model().objects.create_user("other@example.com")
        show = Show.objects.create(
            external_id="123",
            name="Foo",
            overview="A show.",
            backdrop_path="https://artworks.thetvdb.com/fanart.jpg",
            imdb_id="tt0944947",
            trailer_url="https://www.youtube.com/watch?v=abc123",
            average_runtime=57,
            last_air_date=date.today() - timedelta(days=200),
            airs_time=time(21, 0),
            airs_timezone="America/New_York",
            cast=[{
                "name": "Emilia Clarke",
                "character": "Daenerys Targaryen",
                "photo_url": "https://artworks.thetvdb.com/clarke.jpg",
            }],
        )
        season = Season.objects.create(show=show, season_number=1, name="Season 1")
        Episode.objects.create(
            show=show, season=season, season_number=1, episode_number=1, name="Pilot"
        )
        UserShow.objects.create(user=other_user, show=show, status=UserShow.Status.TRACKED)
        self.user.settings.timezone = "America/Sao_Paulo"
        self.user.settings.save(update_fields=["timezone"])

        response = self.client.get("/tv/123/", HTTP_HX_REQUEST="true")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Foo")
        self.assertNotContains(response, "Pilot")
        self.assertContains(response, "https://artworks.thetvdb.com/fanart.jpg")
        self.assertContains(response, "tt0944947")
        self.assertContains(response, "https://www.youtube.com/watch?v=abc123")
        self.assertContains(response, "57")
        self.assertContains(response, "11:00 PM")
        self.assertContains(response, "Original airing time: 9:00 PM America/New_York")
        self.assertNotContains(response, "Airs locally")
        self.assertContains(response, "Emilia Clarke")
        self.assertContains(response, "Daenerys Targaryen")
        self.assertContains(response, 'aria-label="Track show"')
        episodes_response = self.client.get(
            "/tv/123/episodes/",
            HTTP_HX_REQUEST="true",
        )
        self.assertContains(episodes_response, "Pilot")
        # Current user has not tracked it themselves: no checkboxes, no watched state.
        self.assertNotContains(episodes_response, "checkbox-sm")

    def test_hides_seasons_without_episodes_but_keeps_unreleased_episodes(self):
        show = Show.objects.create(external_id="123", name="Foo")
        Season.objects.create(show=show, season_number=1, name="Empty Season")
        populated_season = Season.objects.create(
            show=show,
            season_number=2,
            name="Season With Upcoming Episode",
        )
        Episode.objects.create(
            show=show,
            season=populated_season,
            season_number=2,
            episode_number=1,
            name="Tomorrow",
            air_date=date.today() + timedelta(days=1),
        )

        response = self.client.get(
            "/tv/123/episodes/",
            HTTP_HX_REQUEST="true",
        )

        self.assertNotContains(response, "Season 1")
        self.assertContains(response, "Season 2")
        self.assertContains(response, "Tomorrow")

    def test_cloaks_collapsed_season_content_until_alpine_initializes(self):
        show = Show.objects.create(external_id="123", name="Foo")
        season = Season.objects.create(show=show, season_number=1, name="Season 1")
        Episode.objects.create(
            show=show, season=season, season_number=1, episode_number=1, name="Pilot"
        )

        response = self.client.get(
            "/tv/123/episodes/",
            HTTP_HX_REQUEST="true",
        )

        self.assertRegex(
            response.content.decode(),
            r'<div(?=[^>]*x-show="expanded")(?=[^>]*x-cloak\b)[^>]*>',
        )

    def test_renders_interactive_checkboxes_when_current_user_tracks_it(self):
        show = Show.objects.create(external_id="123", name="Foo")
        season = Season.objects.create(show=show, season_number=1, name="Season 1")
        episode = Episode.objects.create(
            show=show,
            season=season,
            season_number=1,
            episode_number=1,
            name="Pilot",
            air_date=date.today() - timedelta(days=1),
        )
        UserShow.objects.create(user=self.user, show=show, status=UserShow.Status.TRACKED)
        UserEpisode.objects.create(user=self.user, episode=episode)

        response = self.client.get("/tv/123/", HTTP_HX_REQUEST="true")
        episodes_response = self.client.get(
            "/tv/123/episodes/",
            HTTP_HX_REQUEST="true",
        )

        self.assertContains(episodes_response, "checkbox-sm")
        self.assertContains(episodes_response, "checked")
        self.assertContains(response, 'aria-label="Show actions"')
        self.assertContains(response, 'aria-label="Drop show"')
        self.assertContains(response, 'aria-label="Pause show"')
        self.assertContains(response, 'aria-label="Mark show unwatched"')
        self.assertContains(response, "fa-circle-minus")
        self.assertContains(episodes_response, f"/tv/123/episodes/{episode.id}/\"")

    def test_tracked_show_renders_status_and_watched_actions(self):
        show = Show.objects.create(external_id="123", name="Foo")
        season = Season.objects.create(show=show, season_number=1, name="Season 1")
        Episode.objects.create(
            show=show, season=season, season_number=1, episode_number=1
        )
        UserShow.objects.create(user=self.user, show=show, status=UserShow.Status.TRACKED)

        response = self.client.get("/tv/123/", HTTP_HX_REQUEST="true")

        self.assertContains(response, 'aria-label="Show actions"')
        self.assertContains(response, 'aria-label="Pause show"')
        self.assertContains(response, 'aria-label="Drop show"')
        self.assertContains(response, 'aria-label="Delete show"')
        self.assertContains(response, 'aria-label="Mark show watched"')
        self.assertNotContains(response, "fa-bookmark-minus")

    def test_paused_show_renders_resume_drop_delete(self):
        show = Show.objects.create(external_id="123", name="Foo")
        UserShow.objects.create(user=self.user, show=show, status=UserShow.Status.PAUSED)

        response = self.client.get("/tv/123/", HTTP_HX_REQUEST="true")

        self.assertContains(response, 'aria-label="Start watching again"')
        self.assertContains(response, 'aria-label="Drop show"')
        self.assertContains(response, 'aria-label="Delete show"')
        self.assertNotContains(response, 'aria-label="Pause show"')
        self.assertNotContains(response, 'aria-label="Mark watched"')

    def test_dropped_show_renders_resume_pause_delete(self):
        show = Show.objects.create(external_id="123", name="Foo")
        UserShow.objects.create(user=self.user, show=show, status=UserShow.Status.DROPPED)

        response = self.client.get("/tv/123/", HTTP_HX_REQUEST="true")

        self.assertContains(response, 'aria-label="Start watching again"')
        self.assertContains(response, 'aria-label="Pause show"')
        self.assertContains(response, 'aria-label="Delete show"')
        self.assertNotContains(response, 'aria-label="Drop show"')
        self.assertNotContains(response, 'aria-label="Mark watched"')

    def test_shows_track_button_when_show_exists_but_user_not_tracking(self):
        other_user = get_user_model().objects.create_user("other@example.com")
        show = Show.objects.create(external_id="123", name="Foo")
        UserShow.objects.create(user=other_user, show=show, status=UserShow.Status.TRACKED)

        response = self.client.get("/tv/123/", HTTP_HX_REQUEST="true")

        self.assertContains(response, 'aria-label="Track show"')

    def test_shows_switch_action_when_tracked_on_another_provider(self):
        source = Show.objects.create(
            provider="tvdb",
            external_id="121361",
            tmdb_id="1399",
            name="Game of Thrones",
        )
        Show.objects.create(
            provider="tmdb",
            external_id="1399",
            tvdb_id="121361",
            name="Game of Thrones",
        )
        UserShow.objects.create(user=self.user, show=source, status=UserShow.Status.TRACKED)

        response = self.client.get("/tv/1399/?provider=tmdb", HTTP_HX_REQUEST="true")

        self.assertContains(response, "Tracked on another provider")
        self.assertContains(response, 'aria-label="Switch to TMDB"')
        self.assertContains(response, "/tv/1399/switch/")
        self.assertNotContains(response, 'aria-label="Track show"')
        self.assertRegex(
            response.content.decode(),
            r'<div id="show-actions" class="fab">\s*<div class="tooltip',
        )

    def test_switch_action_uses_reverse_provider(self):
        source = Show.objects.create(
            provider="tmdb",
            external_id="1399",
            tvdb_id="121361",
            name="Game of Thrones",
        )
        Show.objects.create(
            provider="tvdb",
            external_id="121361",
            tmdb_id="1399",
            name="Game of Thrones",
        )
        UserShow.objects.create(user=self.user, show=source, status=UserShow.Status.TRACKED)

        response = self.client.get("/tv/121361/?provider=tvdb", HTTP_HX_REQUEST="true")

        self.assertContains(response, 'aria-label="Switch to TVDB"')

    @patch("apps.tv.views.get_show_detail")
    def test_refreshes_missing_provider_ids_before_checking_switch_state(
        self,
        get_show_detail_mock,
    ):
        source = Show.objects.create(
            provider="tvdb",
            external_id="121361",
            imdb_id="tt0944947",
            name="Game of Thrones",
        )
        Show.objects.create(provider="tmdb", external_id="1399", name="Game of Thrones")
        UserShow.objects.create(user=self.user, show=source, status=UserShow.Status.TRACKED)
        get_show_detail_mock.return_value = DetailDTO(
            provider="tmdb",
            external_id="1399",
            title="Game of Thrones",
            imdb_id="tt0944947",
            tmdb_id="1399",
        )

        response = self.client.get("/tv/1399/?provider=tmdb", HTTP_HX_REQUEST="true")

        self.assertContains(response, 'aria-label="Switch to TMDB"')
        get_show_detail_mock.assert_called_once_with(
            "1399",
            language="en-US",
            provider="tmdb",
        )

    def test_shows_delete_button_after_drop_but_not_before_any_tracking(self):
        show = Show.objects.create(external_id="123", name="Foo")

        response_never_tracked = self.client.get("/tv/123/", HTTP_HX_REQUEST="true")
        self.assertNotContains(response_never_tracked, 'aria-label="Delete show"')

        UserShow.objects.create(user=self.user, show=show, status=UserShow.Status.DROPPED)
        response_dropped = self.client.get("/tv/123/", HTTP_HX_REQUEST="true")
        self.assertContains(response_dropped, 'aria-label="Delete show"')

    def test_tracked_show_renders_refresh_action(self):
        show = Show.objects.create(external_id="123", name="Foo")
        UserShow.objects.create(user=self.user, show=show, status=UserShow.Status.TRACKED)

        response = self.client.get("/tv/123/", HTTP_HX_REQUEST="true")

        self.assertContains(response, 'aria-label="Refresh metadata"')
        self.assertContains(response, "/tv/123/refresh/")
        self.assertContains(response, 'hx-swap="none"')

    def test_paused_show_can_be_tracked_or_deleted_but_not_paused_again(self):
        show = Show.objects.create(external_id="123", name="Foo")
        UserShow.objects.create(user=self.user, show=show, status=UserShow.Status.PAUSED)

        response = self.client.get("/tv/123/", HTTP_HX_REQUEST="true")

        self.assertContains(response, 'aria-label="Start watching again"')
        self.assertContains(response, 'aria-label="Delete show"')
        self.assertNotContains(response, 'aria-label="Pause show"')


class ShowTrackViewTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("user@example.com", password="password")
        self.client.login(username="user@example.com", password="password")

    def test_requires_htmx_header(self):
        response = self.client.post("/tv/123/track/")
        self.assertEqual(response.status_code, 403)

    @patch("apps.tv.views.queue_track_show")
    def test_post_tracks_show_and_redirects(self, queue_track_show_mock):
        response = self.client.post("/tv/123/track/", HTTP_HX_REQUEST="true")

        queue_track_show_mock.assert_called_once_with(self.user, "123", provider="tvdb")
        self.assertEqual(response["HX-Redirect"], "/tv/123/")

    @patch("apps.tv.views.queue_track_show")
    def test_post_tracks_show_with_requested_provider(self, queue_track_show_mock):
        response = self.client.post(
            "/tv/1399/track/?provider=tmdb",
            HTTP_HX_REQUEST="true",
        )

        queue_track_show_mock.assert_called_once_with(self.user, "1399", provider="tmdb")
        self.assertEqual(response["HX-Redirect"], "/tv/1399/?provider=tmdb")

    @patch("apps.tv.views.queue_track_show")
    def test_demo_mode_blocks_non_superusers(self, queue_track_show_mock):
        with self.settings(DEMO=True):
            response = self.client.post("/tv/123/track/", HTTP_HX_REQUEST="true")

        self.assertEqual(response.status_code, 403)
        queue_track_show_mock.assert_not_called()

    @patch("apps.tv.views.queue_track_show")
    def test_post_queues_show_tracking_without_calling_heavy_service(
        self,
        queue_track_show_mock,
    ):
        response = self.client.post("/tv/123/track/", HTTP_HX_REQUEST="true")

        queue_track_show_mock.assert_called_once_with(self.user, "123", provider="tvdb")
        self.assertEqual(response["HX-Redirect"], "/tv/123/")


class ShowRefreshViewTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("user@example.com", password="password")
        self.client.login(username="user@example.com", password="password")

    def test_requires_htmx_header(self):
        response = self.client.post("/tv/123/refresh/")

        self.assertEqual(response.status_code, 403)

    @patch("apps.tv.views.refresh_show")
    def test_post_refreshes_tracked_show(self, refresh_show_mock):
        show = Show.objects.create(external_id="123", name="Foo")
        UserShow.objects.create(user=self.user, show=show, status=UserShow.Status.TRACKED)

        response = self.client.post(
            "/tv/123/refresh/",
            HTTP_HX_REQUEST="true",
        )

        self.assertEqual(response.status_code, 204)
        self.assertEqual(response["HX-Trigger"], "toast")
        refresh_show_mock.assert_called_once_with(self.user, show)

    @patch("apps.tv.views.refresh_show")
    def test_demo_mode_blocks_refresh(self, refresh_show_mock):
        show = Show.objects.create(external_id="123", name="Foo")
        UserShow.objects.create(user=self.user, show=show, status=UserShow.Status.TRACKED)

        with self.settings(DEMO=True):
            response = self.client.post(
                "/tv/123/refresh/",
                HTTP_HX_REQUEST="true",
            )

        self.assertEqual(response.status_code, 403)
        refresh_show_mock.assert_not_called()


class ShowSwitchViewTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("user@example.com", password="password")
        self.client.login(username="user@example.com", password="password")

    @patch("apps.tv.views.queue_switch_show_provider")
    def test_post_switches_show_provider_and_redirects(self, queue_switch_show_provider_mock):
        response = self.client.post(
            "/tv/1399/switch/?provider=tmdb&from_provider=tvdb&from_external_id=121361",
            HTTP_HX_REQUEST="true",
        )

        queue_switch_show_provider_mock.assert_called_once_with(
            self.user,
            source_provider="tvdb",
            source_external_id="121361",
            target_provider="tmdb",
            target_external_id="1399",
        )
        self.assertEqual(response["HX-Redirect"], "/tv/1399/?provider=tmdb")

    @patch("apps.tv.views.queue_switch_show_provider")
    def test_switch_requires_source_state_parameters(self, queue_switch_show_provider_mock):
        response = self.client.post(
            "/tv/1399/switch/?provider=tmdb",
            HTTP_HX_REQUEST="true",
        )

        self.assertEqual(response.status_code, 400)
        queue_switch_show_provider_mock.assert_not_called()

    @patch("apps.tv.views.queue_switch_show_provider")
    def test_demo_mode_blocks_switch(self, queue_switch_show_provider_mock):
        with self.settings(DEMO=True):
            response = self.client.post(
                "/tv/1399/switch/?provider=tmdb&from_provider=tvdb&from_external_id=121361",
                HTTP_HX_REQUEST="true",
            )

        self.assertEqual(response.status_code, 403)
        queue_switch_show_provider_mock.assert_not_called()

    @patch("apps.tv.views.queue_switch_show_provider")
    def test_post_queues_show_provider_switch_without_calling_heavy_service(
        self,
        queue_switch_show_provider_mock,
    ):
        response = self.client.post(
            "/tv/1399/switch/?provider=tmdb&from_provider=tvdb&from_external_id=121361",
            HTTP_HX_REQUEST="true",
        )

        queue_switch_show_provider_mock.assert_called_once_with(
            self.user,
            source_provider="tvdb",
            source_external_id="121361",
            target_provider="tmdb",
            target_external_id="1399",
        )
        self.assertEqual(response["HX-Redirect"], "/tv/1399/?provider=tmdb")


class ShowDropViewTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("user@example.com", password="password")
        self.client.login(username="user@example.com", password="password")

    def test_drops_show_and_redirects(self):
        show = Show.objects.create(external_id="123", name="Foo")
        UserShow.objects.create(user=self.user, show=show, status=UserShow.Status.TRACKED)

        response = self.client.post("/tv/123/drop/", HTTP_HX_REQUEST="true")

        self.assertEqual(response["HX-Redirect"], "/tv/123/")
        user_show = UserShow.objects.get(user=self.user, show=show)
        self.assertEqual(user_show.status, UserShow.Status.DROPPED)


class ShowPauseViewTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("user@example.com", password="password")
        self.client.login(username="user@example.com", password="password")

    def test_pauses_show_and_redirects(self):
        show = Show.objects.create(external_id="123", name="Foo")
        UserShow.objects.create(user=self.user, show=show, status=UserShow.Status.TRACKED)

        response = self.client.post("/tv/123/pause/", HTTP_HX_REQUEST="true")

        self.assertEqual(response["HX-Redirect"], "/tv/123/")
        user_show = UserShow.objects.get(user=self.user, show=show)
        self.assertEqual(user_show.status, UserShow.Status.PAUSED)


class ShowDeleteViewTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("user@example.com", password="password")
        self.client.login(username="user@example.com", password="password")

    def test_deletes_show_data_and_redirects(self):
        show = Show.objects.create(external_id="123", name="Foo")
        season = Season.objects.create(show=show, season_number=1, name="Season 1")
        episode = Episode.objects.create(
            show=show, season=season, season_number=1, episode_number=1, name="Pilot"
        )
        UserShow.objects.create(user=self.user, show=show, status=UserShow.Status.TRACKED)
        UserEpisode.objects.create(user=self.user, episode=episode)

        response = self.client.post("/tv/123/delete/", HTTP_HX_REQUEST="true")

        self.assertEqual(response["HX-Redirect"], "/tv/123/")
        self.assertFalse(UserShow.objects.filter(user=self.user, show=show).exists())
        self.assertFalse(UserEpisode.objects.filter(user=self.user, episode=episode).exists())


class ShowWatchedViewTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("user@example.com", password="password")
        self.client.login(username="user@example.com", password="password")
        self.show = Show.objects.create(external_id="123", name="Foo")
        self.season = Season.objects.create(show=self.show, season_number=1, name="Season 1")
        self.episode = Episode.objects.create(
            show=self.show,
            season=self.season,
            season_number=1,
            episode_number=1,
            name="Pilot",
            air_date=date.today() - timedelta(days=1),
        )

    def test_requires_htmx_header(self):
        response = self.client.post("/tv/123/watched/")
        self.assertEqual(response.status_code, 403)

    def test_post_marks_show_watched_and_redirects(self):
        UserShow.objects.create(user=self.user, show=self.show, status=UserShow.Status.TRACKED)

        response = self.client.post("/tv/123/watched/", HTTP_HX_REQUEST="true")

        self.assertEqual(response["HX-Redirect"], "/tv/123/")
        self.assertTrue(
            UserEpisode.objects.filter(user=self.user, episode=self.episode).exists()
        )

    def test_delete_unmarks_show_watched(self):
        UserShow.objects.create(user=self.user, show=self.show, status=UserShow.Status.TRACKED)
        UserEpisode.objects.create(user=self.user, episode=self.episode)

        response = self.client.delete("/tv/123/watched/", HTTP_HX_REQUEST="true")

        self.assertEqual(response["HX-Redirect"], "/tv/123/")
        self.assertFalse(
            UserEpisode.objects.filter(user=self.user, episode=self.episode).exists()
        )

    def test_post_without_tracking_returns_bad_request(self):
        response = self.client.post("/tv/123/watched/", HTTP_HX_REQUEST="true")

        self.assertEqual(response.status_code, 400)


class SeasonWatchedViewTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("user@example.com", password="password")
        self.client.login(username="user@example.com", password="password")
        self.show = Show.objects.create(external_id="123", name="Foo")
        self.season = Season.objects.create(show=self.show, season_number=1, name="Season 1")
        self.episode = Episode.objects.create(
            show=self.show,
            season=self.season,
            season_number=1,
            episode_number=1,
            name="Pilot",
            air_date=date.today() - timedelta(days=1),
        )
        UserShow.objects.create(user=self.user, show=self.show, status=UserShow.Status.TRACKED)

    def test_requires_htmx_header(self):
        response = self.client.post(f"/tv/123/seasons/{self.season.id}/watched/")
        self.assertEqual(response.status_code, 403)

    def test_post_marks_season_watched_and_returns_season_fragment(self):
        response = self.client.post(
            f"/tv/123/seasons/{self.season.id}/watched/", HTTP_HX_REQUEST="true"
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "1/1")
        self.assertTrue(
            UserEpisode.objects.filter(user=self.user, episode=self.episode).exists()
        )

    def test_delete_unmarks_season_watched(self):
        UserEpisode.objects.create(user=self.user, episode=self.episode)

        response = self.client.delete(
            f"/tv/123/seasons/{self.season.id}/watched/", HTTP_HX_REQUEST="true"
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(
            UserEpisode.objects.filter(user=self.user, episode=self.episode).exists()
        )

    def test_returns_bad_request_when_show_not_tracked(self):
        other_show = Show.objects.create(external_id="456", name="Bar")
        other_season = Season.objects.create(show=other_show, season_number=1, name="Season 1")

        response = self.client.post(
            f"/tv/456/seasons/{other_season.id}/watched/", HTTP_HX_REQUEST="true"
        )

        self.assertEqual(response.status_code, 400)


class EpisodeWatchedViewTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("user@example.com", password="password")
        self.client.login(username="user@example.com", password="password")
        self.show = Show.objects.create(external_id="123", name="Foo")
        self.season = Season.objects.create(show=self.show, season_number=1, name="Season 1")
        self.episode = Episode.objects.create(
            show=self.show,
            season=self.season,
            season_number=1,
            episode_number=1,
            name="Pilot",
            air_date=date.today() - timedelta(days=1),
        )
        UserShow.objects.create(user=self.user, show=self.show, status=UserShow.Status.TRACKED)

    def test_requires_htmx_header(self):
        response = self.client.post(f"/tv/123/episodes/{self.episode.id}/watched/")
        self.assertEqual(response.status_code, 403)

    def test_post_marks_episode_watched_and_returns_season_fragment(self):
        response = self.client.post(
            f"/tv/123/episodes/{self.episode.id}/watched/", HTTP_HX_REQUEST="true"
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "checked")
        self.assertTrue(
            UserEpisode.objects.filter(user=self.user, episode=self.episode).exists()
        )

    def test_delete_unmarks_episode_watched(self):
        UserEpisode.objects.create(user=self.user, episode=self.episode)

        response = self.client.delete(
            f"/tv/123/episodes/{self.episode.id}/watched/", HTTP_HX_REQUEST="true"
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(
            UserEpisode.objects.filter(user=self.user, episode=self.episode).exists()
        )

    def test_returns_bad_request_when_show_not_tracked(self):
        other_show = Show.objects.create(external_id="456", name="Bar")
        other_season = Season.objects.create(show=other_show, season_number=1, name="Season 1")
        other_episode = Episode.objects.create(
            show=other_show, season=other_season, season_number=1, episode_number=1, name="Pilot"
        )

        response = self.client.post(
            f"/tv/456/episodes/{other_episode.id}/watched/", HTTP_HX_REQUEST="true"
        )

        self.assertEqual(response.status_code, 400)
