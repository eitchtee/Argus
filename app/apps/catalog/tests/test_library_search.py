from datetime import date

from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.catalog.library import search_library
from apps.movies.models import Movie, UserMovie
from apps.tv.models import Episode, Season, Show, UserShow


class SearchLibraryTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user("user@example.com", password="password")
        self.other = User.objects.create_user("other@example.com", password="password")
        self.user.settings.tmdb_metadata_language = "pt-BR"
        self.user.settings.save()

        self.movie = Movie.objects.create(
            external_id="1",
            title="Star Wars",
            translations={"pt-BR": {"title": "Guerra nas Estrelas"}},
        )
        UserMovie.objects.create(user=self.user, movie=self.movie, on_watchlist=True)
        self.foreign = Movie.objects.create(external_id="2", title="Star Trek")
        UserMovie.objects.create(user=self.other, movie=self.foreign, on_watchlist=True)

        self.show = Show.objects.create(external_id="10", name="Andor: A Star Wars Story")
        UserShow.objects.create(user=self.user, show=self.show)
        season = Season.objects.create(show=self.show, season_number=1)
        self.episode = Episode.objects.create(
            show=self.show,
            season=season,
            season_number=1,
            episode_number=3,
            name="Star of Kassa",
            air_date=date(2022, 9, 21),
        )

    def test_only_user_library_with_titles_before_episodes(self):
        results = search_library(self.user, "star")

        self.assertEqual(
            [item["record"] for item in results["titles"]],
            [self.show, self.movie],
        )
        self.assertEqual([item["record"] for item in results["episodes"]], [self.episode])

    def test_matches_translated_title_and_ranks_prefix_first(self):
        results = search_library(self.user, "guerra")
        self.assertEqual([item["title"] for item in results["titles"]], ["Guerra nas Estrelas"])

        results = search_library(self.user, "and")
        self.assertEqual(results["titles"][0]["record"], self.show)

    def test_short_query_returns_nothing(self):
        self.assertEqual(search_library(self.user, "s"), {"titles": [], "episodes": []})
