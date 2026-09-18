from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.catalog.providers.base import DetailDTO
from apps.movies.models import Movie, UserMovie
from apps.simkl.client import RedirectTarget, SimklError
from apps.simkl.models import SimklMediaInfo
from apps.tv.models import Show, UserShow


DETAIL = {
    "title": "Inception",
    "ids": {"simkl": 472214, "slug": "inception"},
    "rank": 20,
    "droprate": "0.1%",
    "certification": "PG-13",
    "ratings": {"simkl": {"rating": 8.6, "votes": 11454}, "imdb": {"rating": 8.8, "votes": 2816410}},
    "trailers": [{"name": "Official Trailer", "youtube": "Jvurpf91omw", "size": 1080}],
    "users_recommendations": [
        {"title": "Interstellar", "year": 2014, "poster": "20/2052598c2716ef054", "type": "movie", "ids": {"simkl": 250822, "slug": "interstellar"}},
    ],
}


def fake_client():
    client = Mock()
    client.resolve.return_value = RedirectTarget(section="movies", simkl_id=472214)
    client.get_detail.return_value = DETAIL
    return client


@override_settings(SIMKL_CLIENT_ID="client")
class DetailPageEnrichmentTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = get_user_model().objects.create_user("user@example.com", password="password")
        self.client.login(username="user@example.com", password="password")
        self.movie = Movie.objects.create(
            external_id="27205",
            tmdb_id="27205",
            imdb_id="tt1375666",
            title="Inception",
            last_synced_at=timezone.now(),
        )
        UserMovie.objects.create(user=self.user, movie=self.movie, is_seen=True)
        self.show = Show.objects.create(
            external_id="73739", tvdb_id="73739", name="Lost", last_synced_at=timezone.now()
        )
        UserShow.objects.create(user=self.user, show=self.show)

    def tearDown(self):
        cache.clear()

    def info_url(self, media_type, external_id, provider):
        url = reverse("simkl-media-info", kwargs={"media_type": media_type, "external_id": external_id})
        return f"{url}?provider={provider}"

    def test_detail_pages_render_a_loading_slot_pointing_at_the_card_endpoint(self):
        response = self.client.get(
            reverse("movie-detail-content", kwargs={"external_id": "27205"}),
            HTTP_HX_REQUEST="true",
        )
        self.assertContains(response, 'id="simkl-info"')
        self.assertContains(response, self.info_url("movie", "27205", "tmdb"))
        self.assertContains(response, "Loading Simkl data")

        response = self.client.get(
            reverse("tv-detail-content", kwargs={"external_id": "73739"}),
            HTTP_HX_REQUEST="true",
        )
        self.assertContains(response, self.info_url("tv", "73739", "tvdb"))

    @override_settings(SIMKL_CLIENT_ID="")
    def test_slot_is_omitted_without_a_client_id(self):
        response = self.client.get(
            reverse("movie-detail-content", kwargs={"external_id": "27205"}),
            HTTP_HX_REQUEST="true",
        )
        self.assertNotContains(response, 'id="simkl-info"')

    @patch("apps.simkl.enrichment.build_client")
    def test_card_fetches_and_persists_for_tracked_titles(self, build_client):
        client = fake_client()
        build_client.return_value = client

        response = self.client.get(self.info_url("movie", "27205", "tmdb"), HTTP_HX_REQUEST="true")

        self.assertContains(response, "On Simkl")
        self.assertContains(response, "8.8")
        self.assertContains(response, "PG-13")
        self.assertContains(response, "https://simkl.com/movies/472214/inception")
        self.assertContains(response, "Interstellar")
        self.assertContains(response, "https://www.youtube.com/watch?v=Jvurpf91omw")
        info = SimklMediaInfo.objects.get(
            content_type=ContentType.objects.get_for_model(Movie), object_id=self.movie.pk
        )
        self.assertEqual(info.status, SimklMediaInfo.Status.OK)
        self.assertEqual(info.simkl_id, 472214)

        # Fresh data is served from the database without touching SIMKL.
        self.client.get(self.info_url("movie", "27205", "tmdb"), HTTP_HX_REQUEST="true")
        client.resolve.assert_called_once()
        client.get_detail.assert_called_once()

    @patch("apps.simkl.enrichment.build_client")
    def test_card_caches_for_untracked_titles(self, build_client):
        client = fake_client()
        build_client.return_value = client
        detail = DetailDTO(provider="tmdb", external_id="550", title="Fight Club", imdb_id="tt0137523", tmdb_id="550")

        with patch("apps.catalog.services.get_movie_detail", return_value=detail):
            response = self.client.get(self.info_url("movie", "550", "tmdb"), HTTP_HX_REQUEST="true")
            self.assertContains(response, "8.8")
            self.client.get(self.info_url("movie", "550", "tmdb"), HTTP_HX_REQUEST="true")

        client.resolve.assert_called_once_with(media_type="movie", imdb="tt0137523", tmdb="550", tvdb=None)
        client.get_detail.assert_called_once()
        self.assertFalse(SimklMediaInfo.objects.exists())
        self.assertIsNotNone(cache.get("simkl:info:movie:tmdb:550"))

    @patch("apps.simkl.enrichment.build_client")
    def test_card_reports_unknown_titles(self, build_client):
        client = fake_client()
        client.resolve.return_value = None
        build_client.return_value = client

        response = self.client.get(self.info_url("tv", "73739", "tvdb"), HTTP_HX_REQUEST="true")

        self.assertContains(response, "no record for this title")
        info = SimklMediaInfo.objects.get(
            content_type=ContentType.objects.get_for_model(Show), object_id=self.show.pk
        )
        self.assertEqual(info.status, SimklMediaInfo.Status.NOT_FOUND)

    @patch("apps.simkl.enrichment.build_client")
    def test_card_reports_api_errors_without_breaking_the_page(self, build_client):
        client = fake_client()
        client.resolve.side_effect = SimklError("down", status_code=503)
        build_client.return_value = client

        response = self.client.get(self.info_url("movie", "27205", "tmdb"), HTTP_HX_REQUEST="true")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "not reachable")

    @patch("apps.simkl.enrichment.build_client")
    def test_recommendations_link_into_argus_through_the_resolver(self, build_client):
        client = fake_client()
        build_client.return_value = client

        response = self.client.get(self.info_url("movie", "27205", "tmdb"), HTTP_HX_REQUEST="true")
        open_url = reverse("simkl-open", kwargs={"media_type": "movie", "simkl_id": 250822})
        self.assertContains(response, f'href="{open_url}"')

        client.get_detail.return_value = {"title": "Interstellar", "ids": {"simkl": 250822, "tmdb": "157336"}}
        redirect = self.client.get(open_url)
        self.assertEqual(redirect.status_code, 302)
        self.assertEqual(redirect["Location"], reverse("movie-detail", kwargs={"external_id": "157336"}))
        # Resolution is cached: the second click costs no SIMKL call.
        self.client.get(open_url)
        self.assertEqual(client.get_detail.call_count, 2)

    @patch("apps.simkl.enrichment.build_client")
    def test_resolver_falls_back_to_simkl_when_no_external_id(self, build_client):
        client = fake_client()
        client.get_detail.return_value = {"title": "Obscure", "ids": {"simkl": 99, "slug": "obscure"}}
        build_client.return_value = client

        redirect = self.client.get(reverse("simkl-open", kwargs={"media_type": "show", "simkl_id": 99}))
        self.assertEqual(redirect["Location"], "https://simkl.com/tv/99/")

        # SIMKL detail records label television as "tv".
        client.get_detail.return_value = {"title": "Lost", "ids": {"simkl": 2205, "tvdb": "73739"}}
        redirect = self.client.get(reverse("simkl-open", kwargs={"media_type": "tv", "simkl_id": 2205}))
        self.assertEqual(redirect["Location"], reverse("tv-detail", kwargs={"external_id": "73739"}))

    def test_card_requires_htmx_and_known_media_type(self):
        self.assertEqual(self.client.get(self.info_url("movie", "27205", "tmdb")).status_code, 403)
        response = self.client.get(self.info_url("book", "1", "tmdb"), HTTP_HX_REQUEST="true")
        self.assertEqual(response.status_code, 404)

    @override_settings(SIMKL_CLIENT_ID="")
    def test_card_explains_missing_configuration(self):
        response = self.client.get(self.info_url("movie", "27205", "tmdb"), HTTP_HX_REQUEST="true")

        self.assertContains(response, "not configured")
