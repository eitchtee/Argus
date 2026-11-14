from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.catalog.aggregate import PRIOR, aggregate, refresh_aggregate_score
from apps.mdblist.enrichment import _store
from apps.mdblist.models import MdblistMediaInfo
from apps.movies.models import Movie, UserMovie
from apps.simkl.models import SimklMediaInfo
from apps.tv.models import Show, UserShow


class AggregateTests(TestCase):
    def test_no_usable_rating_gives_no_score(self):
        self.assertIsNone(aggregate({}))
        self.assertIsNone(aggregate({"tomatoes": (95, 300), "imdb": (None, 10)}))

    def test_sources_are_put_on_one_scale(self):
        # 8.0/10, 4.0/5 and 80/100 are the same opinion.
        heavy = 10**9
        score = aggregate({"imdb": (8.0, heavy), "letterboxd": (4.0, heavy), "metacritic": (80, heavy)})
        self.assertEqual(score, round((0.8 * 3 * 100 + 0.5 * PRIOR) / 3.5))

    def test_a_few_votes_barely_move_the_prior(self):
        obscure = aggregate({"tmdb": (100, 3)})
        popular = aggregate({"tmdb": (100, 50_000)})
        self.assertLess(obscure, 70)
        self.assertGreater(popular, 85)

    def test_tomatometer_is_ignored(self):
        self.assertEqual(
            aggregate({"imdb": (7.0, 10_000), "tomatoes": (100, 400)}),
            aggregate({"imdb": (7.0, 10_000)}),
        )


class RefreshAggregateScoreTests(TestCase):
    def setUp(self):
        self.movie = Movie.objects.create(external_id="550", title="Fight Club", vote_average=8.4, vote_count=30_000)
        self.target = {"content_type": ContentType.objects.get_for_model(Movie), "object_id": self.movie.pk}

    def test_combines_tmdb_simkl_and_mdblist_with_mdblist_winning_overlaps(self):
        SimklMediaInfo.objects.create(**self.target, imdb_rating=5.0, imdb_votes=1000, simkl_rating=8.5, simkl_votes=900)
        MdblistMediaInfo.objects.create(**self.target, ratings=[{"source": "imdb", "value": 8.8, "votes": 2_000_000}])

        score = refresh_aggregate_score(self.movie)

        self.movie.refresh_from_db()
        self.assertEqual(self.movie.aggregate_score, score)
        self.assertEqual(
            score,
            aggregate({"tmdb": (84.0, 30_000), "simkl": (8.5, 900), "imdb": (8.8, 2_000_000)}),
        )

    def test_failed_lookups_and_tvdb_scores_do_not_count(self):
        MdblistMediaInfo.objects.create(
            **self.target, status=MdblistMediaInfo.Status.ERROR, ratings=[{"source": "imdb", "value": 1.0}]
        )
        tvdb_movie = Movie.objects.create(provider="tvdb", external_id="9", title="X", vote_average=9_999)

        self.assertEqual(refresh_aggregate_score(self.movie), aggregate({"tmdb": (84.0, 30_000)}))
        self.assertIsNone(refresh_aggregate_score(tvdb_movie))

    def test_storing_mdblist_data_updates_the_score(self):
        info = MdblistMediaInfo.objects.create(**self.target)
        data = {
            "media_type": "movie", "mdblist_id": "", "slug": "", "imdb_id": "", "score": None,
            "ratings": [{"source": "letterboxd", "value": 4.5, "votes": 900_000}],
            "certification": "", "trailer_url": "", "streams": [], "recommendations": [],
        }

        _store(info, data)

        self.movie.refresh_from_db()
        self.assertEqual(
            self.movie.aggregate_score,
            aggregate({"tmdb": (84.0, 30_000), "letterboxd": (4.5, 900_000)}),
        )


class DetailPageScoreTests(TestCase):
    def setUp(self):
        user = get_user_model().objects.create_user("user@example.com", password="password")
        self.client.login(username="user@example.com", password="password")
        movie = Movie.objects.create(
            external_id="550", title="Fight Club", vote_average=8.4, aggregate_score=81, last_synced_at=timezone.now()
        )
        UserMovie.objects.create(user=user, movie=movie, on_watchlist=True)
        show = Show.objects.create(external_id="73739", name="Lost", aggregate_score=77, last_synced_at=timezone.now())
        UserShow.objects.create(user=user, show=show)

    def test_movie_and_show_pages_show_the_argus_score_not_tmdb(self):
        response = self.client.get(
            reverse("movie-detail-content", kwargs={"external_id": "550"}), HTTP_HX_REQUEST="true"
        )
        self.assertContains(response, "81<span")
        self.assertNotContains(response, "8.4<span")

        response = self.client.get(
            reverse("tv-detail-content", kwargs={"external_id": "73739"}), HTTP_HX_REQUEST="true"
        )
        self.assertContains(response, "77<span")
