from datetime import timedelta
from unittest.mock import Mock

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.movies.models import Movie, UserMovie
from apps.simkl.client import RedirectTarget, SimklError
from apps.simkl.enrichment import (
    poster_url,
    refresh_media_info,
    stale_media_queryset,
)
from apps.simkl.models import SimklMediaInfo
from apps.tv.models import Show, UserShow


MOVIE_DETAIL = {
    "title": "Inception",
    "type": "movie",
    "ids": {"simkl": 472214, "slug": "inception", "tmdb": "27205", "imdb": "tt1375666"},
    "rank": 20,
    "droprate": "0.1%",
    "certification": "PG-13",
    "ratings": {"simkl": {"rating": 8.6, "votes": 11454}, "imdb": {"rating": 8.8, "votes": 2816410}},
    "trailers": [
        {"name": "Teaser", "youtube": "teaser", "size": 720},
        {"name": "Official Trailer", "youtube": "Jvurpf91omw", "size": 1080},
    ],
    "users_recommendations": [
        {"title": "Interstellar", "year": 2014, "poster": "20/2052598c2716ef054", "type": "movie", "ids": {"simkl": 250822, "slug": "interstellar"}},
    ],
}


class EnrichmentTests(TestCase):
    def setUp(self):
        self.movie = Movie.objects.create(external_id="27205", tmdb_id="27205", imdb_id="tt1375666", title="Inception", last_synced_at=timezone.now())

    def test_refresh_resolves_id_and_stores_community_data(self):
        client = Mock()
        client.resolve.return_value = RedirectTarget(section="movies", simkl_id=472214)
        client.get_detail.return_value = MOVIE_DETAIL

        info = refresh_media_info(self.movie, client=client)

        client.resolve.assert_called_once_with(media_type="movie", imdb="tt1375666", tmdb="27205", tvdb=None)
        client.get_detail.assert_called_once_with("movie", 472214)
        self.assertEqual(info.status, SimklMediaInfo.Status.OK)
        self.assertEqual(info.simkl_id, 472214)
        self.assertEqual(info.slug, "inception")
        self.assertEqual(info.simkl_rating, 8.6)
        self.assertEqual(info.imdb_votes, 2816410)
        self.assertEqual(info.rank, 20)
        self.assertEqual(info.drop_rate, "0.1%")
        self.assertEqual(info.certification, "PG-13")
        self.assertEqual(info.trailer_url, "https://www.youtube.com/watch?v=Jvurpf91omw")
        self.assertEqual(info.recommendations[0]["simkl_id"], 250822)
        self.assertEqual(info.simkl_url, "https://simkl.com/movies/472214/inception")

    def test_refresh_reuses_known_simkl_id(self):
        SimklMediaInfo.objects.create(
            content_type=ContentType.objects.get_for_model(Movie),
            object_id=self.movie.pk,
            media_type="movie",
            simkl_id=472214,
        )
        client = Mock()
        client.get_detail.return_value = MOVIE_DETAIL

        refresh_media_info(self.movie, client=client)

        client.resolve.assert_not_called()

    def test_unresolvable_titles_are_marked_not_found(self):
        client = Mock()
        client.resolve.return_value = None

        info = refresh_media_info(self.movie, client=client)

        self.assertEqual(info.status, SimklMediaInfo.Status.NOT_FOUND)
        client.get_detail.assert_not_called()

    def test_api_errors_are_recorded_and_raised(self):
        client = Mock()
        client.resolve.side_effect = SimklError("down", status_code=503)

        with self.assertRaises(SimklError):
            refresh_media_info(self.movie, client=client)

        info = SimklMediaInfo.objects.get(object_id=self.movie.pk)
        self.assertEqual(info.status, SimklMediaInfo.Status.ERROR)
        self.assertEqual(info.last_error, "down")

    @override_settings(SIMKL_METADATA_REFRESH_DAYS=7)
    def test_stale_queryset_picks_tracked_titles_without_fresh_info(self):
        user = get_user_model().objects.create_user("user@example.com")
        UserMovie.objects.create(user=user, movie=self.movie)
        untracked = Movie.objects.create(external_id="1", tmdb_id="1", title="Untracked", last_synced_at=timezone.now())
        show = Show.objects.create(external_id="73739", tvdb_id="73739", name="Lost", last_synced_at=timezone.now())
        UserShow.objects.create(user=user, show=show)
        fresh_show = Show.objects.create(external_id="1", tvdb_id="1", name="Fresh", last_synced_at=timezone.now())
        UserShow.objects.create(user=user, show=fresh_show)
        client = Mock()
        client.resolve.return_value = RedirectTarget(section="tv", simkl_id=1)
        client.get_detail.return_value = {"title": "Fresh", "ids": {"simkl": 1}}
        refresh_media_info(fresh_show, client=client)
        stale = SimklMediaInfo.objects.get(object_id=fresh_show.pk)
        stale.fetched_at = timezone.now() - timedelta(days=30)
        stale.save(update_fields=["fetched_at"])

        results = stale_media_queryset(limit=10)

        self.assertEqual({(type(item).__name__, item.pk) for item in results}, {("Movie", self.movie.pk), ("Show", show.pk), ("Show", fresh_show.pk)})
        self.assertNotIn(untracked, results)

    def test_poster_url_uses_the_image_proxy(self):
        self.assertEqual(
            poster_url("74/74415673dcdc9cdd"),
            "https://wsrv.nl/?url=https://simkl.in/posters/74/74415673dcdc9cdd_ca.webp&q=90",
        )
        self.assertIsNone(poster_url(None))
