from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from apps.movies.models import Movie
from apps.movies.services import mark_seen
from apps.trakt.changes import record_intent, suppress_local_intents
from apps.trakt.identities import movie_payload
from apps.trakt.models import TraktAccount, TraktSyncIntent
from apps.tv.models import Episode, Season, Show, UserShow
from apps.tv.services import drop_show, mark_episode_watched


class TraktChangesTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            "user@example.com",
            password="password",
        )
        self.movie = Movie.objects.create(
            external_id="550",
            trakt_id="5500",
            tmdb_id="550",
            title="Fight Club",
        )

    def test_recording_same_movie_twice_keeps_one_intent(self):
        TraktAccount.objects.create(
            user=self.user,
            access_token="access",
            refresh_token="refresh",
        )

        first = timezone.now() - timedelta(days=1)
        second = timezone.now()
        first_payload = movie_payload(self.movie, watched_at=first)
        second_payload = movie_payload(self.movie, watched_at=second)

        record_intent(
            self.user,
            TraktSyncIntent.Kind.MOVIE_HISTORY,
            first_payload,
        )
        record_intent(
            self.user,
            TraktSyncIntent.Kind.MOVIE_HISTORY,
            second_payload,
        )

        intent = TraktSyncIntent.objects.get(
            user=self.user,
            kind=TraktSyncIntent.Kind.MOVIE_HISTORY,
        )
        self.assertEqual(TraktSyncIntent.objects.filter(user=self.user).count(), 1)
        self.assertEqual(intent.payload["watched_at"], second.isoformat())

    def test_no_account_is_a_no_op(self):
        result = record_intent(
            self.user,
            TraktSyncIntent.Kind.MOVIE_WATCHLIST,
            movie_payload(self.movie),
        )

        self.assertIsNone(result)
        self.assertFalse(TraktSyncIntent.objects.exists())

    def test_remote_suppression_does_not_create_local_intent(self):
        TraktAccount.objects.create(
            user=self.user,
            access_token="access",
            refresh_token="refresh",
        )

        with suppress_local_intents():
            result = record_intent(
                self.user,
                TraktSyncIntent.Kind.MOVIE_WATCHLIST,
                movie_payload(self.movie),
            )

        self.assertIsNone(result)
        self.assertFalse(TraktSyncIntent.objects.exists())

    def test_watchlist_intent_can_replace_desired_membership(self):
        TraktAccount.objects.create(
            user=self.user,
            access_token="access",
            refresh_token="refresh",
        )

        record_intent(
            self.user,
            TraktSyncIntent.Kind.MOVIE_WATCHLIST,
            movie_payload(self.movie),
        )
        record_intent(
            self.user,
            TraktSyncIntent.Kind.MOVIE_WATCHLIST,
            movie_payload(self.movie),
            desired=False,
        )

        intent = TraktSyncIntent.objects.get(
            user=self.user,
            kind=TraktSyncIntent.Kind.MOVIE_WATCHLIST,
        )
        self.assertFalse(intent.desired)

    def test_movie_mark_seen_records_history_and_watchlist_removal(self):
        TraktAccount.objects.create(
            user=self.user,
            access_token="access",
            refresh_token="refresh",
        )

        mark_seen(self.user, self.movie)

        self.assertTrue(
            TraktSyncIntent.objects.filter(
                user=self.user,
                kind=TraktSyncIntent.Kind.MOVIE_HISTORY,
            ).exists()
        )
        self.assertFalse(
            TraktSyncIntent.objects.get(
                user=self.user,
                kind=TraktSyncIntent.Kind.MOVIE_WATCHLIST,
            ).desired
        )

    def test_tv_drop_and_episode_watch_record_intents(self):
        TraktAccount.objects.create(
            user=self.user,
            access_token="access",
            refresh_token="refresh",
        )
        show = Show.objects.create(external_id="show-1", trakt_id="1000", name="The Show")
        season = Season.objects.create(show=show, season_number=1, name="Season 1")
        episode = Episode.objects.create(
            show=show,
            season=season,
            season_number=1,
            episode_number=1,
            trakt_id="1001",
            name="Pilot",
        )
        UserShow.objects.create(user=self.user, show=show, status=UserShow.Status.TRACKED)

        mark_episode_watched(self.user, episode)
        self.assertFalse(
            TraktSyncIntent.objects.get(
                kind=TraktSyncIntent.Kind.SHOW_WATCHLIST,
            ).desired
        )
        drop_show(self.user, show)

        self.assertTrue(
            TraktSyncIntent.objects.filter(
                kind=TraktSyncIntent.Kind.EPISODE_HISTORY,
            ).exists()
        )
        self.assertTrue(
            TraktSyncIntent.objects.get(
                kind=TraktSyncIntent.Kind.SHOW_DROPPED,
            ).desired
        )
