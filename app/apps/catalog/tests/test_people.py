from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, override_settings

from apps.catalog.providers.base import DetailDTO, PersonCreditDTO, PersonDTO
from apps.catalog.providers.tmdb import TMDBProvider
from apps.catalog.templatetags.people import person_url
from apps.catalog.tests.test_tmdb_provider import SequenceOpener
from apps.movies.models import Movie, UserMovie
from apps.tv.models import Show, UserShow


@override_settings(TMDB_IMAGE_BASE_URL="https://image.tmdb.org/t/p")
class TMDBPersonTests(SimpleTestCase):
    def test_fetch_person_merges_credits_and_falls_back_to_english_bio(self):
        opener = SequenceOpener(
            [
                {
                    "id": 17,
                    "name": "Shawn Levy",
                    "biography": "",
                    "known_for_department": "Directing",
                    "birthday": "1968-07-23",
                    "translations": {
                        "translations": [
                            {"iso_639_1": "fr", "data": {"biography": ""}},
                            {"iso_639_1": "en", "data": {"biography": "Canadian director."}},
                        ]
                    },
                    "combined_credits": {
                        "cast": [
                            {"id": 1, "media_type": "movie", "title": "Cameo", "character": "Himself", "release_date": "2010-01-01"},
                        ],
                        "crew": [
                            {"id": 533535, "media_type": "movie", "title": "Deadpool & Wolverine", "job": "Director", "release_date": "2024-07-24"},
                            {"id": 533535, "media_type": "movie", "title": "Deadpool & Wolverine", "job": "Producer", "release_date": "2024-07-24"},
                            {"id": 66732, "media_type": "tv", "name": "Stranger Things", "job": "Executive Producer", "first_air_date": "2016-07-15"},
                            {"id": 9, "media_type": "movie", "title": "Announced", "job": "Director"},
                        ],
                    },
                }
            ]
        )

        person = TMDBProvider(api_key="key", opener=opener).fetch_person("17", language="fr-FR")

        self.assertEqual(person.biography, "Canadian director.")
        self.assertEqual(
            [(c.media_type, c.tmdb_id, c.roles, c.jobs) for c in person.credits],
            [
                ("movie", "9", ["Director"], ["Director"]),
                ("movie", "533535", ["Director", "Producer"], ["Director", "Producer"]),
                ("tv", "66732", ["Executive Producer"], ["Executive Producer"]),
                ("movie", "1", ["Himself"], ["Acting"]),
            ],
        )

    def test_find_person_prefers_tvdb_id_then_name(self):
        opener = SequenceOpener([{"person_results": []}, {"results": [{"id": 10859}]}])

        person_id = TMDBProvider(api_key="key", opener=opener).find_person(tvdb_id="123", name="Ryan Reynolds")

        self.assertEqual(person_id, "10859")
        self.assertIn("/find/123", opener.requests[0][0].full_url)
        self.assertIn("/search/person", opener.requests[1][0].full_url)

    def test_person_url_uses_tmdb_id_or_lookup(self):
        self.assertEqual(person_url("Ryan Reynolds", "10859"), "/people/10859/")
        self.assertEqual(person_url("Ryan Reynolds", None, "77"), "/people/lookup/?tvdb=77&name=Ryan+Reynolds")
        self.assertEqual(person_url("Shawn Levy"), "/people/lookup/?name=Shawn+Levy")


@override_settings(
    STORAGES={
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    },
    DJANGO_VITE_DEV_MODE=True,
)
class PersonViewTests(TestCase):
    def setUp(self):
        from django_vite.core.asset_loader import DjangoViteAssetLoader

        DjangoViteAssetLoader._instance = None
        self.user = get_user_model().objects.create_user("user@example.com", password="password")
        self.client.login(username="user@example.com", password="password")

    @patch("apps.catalog.people.get_person")
    def test_content_marks_library_titles_and_counts_roles(self, get_person_mock):
        get_person_mock.return_value = PersonDTO(
            tmdb_id="17",
            name="Shawn Levy",
            birthday="1968-07-23",
            credits=[
                PersonCreditDTO(media_type="movie", tmdb_id="533535", title="Deadpool & Wolverine", roles=["Director", "Producer"], jobs=["Director", "Producer"]),
                PersonCreditDTO(media_type="movie", tmdb_id="2", title="Night at the Museum", roles=["Director"], jobs=["Director"]),
                PersonCreditDTO(media_type="tv", tmdb_id="66732", title="Stranger Things", jobs=["Executive Producer"]),
            ],
        )
        movie = Movie.objects.create(provider="tmdb", external_id="533535", title="Deadpool & Wolverine")
        UserMovie.objects.create(user=self.user, movie=movie, is_seen=True)
        show = Show.objects.create(provider="tvdb", external_id="305288", tmdb_id="66732", name="Stranger Things")
        UserShow.objects.create(user=self.user, show=show)

        response = self.client.get("/people/17/content/", HTTP_HX_REQUEST="true")

        self.assertContains(response, "Shawn Levy")
        movies, shows = response.context["movies"], response.context["shows"]
        self.assertEqual(
            [(m["href"], m["state"]) for m in movies],
            [("/people/titles/movie/533535/", "watched"), ("/people/titles/movie/2/", None)],
        )
        self.assertEqual(shows[0]["state"], "tracked")
        self.assertEqual(response.context["movie_jobs"], [("Director", 2), ("Producer", 1)])
        # Defaults: movies open from TMDB, shows from TVDB.
        self.assertContains(response, 'href="/people/titles/movie/2/?provider=tmdb"')
        self.assertContains(response, 'href="/people/titles/tv/66732/?provider=tvdb"')

    @patch("apps.catalog.people.find_tvdb_id")
    def test_title_redirect_follows_the_chosen_provider(self, find_mock):
        find_mock.return_value = "305288"
        cases = [
            ("/people/titles/tv/66732/", "/tv/305288/"),
            ("/people/titles/tv/66732/?provider=tmdb", "/tv/66732/?provider=tmdb"),
            ("/people/titles/movie/533535/", "/movies/533535/"),
            ("/people/titles/movie/533535/?provider=tvdb", "/movies/305288/?provider=tvdb"),
        ]
        for url, target in cases:
            with self.subTest(url=url):
                self.assertRedirects(self.client.get(url), target, fetch_redirect_response=False)

    @patch("apps.catalog.people.find_tvdb_id", return_value=None)
    def test_title_redirect_falls_back_to_tmdb_when_tvdb_lacks_it(self, _find_mock):
        response = self.client.get("/people/titles/tv/66732/")

        self.assertRedirects(response, "/tv/66732/?provider=tmdb", fetch_redirect_response=False)

    @patch("apps.catalog.people.find_person_id", return_value="10859")
    def test_lookup_redirects_to_person(self, find_mock):
        response = self.client.get("/people/lookup/?tvdb=77&name=Ryan+Reynolds")

        self.assertRedirects(response, "/people/10859/", fetch_redirect_response=False)
        find_mock.assert_called_once_with(tvdb_id="77", name="Ryan Reynolds")

    @patch("apps.catalog.people.find_person_id", return_value=None)
    def test_lookup_404s_when_nobody_matches(self, _find_mock):
        self.assertEqual(self.client.get("/people/lookup/?name=Nobody").status_code, 404)


class FindTvdbIdTests(SimpleTestCase):
    def setUp(self):
        from django.core.cache import cache

        cache.clear()

    def test_movies_go_through_imdb_and_shows_use_tmdbs_tvdb_id(self):
        from unittest.mock import MagicMock

        from apps.catalog.services import find_tvdb_id

        tmdb, tvdb = MagicMock(), MagicMock()
        # A real DTO: details are pickled into the cache.
        tmdb.fetch_detail.return_value = DetailDTO(
            provider="tmdb", external_id="1", title="", imdb_id="tt6263850", tvdb_id="305288"
        )
        tvdb.find_by_remote_id.return_value = "4711"
        getter = {"tmdb": tmdb, "tvdb": tvdb}.__getitem__

        self.assertEqual(find_tvdb_id("533535", "movie", language="en-US", provider_getter=getter), "4711")
        tvdb.find_by_remote_id.assert_called_once_with("tt6263850", "movie")
        self.assertEqual(find_tvdb_id("66732", "tv", language="en-US", provider_getter=getter), "305288")
