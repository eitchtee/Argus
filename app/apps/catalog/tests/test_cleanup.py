from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.test import TestCase
from django.utils import timezone

from apps.catalog.cleanup import remove_unused_titles
from apps.catalog.models import MediaArtwork, MediaRating, UserMediaArtworkPreference
from apps.movies.models import Movie, UserMovie
from apps.simkl.models import SimklMediaInfo
from apps.tv.models import Episode, Season, Show, UserEpisode, UserShow


class RemoveUnusedTitlesTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("user@example.com", password="password")
        self.counter = 0

    def movie(self):
        self.counter += 1
        return Movie.objects.create(external_id=str(self.counter), title=f"Movie {self.counter}")

    def show(self):
        self.counter += 1
        show = Show.objects.create(external_id=str(self.counter), name=f"Show {self.counter}")
        season = Season.objects.create(show=show, season_number=1)
        Episode.objects.create(show=show, season=season, season_number=1, episode_number=1)
        return show

    def age(self, *objects, days=8):
        stamp = timezone.now() - timedelta(days=days)
        for obj in objects:
            type(obj).objects.filter(pk=obj.pk).update(updated_at=stamp)

    def rate(self, obj, media_type):
        MediaRating.objects.create(
            user=self.user,
            media_type=media_type,
            content_type=ContentType.objects.get_for_model(obj),
            object_id=obj.pk,
            score=Decimal("4.0"),
        )

    def test_titles_with_user_data_are_kept(self):
        tracked_movie = self.movie()
        UserMovie.objects.create(user=self.user, movie=tracked_movie, on_watchlist=True)
        rated_movie = self.movie()
        self.rate(rated_movie, MediaRating.MediaType.MOVIE)
        customised_movie = self.movie()
        UserMediaArtworkPreference.objects.create(
            user=self.user,
            provider=customised_movie.provider,
            media_type=MediaArtwork.MediaType.MOVIE,
            external_id=customised_movie.external_id,
        )
        tracked_show = self.show()
        UserShow.objects.create(user=self.user, show=tracked_show)
        watched_show = self.show()
        UserEpisode.objects.create(user=self.user, episode=watched_show.episodes.get())
        rated_show = self.show()
        self.rate(rated_show, MediaRating.MediaType.SHOW)
        rated_episode_show = self.show()
        self.rate(rated_episode_show.episodes.get(), MediaRating.MediaType.EPISODE)
        kept = [
            tracked_movie,
            rated_movie,
            customised_movie,
            tracked_show,
            watched_show,
            rated_show,
            rated_episode_show,
        ]
        self.age(*kept)

        result = remove_unused_titles()

        self.assertEqual(result, {"movies": 0, "shows": 0})
        for obj in kept:
            self.assertTrue(type(obj).objects.filter(pk=obj.pk).exists(), obj)

    def test_untouched_titles_are_removed_with_their_metadata(self):
        movie = self.movie()
        show = self.show()
        SimklMediaInfo.objects.create(
            content_type=ContentType.objects.get_for_model(Movie),
            object_id=movie.pk,
            media_type="movie",
        )
        MediaArtwork.objects.create(
            provider=show.provider,
            media_type=MediaArtwork.MediaType.TV,
            external_id=show.external_id,
            kind=MediaArtwork.Kind.POSTER,
            image_url="https://example.com/poster.jpg",
        )
        self.age(movie, show)

        result = remove_unused_titles()

        self.assertEqual(result, {"movies": 1, "shows": 1})
        self.assertFalse(Movie.objects.filter(pk=movie.pk).exists())
        self.assertFalse(Show.objects.filter(pk=show.pk).exists())
        self.assertFalse(Season.objects.filter(show_id=show.pk).exists())
        self.assertFalse(Episode.objects.filter(show_id=show.pk).exists())
        self.assertFalse(SimklMediaInfo.objects.exists())
        self.assertFalse(MediaArtwork.objects.exists())

    def test_recently_touched_titles_are_kept(self):
        movie = self.movie()
        show = self.show()
        self.age(movie, show, days=2)

        self.assertEqual(remove_unused_titles(), {"movies": 0, "shows": 0})
