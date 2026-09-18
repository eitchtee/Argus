from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.movies.models import Movie, UserMovie
from apps.simkl.client import TokenResponse
from apps.simkl.models import SimklAccount, SimklLibraryItem, SimklSyncIntent


CONFIGURED = dict(
    SIMKL_CLIENT_ID="client",
    SIMKL_CLIENT_SECRET="secret",
    SIMKL_REDIRECT_URI="https://argus.test/user/simkl/callback/",
)

# Full pages pull the Vite bundle; dev mode skips the manifest lookup.
PAGE_SETTINGS = dict(
    STORAGES={
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {
            "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"
        },
    },
    DJANGO_VITE_DEV_MODE=True,
)


def _reset_vite_loader():
    from django_vite.core.asset_loader import DjangoViteAssetLoader

    DjangoViteAssetLoader._instance = None

TRENDING_ITEM = {
    "title": "The Drama",
    "url": "/movies/2533163/the-drama",
    "poster": "19/19702715dfee84bd7c",
    "ids": {"simkl_id": 2533163, "slug": "the-drama", "imdb": "tt33071426", "tmdb": "1148540"},
    "release_date": "03/26/2026",
    "watched": 96,
    "ratings": {"simkl": {"rating": 7.37, "votes": 136}},
    "status": "premiere",
    "genres": ["Drama"],
}


@override_settings(**PAGE_SETTINGS)
class SimklAccountViewTests(TestCase):
    def setUp(self):
        _reset_vite_loader()
        self.user = get_user_model().objects.create_user("user@example.com", password="password")
        self.client.login(username="user@example.com", password="password")

    def tearDown(self):
        _reset_vite_loader()

    @override_settings(SIMKL_CLIENT_ID="", SIMKL_CLIENT_SECRET="", SIMKL_REDIRECT_URI="")
    def test_connect_requires_server_credentials(self):
        self.assertEqual(self.client.get(reverse("simkl_connect")).status_code, 503)

    @override_settings(SIMKL_CLIENT_ID="client", SIMKL_CLIENT_SECRET="secret", SIMKL_REDIRECT_URI="")
    def test_connect_derives_the_callback_from_the_request(self):
        response = self.client.get(reverse("simkl_connect"))

        query = parse_qs(urlsplit(response["Location"]).query)
        self.assertEqual(query["redirect_uri"], ["http://testserver/user/simkl/callback/"])
        self.assertEqual(
            self.client.session["simkl_oauth_redirect_uri"],
            "http://testserver/user/simkl/callback/",
        )

    @override_settings(**CONFIGURED)
    def test_connect_redirects_to_simkl_with_state(self):
        response = self.client.get(reverse("simkl_connect"))

        self.assertEqual(response.status_code, 302)
        self.assertTrue(response["Location"].startswith("https://simkl.com/oauth/authorize?"))
        query = parse_qs(urlsplit(response["Location"]).query)
        self.assertEqual(query["client_id"], ["client"])
        self.assertEqual(query["redirect_uri"], [CONFIGURED["SIMKL_REDIRECT_URI"]])
        self.assertEqual(query["state"], [self.client.session["simkl_oauth_state"]])

    @override_settings(**CONFIGURED)
    @patch("apps.simkl.views.SimklClient")
    def test_callback_rejects_state_mismatch(self, client_class):
        session = self.client.session
        session["simkl_oauth_state"] = "expected"
        session.save()

        response = self.client.get(reverse("simkl_callback"), {"code": "code", "state": "wrong"})

        self.assertEqual(response.status_code, 400)
        client_class.assert_not_called()

    @override_settings(**CONFIGURED)
    @patch("apps.simkl.views.enqueue_account_sync")
    @patch("apps.simkl.views.SimklClient")
    def test_callback_exchanges_code_and_queues_initial_sync(self, client_class, enqueue):
        client = client_class.return_value
        client.exchange_code.return_value = TokenResponse(access_token="access", expires_in=157680000)
        client.get_user_settings.return_value = {
            "user": {"name": "simkluser"},
            "account": {"id": 77, "type": "vip"},
        }
        session = self.client.session
        session["simkl_oauth_state"] = "state"
        session["simkl_oauth_redirect_uri"] = CONFIGURED["SIMKL_REDIRECT_URI"]
        session.save()

        response = self.client.get(reverse("simkl_callback"), {"code": "code", "state": "state"})

        self.assertEqual(response.status_code, 302)
        account = SimklAccount.objects.get(user=self.user)
        self.assertEqual(account.access_token, "access")
        self.assertEqual(account.simkl_username, "simkluser")
        self.assertEqual(account.account_type, "vip")
        self.assertFalse(account.initial_sync_complete)
        client.exchange_code.assert_called_once_with("code", CONFIGURED["SIMKL_REDIRECT_URI"])
        enqueue.assert_called_once_with(account.id)

    @override_settings(**CONFIGURED)
    @patch("apps.simkl.views.enqueue_account_sync")
    @patch("apps.simkl.views.SimklClient")
    def test_reconnecting_resets_the_library_mirror(self, client_class, _enqueue):
        account = SimklAccount.objects.create(
            user=self.user,
            access_token="old",
            initial_sync_complete=True,
            activities_cursor="2026-01-01T00:00:00Z",
        )
        SimklLibraryItem.objects.create(account=account, media_type="movie", simkl_id=1)
        client = client_class.return_value
        client.exchange_code.return_value = TokenResponse(access_token="new", expires_in=1)
        client.get_user_settings.return_value = {}
        session = self.client.session
        session["simkl_oauth_state"] = "state"
        session.save()

        self.client.get(reverse("simkl_callback"), {"code": "code", "state": "state"})

        account.refresh_from_db()
        self.assertEqual(account.access_token, "new")
        self.assertFalse(account.initial_sync_complete)
        self.assertEqual(account.activities_cursor, "")
        self.assertFalse(account.library_items.exists())

    def test_disconnect_removes_account_and_intents(self):
        SimklAccount.objects.create(user=self.user, access_token="token")
        SimklSyncIntent.objects.create(user=self.user, kind="movie_history", identity_key="x", payload={})

        response = self.client.post(reverse("simkl_disconnect"))

        self.assertEqual(response.status_code, 204)
        self.assertFalse(SimklAccount.objects.exists())
        self.assertFalse(SimklSyncIntent.objects.exists())

    @patch("apps.simkl.views.enqueue_account_sync")
    def test_manual_sync_clears_pending_pushes_and_queues(self, enqueue):
        account = SimklAccount.objects.create(
            user=self.user,
            access_token="token",
            pending_pushes={"imdb:tt1": {"reason": "not_found"}},
        )

        response = self.client.post(reverse("simkl_sync"))

        self.assertEqual(response.status_code, 204)
        account.refresh_from_db()
        self.assertEqual(account.pending_pushes, {})
        enqueue.assert_called_once_with(account.id)

    def test_manual_sync_without_account_is_not_found(self):
        self.assertEqual(self.client.post(reverse("simkl_sync")).status_code, 404)

    @override_settings(**CONFIGURED)
    def test_settings_page_shows_simkl_panel(self):
        SimklAccount.objects.create(user=self.user, access_token="token", simkl_username="simkluser")

        response = self.client.get(reverse("user_settings"))

        self.assertContains(response, "simkluser")
        self.assertContains(response, reverse("simkl_sync"))


@override_settings(**PAGE_SETTINGS)
class DiscoverViewTests(TestCase):
    def setUp(self):
        cache.clear()
        _reset_vite_loader()
        self.user = get_user_model().objects.create_user("user@example.com", password="password")
        self.client.login(username="user@example.com", password="password")

    def tearDown(self):
        cache.clear()
        _reset_vite_loader()

    @override_settings(SIMKL_CLIENT_ID="")
    def test_discover_page_explains_missing_configuration(self):
        response = self.client.get(reverse("discover"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "SIMKL is not configured")

    @override_settings(SIMKL_CLIENT_ID="client")
    def test_discover_page_lists_attributed_sections(self):
        response = self.client.get(reverse("discover"))

        self.assertContains(response, "Trending Movies on Simkl")
        self.assertContains(response, "Trending TV Shows on Simkl")
        self.assertContains(response, reverse("discover-section", kwargs={"section": "premieres"}))

    @override_settings(SIMKL_CLIENT_ID="client")
    def test_trending_section_renders_cached_items_and_hides_watched(self):
        cache.set("simkl:trending:movies:today", [TRENDING_ITEM, {**TRENDING_ITEM, "title": "Seen", "ids": {"simkl_id": 1, "tmdb": "550"}}], 60)
        movie = Movie.objects.create(external_id="550", tmdb_id="550", title="Seen")
        UserMovie.objects.create(user=self.user, movie=movie, is_seen=True)

        response = self.client.get(
            reverse("discover-section", kwargs={"section": "trending-movies"}),
            HTTP_HX_REQUEST="true",
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "The Drama")
        self.assertNotContains(response, ">Seen<")
        self.assertContains(response, reverse("movie-detail", kwargs={"external_id": "1148540"}))
        self.assertContains(response, "https://simkl.com/movies/2533163/the-drama")

    @override_settings(SIMKL_CLIENT_ID="client")
    def test_section_fragments_require_htmx(self):
        response = self.client.get(reverse("discover-section", kwargs={"section": "trending-tv"}))

        self.assertEqual(response.status_code, 403)

    @override_settings(SIMKL_CLIENT_ID="client")
    def test_unknown_section_is_not_found(self):
        response = self.client.get(
            reverse("discover-section", kwargs={"section": "nope"}),
            HTTP_HX_REQUEST="true",
        )

        self.assertEqual(response.status_code, 404)

    @override_settings(SIMKL_CLIENT_ID="client")
    def test_home_page_includes_trending_block(self):
        cache.set("simkl:trending:movies:today", [TRENDING_ITEM], 60)

        page = self.client.get(reverse("index"))
        self.assertContains(page, "Trending on Simkl")

        fragment = self.client.get(
            reverse("simkl-home-trending"),
            {"category": "movies", "timeframe": "today"},
            HTTP_HX_REQUEST="true",
        )
        self.assertContains(fragment, "The Drama")

    @override_settings(SIMKL_CLIENT_ID="")
    def test_home_page_hides_trending_block_without_client_id(self):
        page = self.client.get(reverse("index"))

        self.assertNotContains(page, "Trending on Simkl")
