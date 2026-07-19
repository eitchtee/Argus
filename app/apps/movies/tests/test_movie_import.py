from django.test import TestCase

from apps.catalog.models import Genre, SyncStatus
from apps.catalog.providers.base import CastMemberDTO, DetailDTO, GenreDTO
from apps.catalog.providers.exceptions import ProviderError
from apps.movies.models import Movie
from apps.movies.services import import_movie


class FakeProvider:
    def __init__(self, detail=None, error=None):
        self.detail = detail
        self.error = error
        self.calls = []

    def fetch_detail(self, external_id, *, language, media_type="movie"):
        self.calls.append((external_id, language, media_type))
        if self.error:
            raise self.error
        return self.detail


def movie_detail(**overrides):
    defaults = {
        "provider": "tmdb",
        "external_id": "550",
        "imdb_id": "tt0137523",
        "tmdb_id": "550",
        "tvdb_id": "42",
        "title": "Fight Club",
        "original_title": "Fight Club",
        "overview": "Overview",
        "tagline": "Mischief. Mayhem. Soap.",
        "poster_path": "/poster.jpg",
        "backdrop_path": "/backdrop.jpg",
        "release_date": "1999-10-15",
        "runtime": 139,
        "status": "Released",
        "vote_average": 8.4,
        "vote_count": 29400,
        "director": "David Fincher",
        "trailer_url": "https://www.youtube.com/watch?v=SUXWAEX2jlg",
        "cast": [
            CastMemberDTO(name="Edward Norton", character="The Narrator", photo_url="/norton.jpg"),
        ],
        "genres": [
            GenreDTO(provider="tmdb", external_id="18", name="Drama"),
            GenreDTO(provider="tmdb", external_id="53", name="Thriller"),
        ],
    }
    defaults.update(overrides)
    return DetailDTO(**defaults)


class MovieImportTests(TestCase):
    def test_import_movie_merges_movie_and_genre_translations(self):
        provider = FakeProvider(
            movie_detail(
                translations={"pt-BR": {"title": "Clube da Luta"}},
                genres=[
                    GenreDTO(
                        provider="tmdb",
                        external_id="18",
                        name="Drama",
                        translations={"pt-BR": {"name": "Drama"}},
                    )
                ],
            )
        )
        Movie.objects.create(
            external_id="550",
            title="Fight Club",
            translations={"en-US": {"title": "Fight Club"}},
        )

        movie = import_movie(
            "tmdb",
            "550",
            language="pt-BR",
            provider_getter=lambda _name: provider,
        )

        self.assertEqual(movie.translations["en-US"]["title"], "Fight Club")
        self.assertEqual(movie.translations["pt-BR"]["title"], "Clube da Luta")
        self.assertEqual(movie.genres.get().translations["pt-BR"]["name"], "Drama")
    def test_import_movie_creates_movie_and_genres(self):
        provider = FakeProvider(movie_detail())

        movie = import_movie(
            "tmdb",
            "550",
            provider_getter=lambda provider_name: provider,
        )

        self.assertEqual(provider.calls, [("550", "en-US", "movie")])
        self.assertEqual(movie.external_id, "550")
        self.assertEqual(movie.imdb_id, "tt0137523")
        self.assertEqual(movie.tmdb_id, "550")
        self.assertEqual(movie.tvdb_id, "42")
        self.assertEqual(movie.title, "Fight Club")
        self.assertEqual(movie.release_date.isoformat(), "1999-10-15")
        self.assertEqual(movie.runtime, 139)
        self.assertEqual(movie.sync_status, SyncStatus.OK)
        self.assertIsNotNone(movie.last_synced_at)
        self.assertEqual(movie.director, "David Fincher")
        self.assertEqual(movie.trailer_url, "https://www.youtube.com/watch?v=SUXWAEX2jlg")
        self.assertEqual(
            movie.cast,
            [{"name": "Edward Norton", "character": "The Narrator", "photo_url": "/norton.jpg"}],
        )
        self.assertEqual(
            list(movie.genres.order_by("external_id").values_list("name", flat=True)),
            ["Drama", "Thriller"],
        )
        self.assertEqual(Genre.objects.count(), 2)

    def test_import_movie_updates_existing_movie_without_duplicates(self):
        provider = FakeProvider(movie_detail(title="Fight Club Updated"))
        movie = Movie.objects.create(external_id="550", title="Old title")
        Genre.objects.create(provider="tmdb", external_id="18", name="Drama")

        imported_movie = import_movie(
            "tmdb",
            "550",
            provider_getter=lambda provider_name: provider,
        )

        self.assertEqual(imported_movie.id, movie.id)
        self.assertEqual(imported_movie.title, "Fight Club Updated")
        self.assertEqual(Movie.objects.count(), 1)
        self.assertEqual(Genre.objects.count(), 2)

    def test_import_movie_replaces_genre_membership_with_provider_detail(self):
        old_genre = Genre.objects.create(provider="tmdb", external_id="99", name="Old")
        movie = Movie.objects.create(external_id="550", title="Old title")
        movie.genres.add(old_genre)
        provider = FakeProvider(
            movie_detail(genres=[GenreDTO(provider="tmdb", external_id="18", name="Drama")])
        )

        imported_movie = import_movie(
            "tmdb",
            "550",
            provider_getter=lambda provider_name: provider,
        )

        self.assertEqual(
            list(imported_movie.genres.values_list("external_id", flat=True)),
            ["18"],
        )

    def test_provider_error_marks_existing_movie_error_without_corrupting_metadata(self):
        movie = Movie.objects.create(external_id="550", title="Fight Club")
        provider = FakeProvider(error=ProviderError("provider down"))

        with self.assertRaises(ProviderError):
            import_movie("tmdb", "550", provider_getter=lambda provider_name: provider)

        movie.refresh_from_db()
        self.assertEqual(movie.title, "Fight Club")
        self.assertEqual(movie.sync_status, SyncStatus.ERROR)
        self.assertIsNone(movie.last_synced_at)

    def test_import_movie_supports_tvdb_provider_metadata(self):
        provider = FakeProvider(
            movie_detail(
                provider="tvdb",
                external_id="42",
                title="A Movie",
                genres=[GenreDTO(provider="tvdb", external_id="1", name="Drama")],
            )
        )

        movie = import_movie(
            "tvdb",
            "42",
            language="eng",
            provider_getter=lambda provider_name: provider,
        )

        self.assertEqual(movie.provider, "tvdb")
        self.assertEqual(movie.external_id, "42")
        self.assertEqual(provider.calls, [("42", "eng", "movie")])
        self.assertEqual(movie.genres.get().provider, "tvdb")

    def test_import_movie_refreshes_cross_provider_ids(self):
        provider = FakeProvider(movie_detail(tvdb_id="42"))
        movie = import_movie(
            "tmdb",
            "550",
            provider_getter=lambda provider_name: provider,
        )

        provider.detail = movie_detail(tmdb_id="551", tvdb_id="43")
        refreshed = import_movie(
            "tmdb",
            "550",
            provider_getter=lambda provider_name: provider,
        )

        self.assertEqual(refreshed.id, movie.id)
        self.assertEqual(refreshed.tmdb_id, "551")
        self.assertEqual(refreshed.tvdb_id, "43")

    def test_import_rejects_unknown_provider(self):
        with self.assertRaisesMessage(ValueError, "Unsupported provider"):
            import_movie("other", "550", provider_getter=lambda provider_name: FakeProvider())
