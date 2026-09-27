from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.mdblist.client import MdblistAuthenticationError, TokenResponse
from apps.mdblist.models import MdblistAccount, MdblistLibraryItem, MdblistSyncIntent
from apps.movies.models import Movie, UserMovie


OAUTH = dict(MDBLIST_CLIENT_ID="cid", MDBLIST_CLIENT_SECRET="secret", MDBLIST_REDIRECT_URI="")
PROFILE = {"username": "mdbuser", "user_id": 7, "plan": "Free", "is_supporter": False}
HTMX = {"HTTP_HX_REQUEST": "true"}

# Full pages pull the Vite bundle; dev mode skips the manifest lookup.
PAGE_SETTINGS = dict(
    STORAGES={
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    },
    DJANGO_VITE_DEV_MODE=True,
)


def _reset_vite_loader():
    from django_vite.core.asset_loader import DjangoViteAssetLoader

    DjangoViteAssetLoader._instance = None


@override_settings(**PAGE_SETTINGS)
@patch("apps.mdblist.views.enqueue_account_sync")
class AccountViewTests(TestCase):
    def setUp(self):
        _reset_vite_loader()
        self.user = get_user_model().objects.create_user("user@example.com", password="password")
        self.client.login(username="user@example.com", password="password")

    def tearDown(self):
        _reset_vite_loader()

    @override_settings(MDBLIST_CLIENT_ID="", MDBLIST_CLIENT_SECRET="")
    def test_oauth_connect_requires_an_application(self, _enqueue):
        self.assertEqual(self.client.get(reverse("mdblist_connect")).status_code, 503)

    @override_settings(**OAUTH)
    def test_oauth_round_trip_stores_the_tokens(self, enqueue):
        response = self.client.get(reverse("mdblist_connect"))
        params = {key: values[0] for key, values in parse_qs(urlsplit(response["Location"]).query).items()}
        self.assertEqual(params["redirect_uri"], "http://testserver/user/mdblist/callback/")
        self.assertEqual(params["code_challenge_method"], "S256")
        verifier = self.client.session["mdblist_oauth_verifier"]

        token = TokenResponse(access_token="access", refresh_token="refresh", expires_in=2592000)
        with (
            patch("apps.mdblist.client.MdblistClient.exchange_code", return_value=token) as exchange,
            patch("apps.mdblist.client.MdblistClient.get_user", return_value=PROFILE),
        ):
            response = self.client.get(reverse("mdblist_callback"), {"state": params["state"], "code": "abc"})

        self.assertEqual(response.status_code, 302)
        self.assertEqual(exchange.call_args.args, ("abc", "http://testserver/user/mdblist/callback/", verifier))
        account = MdblistAccount.objects.get(user=self.user)
        self.assertEqual(account.auth_method, MdblistAccount.AuthMethod.OAUTH)
        self.assertEqual((account.access_token, account.refresh_token), ("access", "refresh"))
        self.assertIsNotNone(account.token_expires_at)
        self.assertEqual(account.mdblist_username, "mdbuser")
        enqueue.assert_called_once_with(account.id)

    @override_settings(**OAUTH)
    def test_callback_rejects_a_forged_state(self, _enqueue):
        self.client.get(reverse("mdblist_connect"))

        response = self.client.get(reverse("mdblist_callback"), {"state": "forged", "code": "abc"})

        self.assertEqual(response.status_code, 400)
        self.assertFalse(MdblistAccount.objects.exists())

    def test_api_key_connect_validates_the_key(self, enqueue):
        with patch("apps.mdblist.client.MdblistClient.get_user", return_value=PROFILE):
            response = self.client.post(reverse("mdblist_connect_key"), {"api_key": " key-123 "})

        self.assertEqual(response.status_code, 204)
        account = MdblistAccount.objects.get(user=self.user)
        self.assertEqual(account.auth_method, MdblistAccount.AuthMethod.API_KEY)
        self.assertEqual(account.access_token, "key-123")
        enqueue.assert_called_once_with(account.id)

    def test_a_rejected_api_key_connects_nothing(self, enqueue):
        with patch("apps.mdblist.client.MdblistClient.get_user", side_effect=MdblistAuthenticationError("bad")):
            self.client.post(reverse("mdblist_connect_key"), {"api_key": "nope"})

        self.assertFalse(MdblistAccount.objects.exists())
        enqueue.assert_not_called()

    def test_reconnecting_drops_the_old_mirror(self, _enqueue):
        account = MdblistAccount.objects.create(user=self.user, access_token="old", initial_sync_complete=True)
        MdblistLibraryItem.objects.create(account=account, media_type="movie", tmdb_id=550, on_watchlist=True)

        with patch("apps.mdblist.client.MdblistClient.get_user", return_value=PROFILE):
            self.client.post(reverse("mdblist_connect_key"), {"api_key": "new"})

        account.refresh_from_db()
        self.assertFalse(account.initial_sync_complete)
        self.assertFalse(account.library_items.exists())

    def test_disconnect_and_sync(self, enqueue):
        account = MdblistAccount.objects.create(user=self.user, access_token="k", pending_pushes={"tmdb:1": {}})
        self.assertEqual(self.client.post(reverse("mdblist_sync")).status_code, 204)
        account.refresh_from_db()
        self.assertEqual(account.pending_pushes, {})
        enqueue.assert_called_once_with(account.id)

        MdblistSyncIntent.objects.create(user=self.user, kind="movie_history", identity_key="tmdb:1")
        self.client.post(reverse("mdblist_disconnect"))
        self.assertFalse(MdblistAccount.objects.exists())
        self.assertFalse(MdblistSyncIntent.objects.exists())

    @override_settings(**OAUTH)
    def test_settings_page_offers_both_ways_to_connect(self, _enqueue):
        response = self.client.get(reverse("user_settings"))

        self.assertContains(response, reverse("mdblist_connect"))
        self.assertContains(response, reverse("mdblist_connect_key"))


@override_settings(**PAGE_SETTINGS, MDBLIST_API_KEY="server-key")
class PageTests(TestCase):
    def setUp(self):
        cache.clear()
        _reset_vite_loader()
        self.user = get_user_model().objects.create_user("user@example.com", password="password")
        self.client.login(username="user@example.com", password="password")

    def tearDown(self):
        cache.clear()
        _reset_vite_loader()

    def test_detail_card_renders_ratings(self):
        from apps.mdblist.tests.test_enrichment import FIGHT_CLUB

        with patch("apps.mdblist.client.MdblistClient.get_media", return_value=FIGHT_CLUB):
            response = self.client.get(reverse("mdblist-media-info", kwargs={"media_type": "movie", "external_id": "550"}), **HTMX)

        self.assertContains(response, "Rotten Tomatoes")
        self.assertContains(response, "https://www.rottentomatoes.com/m/fight_club")
        self.assertContains(response, "American Psycho")

    def test_home_charts_hide_watched_titles(self):
        from apps.mdblist.tests.test_enrichment import CHART

        movie = Movie.objects.create(external_id="550", tmdb_id="550", title="Fight Club")
        UserMovie.objects.create(user=self.user, movie=movie, is_seen=True)
        with patch("apps.mdblist.client.MdblistClient.get_streaming_chart", return_value=CHART):
            response = self.client.get(reverse("mdblist-home-charts"), **HTMX)

        self.assertContains(response, "Tuner")
        self.assertNotContains(response, "Fight Club")

    @override_settings(SIMKL_CLIENT_ID="")
    def test_discover_page_shows_mdblist_sections_without_simkl(self):
        response = self.client.get(reverse("discover"))

        self.assertContains(response, "Top Streaming Movies")
        self.assertContains(response, reverse("mdblist-discover-section", kwargs={"section": "anticipated"}))
        self.assertNotContains(response, "Trending Movies on Simkl")
        self.assertNotContains(response, "Recommended for you")

    def test_unknown_discover_section_is_404(self):
        response = self.client.get(reverse("mdblist-discover-section", kwargs={"section": "nope"}), **HTMX)

        self.assertEqual(response.status_code, 404)

    @override_settings(SIMKL_CLIENT_ID="", MDBLIST_API_KEY="")
    def test_without_mdblist_the_pages_stay_quiet(self):
        response = self.client.get(reverse("index"))

        self.assertNotContains(response, reverse("mdblist-home-charts"))
        self.assertNotContains(response, reverse("discover"))
