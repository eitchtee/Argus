from django.contrib.auth import get_user_model
from django.db import IntegrityError
from django.test import TestCase
from django.utils import timezone

from apps.catalog.models import Genre, Tier
from apps.tv.models import Episode, Season, Show, UserEpisode, UserShow


class TvModelTests(TestCase):
    def test_tv_metadata_translations_default_to_empty_dict(self):
        show = Show.objects.create(external_id="series-1", name="The Expanse")
        season = Season.objects.create(show=show, season_number=1, name="Season 1")
        episode = Episode.objects.create(
            show=show,
            season=season,
            season_number=1,
            episode_number=1,
            name="Dulcinea",
        )

        self.assertEqual(show.translations, {})
        self.assertEqual(season.translations, {})
        self.assertEqual(episode.translations, {})

    def test_show_provider_external_id_is_unique(self):
        Show.objects.create(external_id="series-1", name="The Expanse")

        with self.assertRaises(IntegrityError):
            Show.objects.create(external_id="series-1", name="Duplicate")

    def test_show_defaults_and_genres(self):
        genre = Genre.objects.create(provider="tvdb", external_id="1", name="Sci-Fi")
        show = Show.objects.create(
            external_id="series-1",
            name="The Expanse",
            overview="Political tension across the system.",
        )

        show.genres.add(genre)

        self.assertEqual(show.provider, "tvdb")
        self.assertEqual(show.tvdb_id, "series-1")
        self.assertEqual(show.aired_episode_count, 0)
        self.assertEqual(list(show.genres.all()), [genre])
        self.assertEqual(str(show), "The Expanse")

    def test_season_is_unique_per_show_and_number(self):
        show = Show.objects.create(external_id="series-1", name="The Expanse")
        Season.objects.create(show=show, season_number=1, name="Season 1")

        with self.assertRaises(IntegrityError):
            Season.objects.create(show=show, season_number=1, name="Duplicate")

    def test_episode_is_unique_per_show_season_and_episode_number(self):
        show = Show.objects.create(external_id="series-1", name="The Expanse")
        season = Season.objects.create(show=show, season_number=1, name="Season 1")
        Episode.objects.create(
            show=show,
            season=season,
            season_number=1,
            episode_number=1,
            name="Dulcinea",
        )

        with self.assertRaises(IntegrityError):
            Episode.objects.create(
                show=show,
                season=season,
                season_number=1,
                episode_number=1,
                name="Duplicate",
            )

    def test_user_show_is_unique_per_user_show_and_uses_shared_tiers(self):
        user = get_user_model().objects.create_user("user@example.com")
        show = Show.objects.create(external_id="series-1", name="The Expanse")
        UserShow.objects.create(user=user, show=show)

        with self.assertRaises(IntegrityError):
            UserShow.objects.create(user=user, show=show)

        field = UserShow._meta.get_field("tier")
        self.assertEqual([choice[0] for choice in field.choices], Tier.values)

    def test_user_show_defaults_to_tracked_status(self):
        user = get_user_model().objects.create_user("user@example.com")
        show = Show.objects.create(external_id="series-1", name="The Expanse")

        user_show = UserShow.objects.create(user=user, show=show)

        self.assertEqual(user_show.status, UserShow.Status.TRACKED)
        self.assertEqual(
            set(UserShow.Status.values),
            {"tracked", "paused", "dropped"},
        )
        self.assertIsNotNone(user_show.tracking_started_at)
        self.assertIsNone(user_show.tier)

    def test_user_episode_is_sparse_seen_state_unique_per_user_episode(self):
        user = get_user_model().objects.create_user("user@example.com")
        show = Show.objects.create(external_id="series-1", name="The Expanse")
        season = Season.objects.create(show=show, season_number=1, name="Season 1")
        episode = Episode.objects.create(
            show=show,
            season=season,
            season_number=1,
            episode_number=1,
            name="Dulcinea",
        )
        seen_at = timezone.now()
        user_episode = UserEpisode.objects.create(
            user=user,
            episode=episode,
            seen_at=seen_at,
        )

        self.assertEqual(user_episode.seen_at, seen_at)
        with self.assertRaises(IntegrityError):
            UserEpisode.objects.create(user=user, episode=episode)

    def test_show_poster_url_returns_stored_absolute_url(self):
        show = Show.objects.create(
            external_id="series-1",
            name="The Expanse",
            poster_path="https://artworks.thetvdb.com/poster.jpg",
        )

        self.assertEqual(show.poster_url, "https://artworks.thetvdb.com/poster.jpg")

    def test_show_poster_url_is_none_without_poster_path(self):
        show = Show.objects.create(external_id="series-1", name="The Expanse")

        self.assertIsNone(show.poster_url)

    def test_show_backdrop_url_returns_stored_absolute_url(self):
        show = Show.objects.create(
            external_id="series-1",
            name="The Expanse",
            backdrop_path="https://artworks.thetvdb.com/fanart.jpg",
        )

        self.assertEqual(show.backdrop_url, "https://artworks.thetvdb.com/fanart.jpg")

    def test_show_backdrop_url_is_none_without_backdrop_path(self):
        show = Show.objects.create(external_id="series-1", name="The Expanse")

        self.assertIsNone(show.backdrop_url)

    def test_show_cast_defaults_to_empty_list(self):
        show = Show.objects.create(external_id="series-1", name="The Expanse")

        self.assertEqual(show.cast, [])
