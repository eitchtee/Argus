from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.catalog.ratings import clear_rating, rate_media
from apps.movies.models import Movie, UserMovie
from apps.movies.services import mark_seen
from apps.simkl.changes import simkl_rating_from_score
from apps.simkl.models import SimklAccount, SimklSyncIntent
from apps.trakt.changes import suppress_local_intents
from apps.trakt.models import TraktSyncIntent
from apps.tv.models import Show, UserShow
from apps.tv.services import drop_show, pause_show


class SimklChangesTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            "user@example.com",
            password="password",
        )
        self.movie = Movie.objects.create(
            external_id="550",
            tmdb_id="550",
            imdb_id="tt0137523",
            title="Fight Club",
        )
        self.show = Show.objects.create(external_id="73739", tvdb_id="73739", name="Lost")

    def test_shared_kinds_fan_out_only_when_an_account_exists(self):
        mark_seen(self.user, self.movie)
        self.assertFalse(SimklSyncIntent.objects.exists())
        self.assertFalse(TraktSyncIntent.objects.exists())

        SimklAccount.objects.create(user=self.user, access_token="token")
        mark_seen(self.user, self.movie)

        kinds = set(SimklSyncIntent.objects.values_list("kind", flat=True))
        self.assertEqual(
            kinds,
            {SimklSyncIntent.Kind.MOVIE_HISTORY, SimklSyncIntent.Kind.MOVIE_WATCHLIST},
        )
        # No Trakt account, so nothing is queued for Trakt.
        self.assertFalse(TraktSyncIntent.objects.exists())

    def test_pause_and_drop_record_paused_intents(self):
        SimklAccount.objects.create(user=self.user, access_token="token")
        UserShow.objects.create(user=self.user, show=self.show)

        pause_show(self.user, self.show)
        intent = SimklSyncIntent.objects.get(kind=SimklSyncIntent.Kind.SHOW_PAUSED)
        self.assertTrue(intent.desired)
        self.assertEqual(intent.payload["ids"]["tvdb"], 73739)

        drop_show(self.user, self.show)
        intent.refresh_from_db()
        self.assertFalse(intent.desired)
        self.assertTrue(
            SimklSyncIntent.objects.filter(
                kind=SimklSyncIntent.Kind.SHOW_DROPPED, desired=True
            ).exists()
        )

    def test_ratings_record_intents_on_the_simkl_scale(self):
        SimklAccount.objects.create(user=self.user, access_token="token")
        UserMovie.objects.create(user=self.user, movie=self.movie, is_seen=True)

        rate_media(self.user, "movie", self.movie, Decimal("3.5"))
        intent = SimklSyncIntent.objects.get(kind=SimklSyncIntent.Kind.MOVIE_RATING)
        self.assertTrue(intent.desired)
        self.assertEqual(intent.payload["rating"], 7)

        clear_rating(self.user, self.movie)
        intent.refresh_from_db()
        self.assertFalse(intent.desired)

    def test_suppressed_changes_do_not_record_intents(self):
        SimklAccount.objects.create(user=self.user, access_token="token")
        UserShow.objects.create(user=self.user, show=self.show)

        with suppress_local_intents():
            pause_show(self.user, self.show)

        self.assertFalse(SimklSyncIntent.objects.exists())

    def test_rating_scale_conversion(self):
        self.assertEqual(simkl_rating_from_score(Decimal("0.5")), 1)
        self.assertEqual(simkl_rating_from_score(Decimal("5.0")), 10)
        self.assertEqual(simkl_rating_from_score(Decimal("2.5")), 5)
