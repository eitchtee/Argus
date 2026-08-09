from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.trakt.changes import record_intent, suppress_local_intents
from apps.stremio.models import StremioAccount, StremioSyncIntent
from apps.tv.models import Show
from apps.tv.services import drop_show


class StremioChangeTests(TestCase):
    def test_drop_show_records_stremio_removal_without_a_trakt_account(self):
        user = get_user_model().objects.create_user("user@example.com", password="pw")
        StremioAccount.objects.create(user=user, auth_key="auth-key")
        show = Show.objects.create(
            imdb_id="tt0944947",
            name="Game of Thrones",
            external_id="1399",
            tmdb_id="1399",
        )

        drop_show(user, show)

        intent = StremioSyncIntent.objects.get(
            user=user,
            kind=StremioSyncIntent.Kind.SHOW_WATCHLIST,
        )
        self.assertFalse(intent.desired)

    def test_record_intent_supports_a_stremio_only_account(self):
        user = get_user_model().objects.create_user("user@example.com", password="pw")
        StremioAccount.objects.create(user=user, auth_key="auth-key")

        intent = record_intent(
            user,
            StremioSyncIntent.Kind.MOVIE_WATCHLIST,
            {"title": "Fight Club", "ids": {"imdb": "tt0137523"}},
        )

        self.assertEqual(intent.kind, StremioSyncIntent.Kind.MOVIE_WATCHLIST)
        self.assertTrue(
            StremioSyncIntent.objects.filter(
                user=user,
                identity_key="imdb:tt0137523",
                desired=True,
            ).exists()
        )

    def test_remote_apply_can_suppress_stremio_local_intents(self):
        user = get_user_model().objects.create_user("user@example.com", password="pw")
        StremioAccount.objects.create(user=user, auth_key="auth-key")

        with suppress_local_intents():
            record_intent(
                user,
                StremioSyncIntent.Kind.MOVIE_WATCHLIST,
                {"ids": {"imdb": "tt0137523"}},
            )

        self.assertFalse(StremioSyncIntent.objects.filter(user=user).exists())

    def test_unsupported_trakt_intent_is_not_written_to_stremio(self):
        user = get_user_model().objects.create_user("user@example.com", password="pw")
        StremioAccount.objects.create(user=user, auth_key="auth-key")

        record_intent(
            user,
            "show_dropped",
            {"title": "Game of Thrones", "ids": {"imdb": "tt0944947"}},
        )

        self.assertFalse(StremioSyncIntent.objects.filter(user=user).exists())
