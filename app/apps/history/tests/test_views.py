from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.movies.models import Movie, UserMovie
from apps.tv.models import Episode, Season, Show, UserEpisode


@override_settings(
    STORAGES={
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {
            "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"
        },
    },
    DJANGO_VITE_DEV_MODE=True,
)
class HistoryViewTests(TestCase):
    def setUp(self):
        from django_vite.core.asset_loader import DjangoViteAssetLoader

        DjangoViteAssetLoader._instance = None
        self.user = get_user_model().objects.create_user(
            "user@example.com",
            password="password",
        )
        self.client.login(username="user@example.com", password="password")

    def tearDown(self):
        from django_vite.core.asset_loader import DjangoViteAssetLoader

        DjangoViteAssetLoader._instance = None

    def test_requires_authentication(self):
        self.client.logout()

        response = self.client.get(reverse("history-page"))

        self.assertEqual(response.status_code, 302)
        self.assertIn("/login/", response["Location"])

    def test_page_shell_defers_history_content(self):
        response = self.client.get(reverse("history-page"))

        self.assertContains(response, 'id="history-content"')
        self.assertContains(response, 'hx-get="/history/"')
        self.assertContains(response, 'hx-trigger="load"')
        self.assertNotContains(response, "Newest movie")

    def test_fragment_renders_movies_and_episodes_newest_first(self):
        now = timezone.now()
        movie = Movie.objects.create(external_id="movie", title="Newest movie")
        show = Show.objects.create(external_id="show", name="A show")
        season = Season.objects.create(show=show, season_number=1, name="Season 1")
        episode = Episode.objects.create(
            show=show,
            season=season,
            season_number=1,
            episode_number=1,
            name="Older episode",
        )
        UserMovie.objects.create(
            user=self.user,
            movie=movie,
            is_seen=True,
            seen_at=now,
        )
        UserEpisode.objects.create(
            user=self.user,
            episode=episode,
            seen_at=now - timedelta(hours=1),
        )

        response = self.client.get(reverse("history-page"), HTTP_HX_REQUEST="true")
        content = response.content.decode()

        self.assertContains(response, "Newest movie")
        self.assertContains(response, "A show")
        self.assertContains(response, "Older episode")
        self.assertContains(response, "Movie")
        self.assertContains(response, "Episode")
        self.assertLess(content.index("Newest movie"), content.index("Older episode"))
        self.assertContains(
            response,
            reverse("movie-detail", kwargs={"external_id": "movie"}),
        )
        self.assertContains(
            response,
            reverse(
                "tv-episode-detail",
                kwargs={"external_id": "show", "episode_id": episode.id},
            ),
        )

    def test_fragment_limits_history_to_25_entries_and_links_next_page(self):
        for index in range(26):
            movie = Movie.objects.create(
                external_id=str(index),
                title=f"Movie {index}",
            )
            UserMovie.objects.create(
                user=self.user,
                movie=movie,
                is_seen=True,
                seen_at=timezone.now() - timedelta(minutes=index),
            )

        response = self.client.get(reverse("history-page"), HTTP_HX_REQUEST="true")

        self.assertEqual(response.content.decode().count('data-history-entry="'), 25)
        self.assertContains(response, 'href="?page=2"')

    def test_fragment_renders_numbered_pagination_with_boundary_controls(self):
        for index in range(151):
            movie = Movie.objects.create(
                external_id=str(index),
                title=f"Movie {index}",
            )
            UserMovie.objects.create(
                user=self.user,
                movie=movie,
                is_seen=True,
                seen_at=timezone.now() - timedelta(minutes=index),
            )

        response = self.client.get(
            reverse("history-page") + "?page=5",
            HTTP_HX_REQUEST="true",
        )

        self.assertContains(response, 'aria-label="First history page"')
        self.assertContains(response, 'href="?page=1"')
        self.assertContains(response, 'aria-label="Previous history page"')
        self.assertContains(response, 'href="?page=4"')
        self.assertContains(response, 'aria-current="page"')
        self.assertContains(response, 'href="?page=3"')
        self.assertContains(response, 'href="?page=5"')
        self.assertContains(response, 'href="?page=6"')
        self.assertContains(response, 'href="?page=7"')
        self.assertContains(response, 'aria-label="Next history page"')
        self.assertContains(response, 'aria-label="Last history page"')

    def test_fragment_uses_a_red_x_for_history_undo(self):
        movie = Movie.objects.create(external_id="movie", title="Movie")
        UserMovie.objects.create(user=self.user, movie=movie, is_seen=True)

        response = self.client.get(reverse("history-page"), HTTP_HX_REQUEST="true")

        self.assertContains(response, "fa-xmark")
        self.assertContains(response, "text-error")
        self.assertNotContains(response, "fa-rotate-left")

    def test_empty_history_renders_empty_state(self):
        response = self.client.get(reverse("history-page"), HTTP_HX_REQUEST="true")

        self.assertContains(response, "No watch history yet.")

    def test_movie_undo_marks_only_current_users_movie_unwatched(self):
        movie = Movie.objects.create(external_id="movie", title="Movie")
        UserMovie.objects.create(user=self.user, movie=movie, is_seen=True)

        response = self.client.delete(
            reverse("history-undo-movie", kwargs={"movie_id": movie.id}) + "?page=1",
            HTTP_HX_REQUEST="true",
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(
            UserMovie.objects.get(user=self.user, movie=movie).is_seen
        )
        self.assertNotContains(response, "Movie")

    def test_episode_undo_removes_current_users_episode_history(self):
        show = Show.objects.create(external_id="show", name="A show")
        season = Season.objects.create(show=show, season_number=1, name="Season 1")
        episode = Episode.objects.create(
            show=show,
            season=season,
            season_number=1,
            episode_number=1,
            name="Pilot",
        )
        UserEpisode.objects.create(user=self.user, episode=episode)

        response = self.client.delete(
            reverse("history-undo-episode", kwargs={"episode_id": episode.id})
            + "?page=1",
            HTTP_HX_REQUEST="true",
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(
            UserEpisode.objects.filter(
                user=self.user,
                episode=episode,
            ).exists()
        )
        self.assertNotContains(response, episode.name)

    def test_undo_requires_htmx(self):
        movie = Movie.objects.create(external_id="movie", title="Movie")
        UserMovie.objects.create(user=self.user, movie=movie, is_seen=True)

        response = self.client.delete(
            reverse("history-undo-movie", kwargs={"movie_id": movie.id})
        )

        self.assertEqual(response.status_code, 403)
        self.assertTrue(
            UserMovie.objects.filter(
                user=self.user,
                movie=movie,
                is_seen=True,
            ).exists()
        )

    def test_movie_undo_is_scoped_to_current_user(self):
        movie = Movie.objects.create(external_id="movie", title="Movie")
        other_user = get_user_model().objects.create_user("other@example.com")
        UserMovie.objects.create(user=other_user, movie=movie, is_seen=True)

        response = self.client.delete(
            reverse("history-undo-movie", kwargs={"movie_id": movie.id}),
            HTTP_HX_REQUEST="true",
        )

        self.assertEqual(response.status_code, 404)
        self.assertTrue(
            UserMovie.objects.filter(
                user=other_user,
                movie=movie,
                is_seen=True,
            ).exists()
        )

    def test_demo_mode_blocks_history_undo_for_non_superusers(self):
        movie = Movie.objects.create(external_id="movie", title="Movie")
        UserMovie.objects.create(user=self.user, movie=movie, is_seen=True)

        with self.settings(DEMO=True):
            response = self.client.delete(
                reverse("history-undo-movie", kwargs={"movie_id": movie.id}),
                HTTP_HX_REQUEST="true",
            )

        self.assertEqual(response.status_code, 403)
        self.assertTrue(
            UserMovie.objects.filter(
                user=self.user,
                movie=movie,
                is_seen=True,
            ).exists()
        )
