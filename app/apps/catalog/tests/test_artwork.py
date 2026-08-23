from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from apps.catalog.artwork import (
    media_language_for_user,
    resolve_media_artwork,
    sync_media_artworks,
)
from apps.catalog.models import MediaArtwork, UserMediaArtworkPreference
from apps.catalog.providers.base import ArtworkDTO, DetailDTO
from apps.movies.models import Movie


class MediaArtworkBulkSyncTests(TestCase):
    """A single series can carry hundreds of artworks, so the reconciliation
    must not scale its statement count with the size of the collection."""

    def _detail(self, count, *, score_offset=0):
        return DetailDTO(
            provider="tvdb",
            external_id="121361",
            title="Game of Thrones",
            artworks=[
                ArtworkDTO(
                    kind="poster" if index % 2 else "background",
                    image_url=f"https://artworks.thetvdb.com/{index}.jpg",
                    score=float(index + score_offset),
                )
                for index in range(count)
            ],
        )

    def test_large_collections_sync_in_a_constant_number_of_statements(self):
        count = 400

        with CaptureQueriesContext(connection) as inserting:
            sync_media_artworks(self._detail(count), media_type="tv")
        with CaptureQueriesContext(connection) as unchanged:
            sync_media_artworks(self._detail(count), media_type="tv")
        with CaptureQueriesContext(connection) as updating:
            sync_media_artworks(
                self._detail(count, score_offset=1),
                media_type="tv",
            )

        self.assertEqual(MediaArtwork.objects.count(), count)
        self.assertLess(len(inserting), 10)
        self.assertLess(len(unchanged), 10)
        self.assertLess(len(updating), 10)

    def test_changed_values_are_written_and_stale_rows_are_pruned(self):
        sync_media_artworks(self._detail(5), media_type="tv")

        sync_media_artworks(self._detail(3, score_offset=100), media_type="tv")

        rows = MediaArtwork.objects.order_by("image_url")
        self.assertEqual(rows.count(), 3)
        self.assertEqual(
            sorted(row.score for row in rows),
            [100.0, 101.0, 102.0],
        )

    def test_an_incomplete_response_keeps_rows_it_did_not_mention(self):
        sync_media_artworks(self._detail(5), media_type="tv")

        sync_media_artworks(
            DetailDTO(
                provider="tvdb",
                external_id="121361",
                title="Game of Thrones",
                poster_path="https://artworks.thetvdb.com/only.jpg",
                artworks=None,
            ),
            media_type="tv",
        )

        self.assertEqual(MediaArtwork.objects.count(), 6)


class MediaArtworkServiceTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(email="viewer@example.com")
        self.user.settings.tmdb_metadata_language = "pt-BR"
        self.user.settings.save(update_fields=["tmdb_metadata_language"])
        self.movie = Movie.objects.create(
            provider="tmdb",
            external_id="550",
            title="Fight Club",
            poster_path="/legacy-poster.jpg",
            backdrop_path="/legacy-background.jpg",
        )

    def test_sync_replaces_stale_artwork_and_preserves_provider_defaults(self):
        stale = MediaArtwork.objects.create(
            provider="tmdb",
            media_type="movie",
            external_id="550",
            kind=MediaArtwork.Kind.POSTER,
            image_url="https://image.tmdb.org/t/p/original/stale.jpg",
        )
        detail = DetailDTO(
            provider="tmdb",
            external_id="550",
            title="Fight Club",
            poster_path="/default-poster.jpg",
            backdrop_path="/default-background.jpg",
            artworks=[
                ArtworkDTO(
                    kind="poster",
                    image_url="https://image.tmdb.org/t/p/original/default-poster.jpg",
                    language="en",
                    score=8.0,
                    is_default=True,
                ),
                ArtworkDTO(
                    kind="background",
                    image_url="https://image.tmdb.org/t/p/original/default-background.jpg",
                    score=7.0,
                    is_default=True,
                ),
            ],
        )

        sync_media_artworks(detail, media_type="movie")

        self.assertFalse(MediaArtwork.objects.filter(id=stale.id).exists())
        self.assertTrue(
            MediaArtwork.objects.get(
                provider="tmdb",
                media_type="movie",
                external_id="550",
                kind="poster",
                is_default=True,
            ).image_url.endswith("default-poster.jpg")
        )

    def test_sync_tracks_retained_artwork_by_kind(self):
        shared_url = "https://image.tmdb.org/t/p/original/shared.jpg"
        stale_background = MediaArtwork.objects.create(
            provider="tmdb",
            media_type="movie",
            external_id="550",
            kind=MediaArtwork.Kind.BACKGROUND,
            image_url=shared_url,
        )
        detail = DetailDTO(
            provider="tmdb",
            external_id="550",
            title="Fight Club",
            artworks=[
                ArtworkDTO(
                    kind=MediaArtwork.Kind.POSTER,
                    image_url=shared_url,
                    is_default=True,
                )
            ],
        )

        sync_media_artworks(detail, media_type="movie")

        self.assertTrue(
            MediaArtwork.objects.filter(
                provider="tmdb",
                media_type="movie",
                external_id="550",
                kind=MediaArtwork.Kind.POSTER,
                image_url=shared_url,
            ).exists()
        )
        self.assertFalse(MediaArtwork.objects.filter(id=stale_background.id).exists())

    def test_sync_keeps_artwork_rows_when_provider_response_is_incomplete(self):
        existing = MediaArtwork.objects.create(
            provider="tmdb",
            media_type="movie",
            external_id="550",
            kind=MediaArtwork.Kind.POSTER,
            image_url="https://image.tmdb.org/t/p/original/existing.jpg",
        )

        sync_media_artworks(
            DetailDTO(
                provider="tmdb",
                external_id="550",
                title="Fight Club",
                artworks=None,
            ),
            media_type="movie",
        )

        self.assertTrue(MediaArtwork.objects.filter(id=existing.id).exists())

    def test_incomplete_response_seeds_missing_legacy_artwork(self):
        sync_media_artworks(
            DetailDTO(
                provider="tmdb",
                external_id="550",
                title="Fight Club",
                poster_path="/legacy-poster.jpg",
                backdrop_path="/legacy-background.jpg",
                artworks=None,
            ),
            media_type="movie",
        )

        self.assertEqual(
            set(
                MediaArtwork.objects.values_list("kind", "image_url", "is_default")
            ),
            {
                (
                    MediaArtwork.Kind.POSTER,
                    "https://image.tmdb.org/t/p/w342/legacy-poster.jpg",
                    True,
                ),
                (
                    MediaArtwork.Kind.BACKGROUND,
                    "https://image.tmdb.org/t/p/w1280/legacy-background.jpg",
                    True,
                ),
            },
        )

    def test_resolver_prefers_profile_language_then_falls_back_to_default(self):
        default = MediaArtwork.objects.create(
            provider="tmdb",
            media_type="movie",
            external_id="550",
            kind=MediaArtwork.Kind.POSTER,
            image_url="https://image.tmdb.org/t/p/original/default.jpg",
            language="en",
            is_default=True,
        )
        localized = MediaArtwork.objects.create(
            provider="tmdb",
            media_type="movie",
            external_id="550",
            kind=MediaArtwork.Kind.POSTER,
            image_url="https://image.tmdb.org/t/p/original/portuguese.jpg",
            language="pt",
            score=5.0,
        )

        self.assertEqual(media_language_for_user(self.user, self.movie), "pt-BR")
        self.assertEqual(
            resolve_media_artwork(self.user, self.movie, MediaArtwork.Kind.POSTER),
            localized.image_url,
        )

        localized.delete()
        self.assertEqual(
            resolve_media_artwork(self.user, self.movie, MediaArtwork.Kind.POSTER),
            default.image_url,
        )

    def test_automatic_background_prefers_provider_default_over_profile_language(self):
        default = MediaArtwork.objects.create(
            provider="tmdb",
            media_type="movie",
            external_id="550",
            kind=MediaArtwork.Kind.BACKGROUND,
            image_url="https://image.tmdb.org/t/p/original/default-background.jpg",
            language="en",
            is_default=True,
        )
        MediaArtwork.objects.create(
            provider="tmdb",
            media_type="movie",
            external_id="550",
            kind=MediaArtwork.Kind.BACKGROUND,
            image_url="https://image.tmdb.org/t/p/original/portuguese-background.jpg",
            language="pt",
            score=10.0,
        )

        self.assertEqual(
            resolve_media_artwork(self.user, self.movie, MediaArtwork.Kind.BACKGROUND),
            default.image_url,
        )

    def test_explicit_user_selection_wins_but_deleted_selection_falls_back(self):
        default = MediaArtwork.objects.create(
            provider="tmdb",
            media_type="movie",
            external_id="550",
            kind=MediaArtwork.Kind.BACKGROUND,
            image_url="https://image.tmdb.org/t/p/original/default-background.jpg",
            is_default=True,
        )
        selected = MediaArtwork.objects.create(
            provider="tmdb",
            media_type="movie",
            external_id="550",
            kind=MediaArtwork.Kind.BACKGROUND,
            image_url="https://image.tmdb.org/t/p/original/cosmetic-background.jpg",
            language=None,
        )
        preference = UserMediaArtworkPreference.objects.create(
            user=self.user,
            provider="tmdb",
            media_type="movie",
            external_id="550",
            background_artwork=selected,
        )

        self.assertEqual(
            resolve_media_artwork(self.user, self.movie, MediaArtwork.Kind.BACKGROUND),
            selected.image_url,
        )

        selected.delete()
        preference.refresh_from_db()
        self.assertIsNone(preference.background_artwork_id)
        self.assertEqual(
            resolve_media_artwork(self.user, self.movie, MediaArtwork.Kind.BACKGROUND),
            default.image_url,
        )
