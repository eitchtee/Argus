from datetime import date, timedelta
from unittest.mock import patch

from cachalot.api import cachalot_disabled
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

    @patch("apps.tv.views.get_show_episodes")
    @patch("apps.tv.views.get_show_detail")
    def test_renders_preview_from_provider_cache_when_not_imported(
        self, get_show_detail_mock, get_show_episodes_mock
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
            airs_schedule="Sundays at 9:00 PM",
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

        response = self.client.get("/tv/123/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Foo")
        self.assertContains(response, "Pilot")
        self.assertContains(response, "https://artworks.thetvdb.com/fanart.jpg")
        self.assertContains(response, "Emilia Clarke")
        self.assertNotContains(response, "checkbox-sm")
        self.assertFalse(Show.objects.filter(external_id="123").exists())

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
            airs_schedule="Sundays at 9:00 PM",
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
        UserShow.objects.create(user=other_user, show=show, is_tracking=True)

        response = self.client.get("/tv/123/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Foo")
        self.assertContains(response, "Pilot")
        self.assertContains(response, "https://artworks.thetvdb.com/fanart.jpg")
        self.assertContains(response, "tt0944947")
        self.assertContains(response, "https://www.youtube.com/watch?v=abc123")
        self.assertContains(response, "57")
        self.assertContains(response, "Sundays at 9:00 PM")
        self.assertContains(response, "Emilia Clarke")
        self.assertContains(response, "Daenerys Targaryen")
        # Current user has not tracked it themselves: no checkboxes, no watched state.
        self.assertNotContains(response, "checkbox-sm")

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
        UserShow.objects.create(user=self.user, show=show, is_tracking=True)
        UserEpisode.objects.create(user=self.user, episode=episode)

        response = self.client.get("/tv/123/")

        self.assertContains(response, "checkbox-sm")
        self.assertContains(response, "checked")
        self.assertContains(response, ">Drop show<")
        self.assertContains(response, f"/tv/123/episodes/{episode.id}/\"")

    def test_shows_track_button_when_show_exists_but_user_not_tracking(self):
        other_user = get_user_model().objects.create_user("other@example.com")
        show = Show.objects.create(external_id="123", name="Foo")
        UserShow.objects.create(user=other_user, show=show, is_tracking=True)

        response = self.client.get("/tv/123/")

        self.assertContains(response, ">Track<")

    def test_shows_delete_button_after_drop_but_not_before_any_tracking(self):
        show = Show.objects.create(external_id="123", name="Foo")

        response_never_tracked = self.client.get("/tv/123/")
        self.assertNotContains(response_never_tracked, ">Delete show<")

        UserShow.objects.create(user=self.user, show=show, is_tracking=False)
        response_dropped = self.client.get("/tv/123/")
        self.assertContains(response_dropped, ">Delete show<")


class ShowTrackViewTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("user@example.com", password="password")
        self.client.login(username="user@example.com", password="password")

    def test_requires_htmx_header(self):
        response = self.client.post("/tv/123/track/")
        self.assertEqual(response.status_code, 403)

    @patch("apps.tv.views.track_show")
    def test_post_tracks_show_and_redirects(self, track_show_mock):
        response = self.client.post("/tv/123/track/", HTTP_HX_REQUEST="true")

        track_show_mock.assert_called_once_with(self.user, "123")
        self.assertEqual(response["HX-Redirect"], "/tv/123/")

    @patch("apps.tv.views.track_show")
    def test_demo_mode_blocks_non_superusers(self, track_show_mock):
        with self.settings(DEMO=True):
            response = self.client.post("/tv/123/track/", HTTP_HX_REQUEST="true")

        self.assertEqual(response.status_code, 403)
        track_show_mock.assert_not_called()


class ShowDropViewTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("user@example.com", password="password")
        self.client.login(username="user@example.com", password="password")

    def test_drops_show_and_redirects(self):
        show = Show.objects.create(external_id="123", name="Foo")
        UserShow.objects.create(user=self.user, show=show, is_tracking=True)

        response = self.client.post("/tv/123/drop/", HTTP_HX_REQUEST="true")

        self.assertEqual(response["HX-Redirect"], "/tv/123/")
        # cachalot's per-transaction cache layer doesn't invalidate save(update_fields=[...])
        # partial updates the way it does inserts/deletes/full saves, so a plain re-query here
        # can read a stale pre-update row within this still-open TestCase transaction (verified
        # via raw SQL that the real row is correctly updated) — bypass the cache for this read.
        with cachalot_disabled():
            user_show = UserShow.objects.get(user=self.user, show=show)
        self.assertFalse(user_show.is_tracking)


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
        UserShow.objects.create(user=self.user, show=show, is_tracking=True)
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
        UserShow.objects.create(user=self.user, show=self.show, is_tracking=True)

        response = self.client.post("/tv/123/watched/", HTTP_HX_REQUEST="true")

        self.assertEqual(response["HX-Redirect"], "/tv/123/")
        self.assertTrue(
            UserEpisode.objects.filter(user=self.user, episode=self.episode).exists()
        )

    def test_delete_unmarks_show_watched(self):
        UserShow.objects.create(user=self.user, show=self.show, is_tracking=True)
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
        UserShow.objects.create(user=self.user, show=self.show, is_tracking=True)

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
        UserShow.objects.create(user=self.user, show=self.show, is_tracking=True)

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
