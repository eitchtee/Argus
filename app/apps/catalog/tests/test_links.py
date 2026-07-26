from django.test import SimpleTestCase

from apps.catalog.links import build_external_links


class ExternalLinkTests(SimpleTestCase):
    def test_external_links_include_provider_and_known_ids_in_reading_order(self):
        links = build_external_links(
            "tv",
            provider="tvdb",
            external_id="121361",
            title="Game of Thrones",
            imdb_id="tt0944947",
            tmdb_id="1399",
            tvdb_id="121361",
            trakt_id="353",
        )

        self.assertEqual(
            [link["label"] for link in links],
            ["TVDB", "TMDB", "IMDb", "Trakt"],
        )
        self.assertEqual(
            links[0]["url"],
            "https://thetvdb.com/dereferrer/series/121361",
        )
        self.assertEqual(
            links[1]["url"],
            "https://www.themoviedb.org/tv/1399",
        )

    def test_movie_links_use_movie_path_and_skip_missing_ids(self):
        links = build_external_links(
            "movie",
            provider="tmdb",
            external_id="550",
            title="Fight Club",
            tmdb_id="550",
        )

        self.assertEqual([link["label"] for link in links], ["TMDB"])
        self.assertEqual(links[0]["url"], "https://www.themoviedb.org/movie/550")

    def test_trakt_link_uses_known_id_when_available(self):
        links = build_external_links(
            "tv",
            provider="tvdb",
            external_id="121361",
            title="Game of Thrones",
            trakt_id="353",
        )

        self.assertEqual(links[0]["label"], "TVDB")
        self.assertEqual(
            links[1]["url"],
            "https://trakt.tv/shows/353",
        )
