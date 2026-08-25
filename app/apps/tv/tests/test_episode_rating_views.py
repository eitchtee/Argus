from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.catalog.models import MediaRating
from apps.tv.models import Episode, Season, Show, UserEpisode, UserShow


@override_settings(
    STORAGES={
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {
            "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"
        },
    },
    DJANGO_VITE_DEV_MODE=True,
)
class EpisodeRatingViewTests(TestCase):
    def setUp(self):
        from django_vite.core.asset_loader import DjangoViteAssetLoader

        DjangoViteAssetLoader._instance = None
        self.user = get_user_model().objects.create_user(
            "user@example.com", password="password"
        )
        self.client.login(username="user@example.com", password="password")
        self.show = Show.objects.create(external_id="123", name="Foo")
        self.season = Season.objects.create(show=self.show, season_number=1)
        self.episode = Episode.objects.create(
            show=self.show,
            season=self.season,
            season_number=1,
            episode_number=1,
            name="Pilot",
        )
        UserShow.objects.create(
            user=self.user, show=self.show, status=UserShow.Status.TRACKED
        )
        self.rating_url = reverse(
            "media-rating",
            kwargs={"media_type": "episode", "external_id": str(self.episode.id)},
        )

    def tearDown(self):
        from django_vite.core.asset_loader import DjangoViteAssetLoader

        DjangoViteAssetLoader._instance = None

    def _mark_watched(self):
        UserEpisode.objects.create(user=self.user, episode=self.episode)

    def test_rate_seen_episode(self):
        self._mark_watched()

        response = self.client.post(
            self.rating_url, {"score": "4.5"}, HTTP_HX_REQUEST="true"
        )

        self.assertEqual(response.status_code, 200)
        rating = MediaRating.objects.get(user=self.user)
        self.assertEqual(rating.score, Decimal("4.5"))
        self.assertEqual(rating.media_type, "episode")

    def test_unseen_episode_cannot_be_rated(self):
        response = self.client.post(
            self.rating_url, {"score": "4.5"}, HTTP_HX_REQUEST="true"
        )

        self.assertEqual(response.status_code, 400)
        self.assertFalse(MediaRating.objects.exists())

    def test_marking_watched_delivers_oob_rating_slot(self):
        response = self.client.post(
            reverse(
                "tv-episode-detail-watched",
                kwargs={"external_id": "123", "episode_id": self.episode.id},
            ),
            HTTP_HX_REQUEST="true",
        )

        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn('id="episode-rating"', content)
        self.assertIn("hx-swap-oob", content)
        self.assertIn('class="media-rating"', content)

    def test_unmarking_watched_clears_oob_rating_slot(self):
        self._mark_watched()

        response = self.client.delete(
            reverse(
                "tv-episode-detail-watched",
                kwargs={"external_id": "123", "episode_id": self.episode.id},
            ),
            HTTP_HX_REQUEST="true",
        )

        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn('id="episode-rating"', content)
        self.assertIn("hx-swap-oob", content)
        self.assertNotIn('class="media-rating"', content)

    def test_episode_detail_shows_rating_when_watched(self):
        self._mark_watched()
        MediaRating.objects.create(
            user=self.user,
            media_type="episode",
            content_type=ContentType.objects.get_for_model(Episode),
            object_id=self.episode.pk,
            score=Decimal("2.5"),
        )

        response = self.client.get(
            reverse(
                "tv-episode-detail-content",
                kwargs={"external_id": "123", "episode_id": self.episode.id},
            ),
            HTTP_HX_REQUEST="true",
        )

        self.assertContains(response, 'id="episode-rating"')
        self.assertContains(response, 'class="media-rating"')
        self.assertContains(response, 'data-score="2.5"')


class ShowRatingViewTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            "user@example.com", password="password"
        )
        self.client.login(username="user@example.com", password="password")
        self.show = Show.objects.create(external_id="123", name="Foo")
        self.rating_url = reverse(
            "media-rating",
            kwargs={"media_type": "show", "external_id": "123"},
        )

    def test_show_is_rateable_without_being_tracked_or_watched(self):
        response = self.client.post(
            self.rating_url, {"score": "3.5"}, HTTP_HX_REQUEST="true"
        )

        self.assertEqual(response.status_code, 200)
        rating = MediaRating.objects.get(user=self.user)
        self.assertEqual(rating.score, Decimal("3.5"))

    def test_show_rating_url_carries_provider_for_non_tvdb_defaults(self):
        from apps.catalog.ratings import build_rating_url

        self.assertEqual(
            build_rating_url("show", "123", "tmdb"),
            "/media/show/123/rating/?provider=tmdb",
        )
