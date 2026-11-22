from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.catalog.models import SyncStatus
from apps.catalog.providers.base import (
    CastMemberDTO,
    CollectionDTO,
    CollectionPartDTO,
    DetailDTO,
)
from apps.catalog.providers.exceptions import ProviderError
from apps.movies.models import Movie, UserMovie


@override_settings(
    STORAGES={
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {
            "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"
        },
    },
    DJANGO_VITE_DEV_MODE=True,
)
class MovieDetailViewTests(TestCase):
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
        response = self.client.get("/movies/550/")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login/", response["Location"])

    @patch("apps.movies.views._build_movie_context")
    def test_htmx_page_shell_defers_movie_context(self, build_movie_context_mock):
        response = self.client.get(
            "/movies/550/",
            HTTP_HX_REQUEST="true",
        )

        build_movie_context_mock.assert_not_called()
        self.assertContains(response, 'id="movie-content"')
        self.assertContains(response, 'hx-get="/movies/550/content/"')
        self.assertContains(response, 'hx-trigger="load"')
        self.assertNotContains(response, "Fight Club")

    def test_movie_detail_content_requires_htmx(self):
        response = self.client.get("/movies/550/content/")

        self.assertEqual(response.status_code, 403)

    def test_renders_from_db_when_movie_already_imported(self):
        Movie.objects.create(
            external_id="550",
            title="Fight Club",
            last_synced_at=timezone.now(),
            status="Completed",
            normalized_status=Movie.NormalizedStatus.UPCOMING,
            overview="A movie about a fight club.",
            backdrop_path="/backdrop.jpg",
            director="David Fincher",
            trailer_url="https://www.youtube.com/watch?v=SUXWAEX2jlg",
            cast=[{"name": "Edward Norton", "character": "The Narrator", "photo_url": "/norton.jpg"}],
        )

        with self.settings(TMDB_IMAGE_BASE_URL="https://image.tmdb.org/t/p/"):
            response = self.client.get("/movies/550/content/", HTTP_HX_REQUEST="true")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Fight Club")
        self.assertContains(response, "Upcoming")
        self.assertContains(response, 'class="media-status media-status--warning"')
        self.assertContains(response, 'data-lucide="calendar-clock"')
        self.assertNotContains(response, "Completed")
        self.assertContains(response, "A movie about a fight club.")
        self.assertContains(response, 'aria-label="Add to watchlist"')
        self.assertContains(response, 'data-lucide="bookmark"')
        self.assertContains(response, "https://image.tmdb.org/t/p/w1280/backdrop.jpg")
        self.assertContains(response, "David Fincher")
        self.assertContains(response, "https://www.youtube.com/watch?v=SUXWAEX2jlg")
        self.assertContains(response, "Edward Norton")
        self.assertContains(response, "The Narrator")

    def test_renders_extras_slot_from_db(self):
        Movie.objects.create(
            external_id="165",
            title="Back to the Future Part II",
            last_synced_at=timezone.now(),
            mid_credits=True,
            post_credits=True,
            tmdb_collection_id="264",
        )

        response = self.client.get("/movies/165/content/", HTTP_HX_REQUEST="true")

        self.assertContains(
            response, 'hx-get="/movies/165/extras/?mid=1&amp;post=1&amp;collection=264"'
        )
        self.assertContains(response, 'hx-trigger="load, movie-state-changed from:body"')

    def test_omits_extras_slot_when_untagged(self):
        Movie.objects.create(external_id="550", title="Fight Club", last_synced_at=timezone.now())

        response = self.client.get("/movies/550/content/", HTTP_HX_REQUEST="true")

        self.assertNotContains(response, "/extras/")

    def test_detail_fragment_sets_movie_document_title(self):
        Movie.objects.create(external_id="550", title="Fight Club")

        response = self.client.get("/movies/550/content/", HTTP_HX_REQUEST="true")

        self.assertContains(response, "<title>Fight Club :: Movie :: Argus</title>")

    def test_shows_switch_action_when_tracked_on_another_provider(self):
        source = Movie.objects.create(
            provider="tmdb",
            external_id="550",
            tvdb_id="42",
            title="Fight Club",
        )
        Movie.objects.create(
            provider="tvdb",
            external_id="42",
            tmdb_id="550",
            title="Fight Club",
        )
        UserMovie.objects.create(user=self.user, movie=source)

        response = self.client.get("/movies/42/content/?provider=tvdb", HTTP_HX_REQUEST="true")

        self.assertContains(response, "Tracked on another provider")
        self.assertContains(response, 'aria-label="Switch to TVDB"')
        self.assertContains(response, "/movies/42/switch/")
        self.assertNotContains(response, 'aria-label="Add to watchlist"')
        self.assertRegex(
            response.content.decode(),
            r'<div id="movie-actions" class="media-poster-actions"[^>]*>\s*<div class="join media-action-join join-horizontal">\s*<button',
        )

    def test_switch_action_uses_reverse_provider(self):
        source = Movie.objects.create(
            provider="tvdb",
            external_id="42",
            tmdb_id="550",
            title="Fight Club",
        )
        Movie.objects.create(
            provider="tmdb",
            external_id="550",
            tvdb_id="42",
            title="Fight Club",
        )
        UserMovie.objects.create(user=self.user, movie=source)

        response = self.client.get("/movies/550/content/?provider=tmdb", HTTP_HX_REQUEST="true")

        self.assertContains(response, 'aria-label="Switch to TMDB"')

    @patch("apps.movies.views.get_movie_detail")
    def test_refreshes_missing_provider_ids_before_checking_switch_state(
        self,
        get_movie_detail_mock,
    ):
        source = Movie.objects.create(
            provider="tmdb",
            external_id="550",
            imdb_id="tt0137523",
            title="Fight Club",
        )
        Movie.objects.create(provider="tvdb", external_id="42", title="Fight Club")
        UserMovie.objects.create(user=self.user, movie=source)
        get_movie_detail_mock.return_value = DetailDTO(
            provider="tvdb",
            external_id="42",
            title="Fight Club",
            imdb_id="tt0137523",
            tvdb_id="42",
        )

        response = self.client.get("/movies/42/content/?provider=tvdb", HTTP_HX_REQUEST="true")

        self.assertContains(response, 'aria-label="Switch to TVDB"')
        get_movie_detail_mock.assert_called_once_with(
            "42",
            language="eng",
            provider="tvdb",
        )

    def test_does_not_leak_another_users_watchlist_state(self):
        other_user = get_user_model().objects.create_user("other@example.com")
        movie = Movie.objects.create(external_id="550", title="Fight Club")
        UserMovie.objects.create(user=other_user, movie=movie, on_watchlist=True)

        response = self.client.get("/movies/550/content/", HTTP_HX_REQUEST="true")

        self.assertContains(response, 'aria-label="Add to watchlist"')

    @patch("apps.movies.views.get_movie_detail")
    def test_renders_from_provider_cache_when_not_yet_imported(self, get_movie_detail_mock):
        get_movie_detail_mock.return_value = DetailDTO(
            provider="tmdb",
            external_id="603",
            title="The Matrix",
            overview="A hacker learns the truth.",
            director="The Wachowskis",
            trailer_url="https://www.youtube.com/watch?v=vKQi3bta_kk",
            cast=[CastMemberDTO(name="Keanu Reeves", character="Neo", photo_url="/keanu.jpg")],
        )

        response = self.client.get("/movies/603/content/", HTTP_HX_REQUEST="true")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "The Matrix")
        self.assertContains(response, "The Wachowskis")
        self.assertContains(response, "https://www.youtube.com/watch?v=vKQi3bta_kk")
        self.assertContains(response, "Keanu Reeves")
        self.assertFalse(Movie.objects.filter(external_id="603").exists())

    @patch("apps.movies.views.get_movie_detail")
    def test_renders_provider_cache_while_a_freshly_tracked_row_is_unsynced(
        self, get_movie_detail_mock
    ):
        """Tracking writes a placeholder row named after the external id and
        leaves a worker to fill it in. The page must not fall back to that row
        while the worker is still running."""
        get_movie_detail_mock.return_value = DetailDTO(
            provider="tmdb",
            external_id="603",
            title="The Matrix",
            overview="A hacker learns the truth.",
        )
        movie = Movie.objects.create(
            provider="tmdb",
            external_id="603",
            title="603",
            sync_status=SyncStatus.PENDING,
        )
        UserMovie.objects.create(user=self.user, movie=movie, on_watchlist=True)

        response = self.client.get("/movies/603/content/", HTTP_HX_REQUEST="true")

        self.assertContains(response, "The Matrix")
        self.assertContains(response, "A hacker learns the truth.")
        # The user's own state still comes from the row.
        self.assertContains(response, 'aria-label="Remove from watchlist"')
        self.assertNotContains(response, 'aria-label="Add to watchlist"')

    @patch("apps.movies.views.get_movie_detail")
    def test_the_synced_row_wins_over_the_provider_cache(self, get_movie_detail_mock):
        get_movie_detail_mock.return_value = DetailDTO(
            provider="tmdb",
            external_id="603",
            title="Stale Provider Title",
        )
        Movie.objects.create(
            provider="tmdb",
            external_id="603",
            title="The Matrix",
            sync_status=SyncStatus.OK,
            last_synced_at=timezone.now(),
        )

        response = self.client.get("/movies/603/content/", HTTP_HX_REQUEST="true")

        self.assertContains(response, "The Matrix")
        self.assertNotContains(response, "Stale Provider Title")
        get_movie_detail_mock.assert_not_called()

    @patch("apps.movies.views.get_movie_detail")
    def test_renders_normalized_status_for_provider_preview(self, get_movie_detail_mock):
        get_movie_detail_mock.return_value = DetailDTO(
            provider="tmdb",
            external_id="603",
            title="The Matrix",
            status="In Production",
        )

        response = self.client.get("/movies/603/content/", HTTP_HX_REQUEST="true")

        self.assertContains(response, "Upcoming")
        self.assertNotContains(response, "In Production")

    @patch("apps.movies.views.get_movie_detail")
    def test_preview_uses_requested_provider_and_language(self, get_movie_detail_mock):
        get_movie_detail_mock.return_value = DetailDTO(
            provider="tvdb",
            external_id="42",
            title="A Movie",
        )
        self.user.settings.tvdb_metadata_language = "eng"
        self.user.settings.save()

        response = self.client.get("/movies/42/content/?provider=tvdb", HTTP_HX_REQUEST="true")

        self.assertEqual(response.status_code, 200)
        get_movie_detail_mock.assert_called_once_with(
            "42",
            language="eng",
            provider="tvdb",
        )


class MovieExtrasViewTests(TestCase):
    URL = "/movies/165/extras/?mid=0&post=0&collection=264"

    def setUp(self):
        self.user = get_user_model().objects.create_user("user@example.com", password="password")
        self.client.login(username="user@example.com", password="password")

    @patch("apps.movies.views.get_movie_collection")
    def test_marks_seen_tracked_current_and_upcoming_parts(self, get_collection_mock):
        get_collection_mock.return_value = CollectionDTO(
            name="Back to the Future Collection",
            parts=[
                CollectionPartDTO(tmdb_id="105", title="Back to the Future", release_date="1985-07-03"),
                CollectionPartDTO(tmdb_id="165", title="Back to the Future Part II", release_date="1989-11-22"),
                CollectionPartDTO(tmdb_id="196", title="Back to the Future Part III", release_date="1990-05-25"),
                CollectionPartDTO(tmdb_id="999", title="Back to the Future Part IV", release_date="2999-01-01"),
            ],
        )
        seen = Movie.objects.create(provider="tmdb", external_id="105", title="Back to the Future")
        UserMovie.objects.create(user=self.user, movie=seen, is_seen=True)
        tracked = Movie.objects.create(provider="tmdb", external_id="196", title="Part III")
        UserMovie.objects.create(user=self.user, movie=tracked, on_watchlist=True)

        response = self.client.get(self.URL, HTTP_HX_REQUEST="true")
        parts = response.context["parts"]

        self.assertEqual(
            [(p["is_seen"], p["is_tracked"], p["is_current"], p["is_released"]) for p in parts],
            [(True, True, False, True), (False, False, True, True),
             (False, True, False, True), (False, False, False, False)],
        )
        self.assertContains(response, 'data-lucide="eye"', count=1)
        self.assertContains(response, 'data-lucide="bookmark"', count=1)
        self.assertContains(response, "1 of 3 watched")
        self.assertContains(response, "Coming 2999")
        self.assertContains(response, 'href="/movies/105/"')
        self.assertNotContains(response, 'href="/movies/165/"')

    @patch("apps.movies.views.get_movie_collection")
    def test_renders_nothing_for_single_part_collection(self, get_collection_mock):
        get_collection_mock.return_value = CollectionDTO(
            name="Solo", parts=[CollectionPartDTO(tmdb_id="165", title="Solo")],
        )

        response = self.client.get(self.URL, HTTP_HX_REQUEST="true")

        self.assertEqual(response.content, b"")

    @patch("apps.movies.views.get_movie_collection", side_effect=ProviderError("down"))
    def test_renders_nothing_when_tmdb_is_unreachable(self, _get_collection_mock):
        response = self.client.get(self.URL, HTTP_HX_REQUEST="true")

        self.assertEqual(response.content, b"")

    @patch("apps.movies.views.get_movie_collection", side_effect=ProviderError("down"))
    def test_credits_still_render_when_collection_fails(self, _get_collection_mock):
        response = self.client.get(
            "/movies/165/extras/?mid=1&post=1&collection=264", HTTP_HX_REQUEST="true"
        )

        self.assertContains(response, "a scene during the credits and another one after them")
        self.assertNotContains(response, "franchise-rail")

    @patch("apps.movies.views.get_movie_collection")
    def test_post_credits_only_skips_tmdb(self, get_collection_mock):
        response = self.client.get("/movies/165/extras/?mid=0&post=1", HTTP_HX_REQUEST="true")

        self.assertContains(response, "a scene after the credits.")
        get_collection_mock.assert_not_called()

    def test_renders_nothing_without_credits_or_collection(self):
        response = self.client.get("/movies/165/extras/?mid=0&post=0", HTTP_HX_REQUEST="true")

        self.assertEqual(response.content, b"")


class MovieTrackViewTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("user@example.com", password="password")
        self.client.login(username="user@example.com", password="password")

    def test_requires_htmx_header(self):
        response = self.client.post("/movies/550/track/")
        self.assertEqual(response.status_code, 403)

    @patch("apps.movies.views.queue_track_movie")
    def test_post_tracks_movie(self, queue_track_movie_mock):
        movie = Movie.objects.create(external_id="550", title="Fight Club")
        queue_track_movie_mock.return_value = UserMovie.objects.create(
            user=self.user, movie=movie, on_watchlist=True
        )

        response = self.client.post("/movies/550/track/", HTTP_HX_REQUEST="true")

        queue_track_movie_mock.assert_called_once_with(self.user, "tmdb", "550")
        self.assertContains(response, 'aria-label="Movie actions"')
        self.assertContains(response, 'aria-label="Remove from watchlist"')

    @patch("apps.movies.views.queue_track_movie")
    def test_post_tracks_movie_with_requested_provider(self, queue_track_movie_mock):
        movie = Movie.objects.create(provider="tvdb", external_id="42", title="A Movie")
        queue_track_movie_mock.return_value = UserMovie.objects.create(
            user=self.user,
            movie=movie,
            on_watchlist=True,
        )

        self.client.post(
            "/movies/42/track/?provider=tvdb",
            HTTP_HX_REQUEST="true",
        )

        queue_track_movie_mock.assert_called_once_with(self.user, "tvdb", "42")

    def test_delete_untracks_movie(self):
        movie = Movie.objects.create(external_id="550", title="Fight Club")
        UserMovie.objects.create(user=self.user, movie=movie, on_watchlist=True)

        response = self.client.delete("/movies/550/track/", HTTP_HX_REQUEST="true")

        self.assertContains(response, 'aria-label="Add to watchlist"')
        # Tells the credits and franchise card to reload its dots.
        self.assertEqual(response["HX-Trigger"], "movie-state-changed")
        self.assertFalse(
            UserMovie.objects.filter(user=self.user, movie=movie, on_watchlist=True).exists()
        )

    @patch("apps.movies.views.queue_track_movie")
    def test_demo_mode_blocks_non_superusers(self, queue_track_movie_mock):
        with self.settings(DEMO=True):
            response = self.client.post("/movies/550/track/", HTTP_HX_REQUEST="true")

        self.assertEqual(response.status_code, 403)
        queue_track_movie_mock.assert_not_called()

class MovieSwitchViewTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("user@example.com", password="password")
        self.client.login(username="user@example.com", password="password")

    @patch("apps.movies.views.switch_movie_provider")
    def test_post_switches_movie_provider_and_redirects(self, switch_movie_provider_mock):
        response = self.client.post(
            "/movies/42/switch/?provider=tvdb&from_provider=tmdb&from_external_id=550",
            HTTP_HX_REQUEST="true",
        )

        switch_movie_provider_mock.assert_called_once_with(
            self.user,
            source_provider="tmdb",
            source_external_id="550",
            target_provider="tvdb",
            target_external_id="42",
        )
        self.assertEqual(response["HX-Redirect"], "/movies/42/?provider=tvdb")

    @patch("apps.movies.views.switch_movie_provider")
    def test_switch_requires_source_state_parameters(self, switch_movie_provider_mock):
        response = self.client.post(
            "/movies/42/switch/?provider=tvdb",
            HTTP_HX_REQUEST="true",
        )

        self.assertEqual(response.status_code, 400)
        switch_movie_provider_mock.assert_not_called()

    @patch("apps.movies.views.switch_movie_provider")
    def test_demo_mode_blocks_switch(self, switch_movie_provider_mock):
        with self.settings(DEMO=True):
            response = self.client.post(
                "/movies/42/switch/?provider=tvdb&from_provider=tmdb&from_external_id=550",
                HTTP_HX_REQUEST="true",
            )

        self.assertEqual(response.status_code, 403)
        switch_movie_provider_mock.assert_not_called()

class MovieWatchedViewTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("user@example.com", password="password")
        self.client.login(username="user@example.com", password="password")

    def test_requires_htmx_header(self):
        response = self.client.post("/movies/550/watched/")
        self.assertEqual(response.status_code, 403)

    @patch("apps.movies.views.refresh_movie")
    @patch("apps.movies.views.import_movie", create=True)
    def test_post_marks_watched_without_refreshing_synced_movie_metadata(
        self,
        import_movie_mock,
        refresh_movie_mock,
    ):
        movie = Movie.objects.create(
            external_id="550",
            title="Fight Club",
            sync_status=SyncStatus.OK,
            last_synced_at=timezone.now(),
        )
        import_movie_mock.return_value = movie

        response = self.client.post("/movies/550/watched/", HTTP_HX_REQUEST="true")

        import_movie_mock.assert_not_called()
        refresh_movie_mock.assert_not_called()
        self.assertContains(response, 'aria-label="Movie actions"')
        self.assertContains(response, 'aria-label="Mark unwatched"')
        self.assertContains(response, 'aria-label="Delete movie"')
        self.assertTrue(
            UserMovie.objects.filter(user=self.user, movie=movie, is_seen=True).exists()
        )

    @patch("apps.movies.views.refresh_movie")
    @patch("apps.movies.views.import_movie", create=True)
    def test_post_creates_placeholder_and_queues_metadata_refresh_for_missing_movie(
        self,
        import_movie_mock,
        refresh_movie_mock,
    ):
        import_movie_mock.side_effect = AssertionError(
            "watched actions must not synchronously import metadata"
        )

        response = self.client.post("/movies/550/watched/", HTTP_HX_REQUEST="true")

        movie = Movie.objects.get(provider="tmdb", external_id="550")
        self.assertEqual(movie.title, "550")
        self.assertEqual(movie.sync_status, SyncStatus.PENDING)
        refresh_movie_mock.assert_called_once_with(self.user, movie)
        self.assertTrue(
            UserMovie.objects.filter(user=self.user, movie=movie, is_seen=True).exists()
        )
        self.assertContains(response, 'aria-label="Mark unwatched"')

    def test_delete_marks_unwatched(self):
        movie = Movie.objects.create(external_id="550", title="Fight Club")
        UserMovie.objects.create(user=self.user, movie=movie, is_seen=True)

        response = self.client.delete("/movies/550/watched/", HTTP_HX_REQUEST="true")

        self.assertContains(response, 'aria-label="Movie actions"')
        self.assertContains(response, 'aria-label="Mark watched"')
        self.assertContains(response, 'aria-label="Remove from watchlist"')
        user_movie = UserMovie.objects.get(user=self.user, movie=movie)
        self.assertFalse(user_movie.is_seen)
        self.assertTrue(user_movie.on_watchlist)


class MoviePosterWatchlistRemoveViewTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("user@example.com", password="password")
        self.client.login(username="user@example.com", password="password")

    def test_requires_htmx_header(self):
        response = self.client.delete("/movies/550/watchlist/poster/")

        self.assertEqual(response.status_code, 403)

    def test_delete_removes_movie_from_watchlist(self):
        movie = Movie.objects.create(external_id="550", title="Fight Club")
        UserMovie.objects.create(user=self.user, movie=movie, on_watchlist=True)

        response = self.client.delete(
            "/movies/550/watchlist/poster/", HTTP_HX_REQUEST="true"
        )

        # htmx 2 does not swap on 204 responses; an empty 200 lets hx-swap="delete" run.
        self.assertEqual(response.status_code, 200)
        self.assertFalse(
            UserMovie.objects.filter(
                user=self.user, movie=movie, on_watchlist=True
            ).exists()
        )

    def test_delete_keeps_seen_state_of_watched_movie(self):
        movie = Movie.objects.create(external_id="550", title="Fight Club")
        UserMovie.objects.create(user=self.user, movie=movie, on_watchlist=True, is_seen=True)

        response = self.client.delete(
            "/movies/550/watchlist/poster/", HTTP_HX_REQUEST="true"
        )

        self.assertEqual(response.status_code, 200)
        user_movie = UserMovie.objects.get(user=self.user, movie=movie)
        self.assertFalse(user_movie.on_watchlist)
        self.assertTrue(user_movie.is_seen)

    def test_delete_is_noop_for_unknown_movie(self):
        response = self.client.delete(
            "/movies/999/watchlist/poster/", HTTP_HX_REQUEST="true"
        )

        self.assertEqual(response.status_code, 200)

    @patch("apps.movies.views.remove_from_watchlist")
    def test_demo_mode_blocks_non_superusers(self, remove_from_watchlist_mock):
        with self.settings(DEMO=True):
            response = self.client.delete(
                "/movies/550/watchlist/poster/", HTTP_HX_REQUEST="true"
            )

        self.assertEqual(response.status_code, 403)
        remove_from_watchlist_mock.assert_not_called()


@override_settings(
    STORAGES={
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {
            "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"
        },
    },
    DJANGO_VITE_DEV_MODE=True,
)
class MovieActionCardViewTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            "user@example.com", password="password"
        )
        self.client.login(username="user@example.com", password="password")

    def test_untracked_movie_renders_single_action_card_button(self):
        Movie.objects.create(external_id="550", title="Fight Club")

        response = self.client.get("/movies/550/content/", HTTP_HX_REQUEST="true")

        self.assertContains(response, 'id="movie-actions"')
        self.assertContains(response, 'class="media-poster-actions"')
        self.assertContains(response, 'aria-label="Add to watchlist"')
        self.assertContains(response, 'class="join media-action-join join-horizontal"')
        self.assertContains(
            response,
            'class="btn btn-sm join-item media-action-button btn-primary"',
        )
        self.assertContains(response, 'data-tippy-content="Add to watchlist"')
        self.assertNotContains(response, 'aria-label="Refresh metadata"')
        self.assertContains(response, 'aria-label="Movie actions"')

    def test_actions_are_attached_to_the_movie_poster_in_a_card(self):
        Movie.objects.create(
            external_id="550", title="Fight Club", last_synced_at=timezone.now()
        )

        response = self.client.get("/movies/550/content/", HTTP_HX_REQUEST="true")
        content = response.content.decode()

        poster_stack = content.index('<div class="media-poster-stack">')
        actions_card = content.index(
            '<div id="movie-actions" class="media-poster-actions"'
        )
        details = content.index('<div class="min-w-0 space-y-6">')

        self.assertLess(poster_stack, actions_card)
        self.assertLess(actions_card, details)
        self.assertContains(response, 'data-tippy-content="Add to watchlist"')
        self.assertRegex(
            content,
            r'<div class="join media-action-join join-horizontal">(?s:.*?)'
            r'<button[^>]+aria-label="Edit artwork and language"',
        )
        self.assertNotContains(response, 'class="fab"')
        self.assertNotContains(response, 'class="tooltip')
        self.assertNotContains(response, "data-tip=")
        self.assertNotContains(response, "btn-outline")
        self.assertNotContains(response, "btn-circle")

    def test_watchlisted_movie_renders_action_card(self):
        movie = Movie.objects.create(external_id="550", title="Fight Club")
        UserMovie.objects.create(user=self.user, movie=movie, on_watchlist=True)

        response = self.client.get("/movies/550/content/", HTTP_HX_REQUEST="true")

        self.assertContains(response, 'aria-label="Movie actions"')
        self.assertContains(response, 'aria-label="Mark watched"')
        self.assertContains(response, 'aria-label="Remove from watchlist"')
        self.assertContains(response, 'aria-label="Refresh metadata"')
        self.assertContains(response, "/movies/550/refresh/")
        self.assertContains(response, 'hx-swap="none"')
        self.assertNotContains(response, 'aria-label="Add to watchlist"')

    def test_watched_movie_renders_action_card(self):
        movie = Movie.objects.create(external_id="550", title="Fight Club")
        UserMovie.objects.create(user=self.user, movie=movie, is_seen=True)

        response = self.client.get("/movies/550/content/", HTTP_HX_REQUEST="true")

        self.assertContains(response, 'aria-label="Movie actions"')
        self.assertContains(response, 'aria-label="Mark unwatched"')
        self.assertContains(response, 'aria-label="Delete movie"')
        self.assertContains(response, 'aria-label="Refresh metadata"')
        self.assertNotContains(response, 'aria-label="Mark watched"')


class MovieRefreshViewTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            "user@example.com", password="password"
        )
        self.client.login(username="user@example.com", password="password")

    def test_requires_htmx_header(self):
        response = self.client.post("/movies/550/refresh/")

        self.assertEqual(response.status_code, 403)

    @patch("apps.movies.views.refresh_movie")
    def test_post_refreshes_tracked_movie(self, refresh_movie_mock):
        movie = Movie.objects.create(external_id="550", title="Fight Club")
        UserMovie.objects.create(user=self.user, movie=movie, on_watchlist=True)

        response = self.client.post(
            "/movies/550/refresh/",
            HTTP_HX_REQUEST="true",
        )

        self.assertEqual(response.status_code, 204)
        self.assertEqual(response["HX-Trigger"], "toast")
        refresh_movie_mock.assert_called_once_with(self.user, movie)

    @patch("apps.movies.views.refresh_movie")
    def test_demo_mode_blocks_refresh(self, refresh_movie_mock):
        movie = Movie.objects.create(external_id="550", title="Fight Club")
        UserMovie.objects.create(user=self.user, movie=movie, on_watchlist=True)

        with self.settings(DEMO=True):
            response = self.client.post(
                "/movies/550/refresh/",
                HTTP_HX_REQUEST="true",
            )

        self.assertEqual(response.status_code, 403)
        refresh_movie_mock.assert_not_called()


class MovieDeleteViewTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            "user@example.com", password="password"
        )
        self.client.login(username="user@example.com", password="password")

    def test_requires_htmx_header(self):
        response = self.client.post("/movies/550/delete/")

        self.assertEqual(response.status_code, 403)

    def test_deletes_only_current_users_movie_state(self):
        other_user = get_user_model().objects.create_user("other@example.com")
        movie = Movie.objects.create(external_id="550", title="Fight Club")
        UserMovie.objects.create(user=self.user, movie=movie, is_seen=True)
        UserMovie.objects.create(user=other_user, movie=movie, is_seen=True)

        response = self.client.post(
            "/movies/550/delete/", HTTP_HX_REQUEST="true"
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(UserMovie.objects.filter(user=self.user, movie=movie).exists())
        self.assertTrue(UserMovie.objects.filter(user=other_user, movie=movie).exists())

    def test_demo_mode_blocks_non_superusers(self):
        movie = Movie.objects.create(external_id="550", title="Fight Club")
        UserMovie.objects.create(user=self.user, movie=movie, is_seen=True)

        with self.settings(DEMO=True):
            response = self.client.post(
                "/movies/550/delete/", HTTP_HX_REQUEST="true"
            )

        self.assertEqual(response.status_code, 403)
        self.assertTrue(UserMovie.objects.filter(user=self.user, movie=movie).exists())
