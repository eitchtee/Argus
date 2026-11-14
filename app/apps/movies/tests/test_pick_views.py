import json

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.catalog.models import Genre
from apps.movies.models import Movie, UserMovie


@override_settings(
    STORAGES={
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {
            "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"
        },
    },
    DJANGO_VITE_DEV_MODE=True,
)
class MoviePickViewTests(TestCase):
    def setUp(self):
        from django_vite.core.asset_loader import DjangoViteAssetLoader

        DjangoViteAssetLoader._instance = None
        self.user = get_user_model().objects.create_user(
            "user@example.com", password="password"
        )
        self.client.login(username="user@example.com", password="password")

    def tearDown(self):
        from django_vite.core.asset_loader import DjangoViteAssetLoader

        DjangoViteAssetLoader._instance = None

    def test_requires_authentication(self):
        self.client.logout()

        response = self.client.get(reverse("movies-pick-page"))

        self.assertEqual(response.status_code, 302)

    def test_pool_holds_only_unwatched_watchlist_movies(self):
        drama = Genre.objects.create(provider="tmdb", external_id="18", name="Drama")
        wanted = Movie.objects.create(
            external_id="550",
            title="Fight Club",
            poster_path="/poster.jpg",
            runtime=139,
            vote_average=8.438,
            director="David Fincher",
            cast=[{"name": "Brad Pitt", "character": "Tyler"}],
        )
        wanted.genres.add(drama)
        seen = Movie.objects.create(external_id="13", title="Forrest Gump")
        other = Movie.objects.create(external_id="680", title="Pulp Fiction")
        UserMovie.objects.create(user=self.user, movie=wanted, on_watchlist=True)
        UserMovie.objects.create(user=self.user, movie=seen, on_watchlist=True, is_seen=True)
        UserMovie.objects.create(user=self.user, movie=other, on_watchlist=False)

        response = self.client.get(reverse("movies-pick-page"))

        pool = json.loads(
            response.content.decode().split('id="movie-pick-pool" type="application/json">')[1].split("</script>")[0]
        )
        self.assertEqual(len(pool), 1)
        self.assertEqual(pool[0]["title"], "Fight Club")
        self.assertEqual(pool[0]["genres"], ["Drama"])
        self.assertEqual(pool[0]["ratings"], [{"source": "tmdb", "label": "TMDB", "value": "8.4"}])
        self.assertContains(response, '<option value="Brad Pitt">Brad Pitt</option>', html=True)
        self.assertEqual(pool[0]["people"], ["David Fincher", "Brad Pitt"])
        self.assertEqual(pool[0]["url"], reverse("movie-detail", kwargs={"external_id": "550"}))

    def test_empty_watchlist_shows_hint(self):
        response = self.client.get(reverse("movies-pick-page"))

        self.assertNotContains(response, "movie-pick-pool")

    def test_synced_ratings_lead_and_tmdb_is_the_fallback(self):
        from django.contrib.contenttypes.models import ContentType

        from apps.mdblist.models import MdblistMediaInfo
        from apps.simkl.models import SimklMediaInfo

        movie = Movie.objects.create(external_id="550", title="Fight Club", vote_average=8.4)
        UserMovie.objects.create(user=self.user, movie=movie, on_watchlist=True)
        target = {"content_type": ContentType.objects.get_for_model(Movie), "object_id": movie.pk}
        MdblistMediaInfo.objects.create(
            **target,
            score=81,
            ratings=[{"source": "tomatoes", "value": 79}, {"source": "imdb", "value": None}],
        )
        SimklMediaInfo.objects.create(**target, imdb_rating=8.8, simkl_rating=8.5)

        response = self.client.get(reverse("movies-pick-page"))

        pool = json.loads(
            response.content.decode().split('id="movie-pick-pool" type="application/json">')[1].split("</script>")[0]
        )
        self.assertEqual(
            [(rating["label"], rating["value"]) for rating in pool[0]["ratings"]],
            [("IMDb", "8.8"), ("Rotten Tomatoes", "79%"), ("MDBList", "81"), ("Simkl", "8.5"), ("TMDB", "8.4")],
        )
