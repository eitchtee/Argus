from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.test import TestCase

from apps.catalog.models import MediaRating
from apps.catalog.ratings import (
    delete_ratings_for,
    get_user_score,
    is_rateable,
    parse_score,
    rate_media,
    resolve_media,
    transfer_rating,
)
from apps.movies.models import Movie, UserMovie
from apps.tv.models import Episode, Season, Show, UserEpisode


class ParseScoreTests(TestCase):
    def test_accepts_half_steps(self):
        self.assertEqual(parse_score("0.5"), Decimal("0.5"))
        self.assertEqual(parse_score("1"), Decimal("1.0"))
        self.assertEqual(parse_score("3.5"), Decimal("3.5"))
        self.assertEqual(parse_score("5.0"), Decimal("5.0"))

    def test_rejects_out_of_range(self):
        with self.assertRaises(ValueError):
            parse_score("0")
        with self.assertRaises(ValueError):
            parse_score("5.5")

    def test_rejects_off_step_values(self):
        with self.assertRaises(ValueError):
            parse_score("4.3")

    def test_rejects_garbage(self):
        with self.assertRaises(ValueError):
            parse_score("abc")
        with self.assertRaises(ValueError):
            parse_score(None)
        with self.assertRaises(ValueError):
            parse_score("")


class RateMediaTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            "user@example.com", password="password"
        )
        self.movie = Movie.objects.create(external_id="550", title="Fight Club")
        self.user_movie = UserMovie.objects.create(
            user=self.user, movie=self.movie, is_seen=True
        )

    def test_watched_movie_can_be_rated(self):
        rating = rate_media(self.user, "movie", self.movie, Decimal("4.5"))

        self.assertEqual(rating.score, Decimal("4.5"))
        self.assertEqual(rating.media_type, "movie")
        self.assertEqual(rating.content_object, self.movie)

    def test_unwatched_movie_cannot_be_rated(self):
        self.user_movie.is_seen = False
        self.user_movie.save()

        with self.assertRaises(ValueError):
            rate_media(self.user, "movie", self.movie, Decimal("4.0"))

    def test_rating_again_edits_the_existing_row(self):
        rate_media(self.user, "movie", self.movie, Decimal("2.0"))
        rate_media(self.user, "movie", self.movie, Decimal("4.5"))

        self.assertEqual(MediaRating.objects.count(), 1)
        self.assertEqual(get_user_score(self.user, self.movie), Decimal("4.5"))

    def test_other_users_ratings_are_independent(self):
        other = get_user_model().objects.create_user("other@example.com", password="pw")

        rate_media(self.user, "movie", self.movie, Decimal("2.0"))

        self.assertIsNone(get_user_score(other, self.movie))
        self.assertFalse(is_rateable(other, self.movie))


class ShowRatingTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            "user@example.com", password="password"
        )

    def test_show_is_rateable_without_any_tracking_or_watched_state(self):
        show = Show.objects.create(external_id="123", name="Foo")

        self.assertTrue(is_rateable(self.user, show))

        rating = rate_media(self.user, "show", show, Decimal("3.5"))
        self.assertEqual(rating.score, Decimal("3.5"))

    def test_resolving_untracked_show_creates_a_stub(self):
        show = resolve_media("show", external_id="999", provider=None)

        self.assertIsNotNone(show.pk)
        self.assertFalse(UserEpisode.objects.filter(episode__show=show).exists())


class EpisodeRatingTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            "user@example.com", password="password"
        )
        self.show = Show.objects.create(external_id="123", name="Foo")
        self.season = Season.objects.create(show=self.show, season_number=1)
        self.episode = Episode.objects.create(
            show=self.show, season=self.season, season_number=1, episode_number=1
        )

    def test_unseen_episode_cannot_be_rated(self):
        self.assertFalse(is_rateable(self.user, self.episode))
        with self.assertRaises(ValueError):
            rate_media(self.user, "episode", self.episode, Decimal("4.0"))

    def test_seen_episode_can_be_rated_and_edited(self):
        UserEpisode.objects.create(user=self.user, episode=self.episode)

        rate_media(self.user, "episode", self.episode, Decimal("2.5"))
        rate_media(self.user, "episode", self.episode, Decimal("5.0"))

        self.assertEqual(MediaRating.objects.count(), 1)
        self.assertEqual(get_user_score(self.user, self.episode), Decimal("5.0"))


class TransferAndDeleteTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            "user@example.com", password="password"
        )

    def test_transfer_moves_rating_to_target(self):
        source = Movie.objects.create(external_id="1", title="Source")
        target = Movie.objects.create(external_id="2", title="Target")
        UserMovie.objects.create(user=self.user, movie=source, is_seen=True)
        rate_media(self.user, "movie", source, Decimal("3.5"))

        transfer_rating(self.user, source=source, target=target)

        self.assertIsNone(get_user_score(self.user, source))
        self.assertEqual(get_user_score(self.user, target), Decimal("3.5"))

    def test_delete_ratings_for_removes_only_that_users_rating(self):
        other = get_user_model().objects.create_user("other@example.com", password="pw")
        movie = Movie.objects.create(external_id="1", title="X")
        UserMovie.objects.create(user=self.user, movie=movie, is_seen=True)
        rate_media(self.user, "movie", movie, Decimal("4.0"))
        movie_rating_type = ContentType.objects.get_for_model(Movie)
        MediaRating.objects.create(
            user=other,
            media_type="movie",
            content_type=movie_rating_type,
            object_id=movie.pk,
            score=Decimal("1.0"),
        )

        delete_ratings_for(self.user, movie)

        self.assertEqual(MediaRating.objects.filter(user=other).count(), 1)
