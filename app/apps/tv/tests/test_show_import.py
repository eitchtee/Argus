from datetime import time

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from apps.catalog.models import Genre, SyncStatus
from apps.catalog.providers.base import CastMemberDTO, DetailDTO, EpisodeDTO, GenreDTO, SeasonDTO
from apps.catalog.providers.exceptions import ProviderError
from apps.tv.models import Episode, Season, Show, UserEpisode, UserShow
from apps.tv.services import import_show


class FakeProvider:
    def __init__(self, detail=None, episodes=None, seasons=None, detail_error=None, episodes_error=None):
        self.detail = detail
        self.episodes = episodes or []
        self.seasons = seasons or []
        self.detail_error = detail_error
        self.episodes_error = episodes_error
        self.detail_calls = []
        self.episode_calls = []
        self.season_calls = []

    def fetch_detail(self, external_id, *, language, media_type="tv"):
        self.detail_calls.append((external_id, language, media_type))
        if self.detail_error:
            raise self.detail_error
        return self.detail

    def fetch_episodes(self, external_id, *, language):
        self.episode_calls.append((external_id, language))
        if self.episodes_error:
            raise self.episodes_error
        return self.episodes

    def fetch_seasons(self, external_id, *, language):
        self.season_calls.append((external_id, language))
        return self.seasons


def show_detail(**overrides):
    defaults = {
        "provider": "tvdb",
        "external_id": "121361",
        "title": "Game of Thrones",
        "original_title": "Game of Thrones",
        "overview": "Nine noble families fight for control.",
        "poster_path": "https://artworks.thetvdb.com/poster.jpg",
        "backdrop_path": None,
        "release_date": "2011-04-17",
        "status": "Ended",
        "network": "HBO",
        "imdb_id": "tt0944947",
        "tmdb_id": "1399",
        "tvdb_id": "121361",
        "trailer_url": "https://www.youtube.com/watch?v=abc123",
        "cast": [
            CastMemberDTO(
                name="Emilia Clarke",
                character="Daenerys Targaryen",
                photo_url="https://artworks.thetvdb.com/clarke.jpg",
            ),
        ],
        "average_runtime": 57,
        "next_air_date": None,
        "last_air_date": "2019-05-19",
        "airs_time": "21:00",
        "genres": [
            GenreDTO(provider="tvdb", external_id="1", name="Drama"),
            GenreDTO(provider="tvdb", external_id="2", name="Fantasy"),
        ],
    }
    defaults.update(overrides)
    return DetailDTO(**defaults)


def episode(**overrides):
    defaults = {
        "season_number": 1,
        "episode_number": 1,
        "absolute_number": 1,
        "name": "Winter Is Coming",
        "overview": "The Stark family receives visitors.",
        "still_path": "https://artworks.thetvdb.com/still.jpg",
        "air_date": "2011-04-17",
        "runtime": 60,
    }
    defaults.update(overrides)
    return EpisodeDTO(**defaults)


class ShowImportTests(TestCase):
    def test_import_show_reconciles_stale_episodes_and_seasons_preserving_watched_state(self):
        provider = FakeProvider(
            detail=show_detail(),
            episodes=[
                episode(),
                episode(episode_number=2, name="The Kingsroad"),
                episode(season_number=2, name="The North Remembers"),
            ],
            seasons=[
                SeasonDTO(season_number=1, name="Season 1"),
                SeasonDTO(season_number=2, name="Season 2"),
            ],
        )
        show = import_show("121361", provider_getter=lambda _: provider)
        user = get_user_model().objects.create_user(
            email="watcher@example.com",
        )
        UserShow.objects.create(user=user, show=show)
        watched_episode = show.episodes.get(season_number=1, episode_number=1)
        stale_episode = show.episodes.get(season_number=1, episode_number=2)
        seen_at = timezone.now() - timezone.timedelta(days=3)
        UserEpisode.objects.create(user=user, episode=watched_episode, seen_at=seen_at)
        UserEpisode.objects.create(user=user, episode=stale_episode)

        provider.episodes = [episode()]
        provider.seasons = [SeasonDTO(season_number=1, name="Season 1")]

        refreshed = import_show("121361", provider_getter=lambda _: provider)

        self.assertEqual(refreshed.id, show.id)
        self.assertEqual(
            list(refreshed.seasons.values_list("season_number", flat=True)),
            [1],
        )
        self.assertEqual(
            list(refreshed.episodes.values_list("season_number", "episode_number")),
            [(1, 1)],
        )
        preserved = UserEpisode.objects.get(user=user, episode=watched_episode)
        self.assertEqual(preserved.seen_at, seen_at)
        self.assertFalse(UserEpisode.objects.filter(user=user, episode=stale_episode).exists())

    def test_import_show_creates_seasons_without_episodes(self):
        provider = FakeProvider(
            detail=show_detail(),
            episodes=[episode()],
            seasons=[
                SeasonDTO(season_number=1, name="Season 1"),
                SeasonDTO(season_number=2, name="Season 2"),
            ],
        )

        show = import_show("121361", provider_getter=lambda _: provider)

        self.assertEqual(
            list(show.seasons.values_list("season_number", flat=True)),
            [1, 2],
        )

    def test_import_show_saves_selected_translations_with_english_scalars(self):
        provider = FakeProvider(
            detail=show_detail(
                translations={"por": {"title": "A Guerra dos Tronos", "overview": "Resumo"}}
            ),
            episodes=[
                episode(
                    translations={"eng": {"name": "Winter Is Coming"}, "por": {"name": "O Inverno Está Chegando"}}
                )
            ],
            seasons=[
                SeasonDTO(
                    season_number=1,
                    name="Season 1",
                    translations={"eng": {"name": "Season 1"}, "por": {"name": "Temporada 1"}},
                )
            ],
        )

        show = import_show("121361", language="por", provider_getter=lambda _: provider)

        self.assertEqual(show.name, "Game of Thrones")
        self.assertEqual(show.translations["por"]["name"], "A Guerra dos Tronos")
        self.assertEqual(show.seasons.get().name, "Season 1")
        self.assertNotIn("name", show.seasons.get().translations.get("por", {}))
        self.assertEqual(show.episodes.get().translations["por"]["name"], "O Inverno Está Chegando")

    def test_import_show_uses_numbered_names_when_provider_names_are_empty(self):
        provider = FakeProvider(
            detail=show_detail(),
            episodes=[episode(name="")],
            seasons=[
                SeasonDTO(
                    season_number=1,
                    name="Provider season name",
                    translations={"eng": {"name": "Provider season name"}},
                )
            ],
        )

        show = import_show("121361", provider_getter=lambda _: provider)

        self.assertEqual(show.seasons.get().name, "Season 1")
        self.assertEqual(show.episodes.get().name, "Episode 1")
    def test_import_show_persists_airing_time_as_a_time(self):
        provider = FakeProvider(detail=show_detail(airs_time="21:00"), episodes=[])

        show = import_show("121361", provider_getter=lambda _: provider)

        self.assertEqual(show.airs_time, time(21, 0))

    def test_import_show_discards_invalid_airing_time(self):
        provider = FakeProvider(detail=show_detail(airs_time="not-a-time"), episodes=[])

        show = import_show("121361", provider_getter=lambda _: provider)

        self.assertIsNone(show.airs_time)

    def test_import_show_creates_show_genres_seasons_and_episodes(self):
        provider = FakeProvider(
            detail=show_detail(),
            episodes=[
                episode(),
                episode(season_number=0, episode_number=1, name="Special", air_date="2010-12-05"),
                episode(season_number=1, episode_number=2, name="The Kingsroad", air_date=None),
            ],
        )

        show = import_show("121361", provider_getter=lambda provider_name: provider)

        self.assertEqual(provider.detail_calls, [("121361", "eng", "tv")])
        self.assertEqual(provider.episode_calls, [("121361", "eng")])
        self.assertEqual(show.provider, "tvdb")
        self.assertEqual(show.external_id, "121361")
        self.assertEqual(show.name, "Game of Thrones")
        self.assertEqual(show.overview, "Nine noble families fight for control.")
        self.assertEqual(show.poster_path, "https://artworks.thetvdb.com/poster.jpg")
        self.assertEqual(show.first_aired.isoformat(), "2011-04-17")
        self.assertEqual(show.status, "Ended")
        self.assertEqual(show.network, "HBO")
        self.assertEqual(show.sync_status, SyncStatus.OK)
        self.assertIsNotNone(show.last_synced_at)
        self.assertEqual(show.aired_episode_count, 1)
        self.assertEqual(show.imdb_id, "tt0944947")
        self.assertEqual(show.tmdb_id, "1399")
        self.assertEqual(show.tvdb_id, "121361")
        self.assertEqual(show.trailer_url, "https://www.youtube.com/watch?v=abc123")
        self.assertEqual(show.average_runtime, 57)
        self.assertIsNone(show.next_air_date)
        self.assertEqual(show.last_air_date.isoformat(), "2019-05-19")
        self.assertEqual(show.airs_time, time(21, 0))
        self.assertEqual(
            show.cast,
            [{
                "name": "Emilia Clarke",
                "character": "Daenerys Targaryen",
                "photo_url": "https://artworks.thetvdb.com/clarke.jpg",
            }],
        )
        self.assertEqual(
            list(show.genres.order_by("external_id").values_list("name", flat=True)),
            ["Drama", "Fantasy"],
        )
        self.assertEqual(Genre.objects.count(), 2)
        self.assertEqual(
            list(show.seasons.order_by("season_number").values_list("season_number", flat=True)),
            [0, 1],
        )
        self.assertEqual(show.episodes.count(), 3)

    def test_import_show_persists_finale_type(self):
        provider = FakeProvider(
            detail=show_detail(),
            episodes=[
                episode(finale_type="series"),
                episode(season_number=1, episode_number=2, finale_type=None),
            ],
        )

        import_show("121361", provider_getter=lambda provider_name: provider)

        self.assertEqual(
            Episode.objects.get(season_number=1, episode_number=1).finale_type, "series"
        )
        self.assertIsNone(
            Episode.objects.get(season_number=1, episode_number=2).finale_type
        )

    def test_import_show_is_idempotent_and_inserts_new_episodes(self):
        provider = FakeProvider(detail=show_detail(), episodes=[episode()])
        show = import_show("121361", provider_getter=lambda provider_name: provider)

        provider.episodes = [
            episode(name="Winter Is Coming Updated"),
            episode(season_number=1, episode_number=2, name="The Kingsroad"),
        ]
        imported_show = import_show("121361", provider_getter=lambda provider_name: provider)

        self.assertEqual(imported_show.id, show.id)
        self.assertEqual(Show.objects.count(), 1)
        self.assertEqual(Season.objects.count(), 1)
        self.assertEqual(Episode.objects.count(), 2)
        self.assertEqual(
            Episode.objects.get(season_number=1, episode_number=1).name,
            "Winter Is Coming Updated",
        )

    def test_import_show_refreshes_tmdb_id(self):
        provider = FakeProvider(detail=show_detail(tmdb_id="1399"), episodes=[])
        show = import_show("121361", provider_getter=lambda _: provider)

        provider.detail = show_detail(tmdb_id="1400")
        refreshed = import_show("121361", provider_getter=lambda _: provider)

        self.assertEqual(refreshed.id, show.id)
        self.assertEqual(refreshed.tmdb_id, "1400")

    def test_import_show_recomputes_aired_count_excluding_specials_and_unaired(self):
        provider = FakeProvider(
            detail=show_detail(),
            episodes=[
                episode(season_number=0, episode_number=1, air_date="2010-01-01"),
                episode(season_number=1, episode_number=1, air_date="2011-04-17"),
                episode(season_number=1, episode_number=2, air_date=None),
                episode(
                    season_number=1,
                    episode_number=3,
                    air_date=(timezone.localdate() + timezone.timedelta(days=7)).isoformat(),
                ),
            ],
        )

        show = import_show("121361", provider_getter=lambda provider_name: provider)

        self.assertEqual(show.aired_episode_count, 1)

    def test_provider_error_marks_existing_show_error_without_corrupting_metadata(self):
        show = Show.objects.create(external_id="121361", name="Game of Thrones")
        provider = FakeProvider(detail_error=ProviderError("provider down"))

        with self.assertRaises(ProviderError):
            import_show("121361", provider_getter=lambda provider_name: provider)

        show.refresh_from_db()
        self.assertEqual(show.name, "Game of Thrones")
        self.assertEqual(show.sync_status, SyncStatus.ERROR)
        self.assertIsNone(show.last_synced_at)

    def test_import_show_supports_tmdb_provider_metadata(self):
        provider = FakeProvider(
            detail=show_detail(
                provider="tmdb",
                external_id="1399",
                poster_path="/poster.jpg",
                genres=[GenreDTO(provider="tmdb", external_id="18", name="Drama")],
            ),
            episodes=[episode()],
            seasons=[],
        )

        show = import_show(
            "1399",
            provider="tmdb",
            language="en-US",
            provider_getter=lambda provider_name: provider,
        )

        self.assertEqual(show.provider, "tmdb")
        self.assertEqual(show.external_id, "1399")
        self.assertEqual(provider.detail_calls, [("1399", "en-US", "tv")])
        self.assertEqual(show.genres.get().provider, "tmdb")

    def test_import_show_refreshes_cross_provider_ids(self):
        provider = FakeProvider(detail=show_detail(), episodes=[])
        show = import_show("121361", provider_getter=lambda _: provider)

        provider.detail = show_detail(tmdb_id="1400", tvdb_id="121362")
        refreshed = import_show("121361", provider_getter=lambda _: provider)

        self.assertEqual(refreshed.id, show.id)
        self.assertEqual(refreshed.tmdb_id, "1400")
        self.assertEqual(refreshed.tvdb_id, "121362")
