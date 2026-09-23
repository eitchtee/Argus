from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from apps.movies.models import Movie
from apps.movies.services import mark_seen
from apps.simkl.models import SimklAccount, SimklSyncIntent
from apps.sync.changes import IntentKind, record_intent, suppress_local_intents
from apps.sync.identities import movie_payload
from apps.tv.models import Episode, Season, Show, UserEpisode, UserShow
from apps.tv.services import (
    drop_show,
    mark_episode_watched,
    unmark_season_watched,
    unmark_show_watched,
)


class RecordIntentTests(TestCase):
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

    def connect(self):
        SimklAccount.objects.create(user=self.user, access_token="token")

    def test_recording_same_movie_twice_keeps_one_intent(self):
        self.connect()
        first = timezone.now() - timedelta(days=1)
        second = timezone.now()

        record_intent(
            self.user,
            IntentKind.MOVIE_HISTORY,
            movie_payload(self.movie, watched_at=first),
        )
        record_intent(
            self.user,
            IntentKind.MOVIE_HISTORY,
            movie_payload(self.movie, watched_at=second),
        )

        intent = SimklSyncIntent.objects.get(
            user=self.user,
            kind=IntentKind.MOVIE_HISTORY,
        )
        self.assertEqual(SimklSyncIntent.objects.filter(user=self.user).count(), 1)
        self.assertEqual(intent.payload["watched_at"], second.isoformat())

    def test_no_account_is_a_no_op(self):
        result = record_intent(
            self.user,
            IntentKind.MOVIE_WATCHLIST,
            movie_payload(self.movie),
        )

        self.assertIsNone(result)
        self.assertFalse(SimklSyncIntent.objects.exists())

    def test_suppression_does_not_create_local_intent(self):
        self.connect()

        with suppress_local_intents():
            result = record_intent(
                self.user,
                IntentKind.MOVIE_WATCHLIST,
                movie_payload(self.movie),
            )

        self.assertIsNone(result)
        self.assertFalse(SimklSyncIntent.objects.exists())

    def test_watchlist_intent_can_replace_desired_membership(self):
        self.connect()

        record_intent(
            self.user,
            IntentKind.MOVIE_WATCHLIST,
            movie_payload(self.movie),
        )
        record_intent(
            self.user,
            IntentKind.MOVIE_WATCHLIST,
            movie_payload(self.movie),
            desired=False,
        )

        intent = SimklSyncIntent.objects.get(
            user=self.user,
            kind=IntentKind.MOVIE_WATCHLIST,
        )
        self.assertFalse(intent.desired)

    def test_movie_mark_seen_records_history_and_watchlist_removal(self):
        self.connect()

        mark_seen(self.user, self.movie)

        self.assertTrue(
            SimklSyncIntent.objects.filter(
                user=self.user,
                kind=IntentKind.MOVIE_HISTORY,
            ).exists()
        )
        self.assertFalse(
            SimklSyncIntent.objects.get(
                user=self.user,
                kind=IntentKind.MOVIE_WATCHLIST,
            ).desired
        )

    def test_tv_drop_and_episode_watch_record_intents(self):
        self.connect()
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
            SimklSyncIntent.objects.get(
                kind=IntentKind.SHOW_WATCHLIST,
            ).desired
        )
        drop_show(self.user, show)

        self.assertTrue(
            SimklSyncIntent.objects.filter(
                kind=IntentKind.EPISODE_HISTORY,
            ).exists()
        )
        self.assertTrue(
            SimklSyncIntent.objects.get(
                kind=IntentKind.SHOW_DROPPED,
            ).desired
        )

    def test_bulk_tv_unwatch_records_episode_history_removals(self):
        self.connect()
        show = Show.objects.create(external_id="show-1", trakt_id="1000", name="The Show")
        season = Season.objects.create(show=show, season_number=1, name="Season 1")
        specials = Season.objects.create(show=show, season_number=0, name="Specials")
        episodes = [
            Episode.objects.create(
                show=show,
                season=season,
                season_number=1,
                episode_number=number,
                trakt_id=str(1000 + number),
                name=f"Episode {number}",
            )
            for number in (1, 2)
        ]
        special = Episode.objects.create(
            show=show,
            season=specials,
            season_number=0,
            episode_number=1,
            trakt_id="1003",
            name="Special",
        )
        UserShow.objects.create(user=self.user, show=show, status=UserShow.Status.TRACKED)
        for episode in [*episodes, special]:
            UserEpisode.objects.create(user=self.user, episode=episode)

        unmark_season_watched(self.user, season)
        unmark_show_watched(self.user, show)

        intents = SimklSyncIntent.objects.filter(
            user=self.user,
            kind=IntentKind.EPISODE_HISTORY,
        )
        self.assertEqual(intents.count(), 2)
        self.assertTrue(all(not intent.desired for intent in intents))
        self.assertFalse(UserEpisode.objects.filter(user=self.user, episode__in=episodes).exists())
        self.assertTrue(UserEpisode.objects.filter(user=self.user, episode=special).exists())
