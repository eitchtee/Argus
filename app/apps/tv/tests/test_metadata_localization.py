from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.tv.models import Episode, Season, Show
from apps.tv.views import _build_show_context


class TVMetadataLocalizationTests(TestCase):
    def test_tracked_detail_localizes_show_season_and_episode(self):
        user = get_user_model().objects.create_user("user@example.com")
        user.settings.language = "en"
        user.settings.tvdb_metadata_language = "por"
        user.settings.save()
        show = Show.objects.create(
            external_id="123",
            name="Show",
            overview="English show",
            translations={"por": {"name": "Série", "overview": "Resumo"}},
        )
        season = Season.objects.create(
            show=show,
            season_number=1,
            name="Season 1",
            translations={"por": {"name": "Temporada 1"}},
        )
        Episode.objects.create(
            show=show,
            season=season,
            season_number=1,
            episode_number=1,
            name="Pilot",
            translations={"por": {"name": "Piloto"}},
        )

        context = _build_show_context(user, "123")

        self.assertEqual(context["title"], "Série")
        self.assertEqual(context["overview"], "Resumo")
        self.assertEqual(context["seasons"][0]["name"], "Temporada 1")
        self.assertEqual(context["seasons"][0]["episodes"][0]["name"], "Piloto")
