from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from apps.history.services import get_history_page
from apps.movies.models import Movie, UserMovie
from apps.tv.models import Episode, Season, Show, UserEpisode


class HistoryServiceTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("user@example.com")

    def test_get_history_page_merges_media_types_newest_first_and_scopes_user(self):
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
        other_movie = Movie.objects.create(external_id="other", title="Other user")

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
        UserMovie.objects.create(
            user=get_user_model().objects.create_user("other@example.com"),
            movie=other_movie,
            is_seen=True,
            seen_at=now + timedelta(days=1),
        )

        page = get_history_page(self.user, 1)

        self.assertEqual(page.paginator.count, 2)
        self.assertEqual(
            [entry.kind for entry in page.object_list],
            ["movie", "episode"],
        )
        self.assertEqual(page.object_list[0].record.movie, movie)
        self.assertEqual(page.object_list[1].record.episode, episode)

    def test_get_history_page_limits_results_to_25_and_exposes_next_page(self):
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

        first_page = get_history_page(self.user, 1)
        second_page = get_history_page(self.user, 2)

        self.assertEqual(len(first_page.object_list), 25)
        self.assertTrue(first_page.has_next())
        self.assertEqual(len(second_page.object_list), 1)
        self.assertFalse(second_page.has_next())

    def test_get_history_page_does_not_skip_items_when_media_types_are_uneven(self):
        now = timezone.now()
        show = Show.objects.create(external_id="show", name="A show")
        season = Season.objects.create(show=show, season_number=1, name="Season 1")
        episode = Episode.objects.create(
            show=show,
            season=season,
            season_number=1,
            episode_number=1,
            name="Newest episode",
        )
        UserEpisode.objects.create(user=self.user, episode=episode, seen_at=now)

        for index in range(25):
            movie = Movie.objects.create(
                external_id=f"movie-{index}",
                title=f"Movie {index}",
            )
            UserMovie.objects.create(
                user=self.user,
                movie=movie,
                is_seen=True,
                seen_at=now - timedelta(minutes=index + 1),
            )

        second_page = get_history_page(self.user, 2)

        self.assertEqual(len(second_page.object_list), 1)
        self.assertEqual(second_page.object_list[0].record.movie.external_id, "movie-24")
