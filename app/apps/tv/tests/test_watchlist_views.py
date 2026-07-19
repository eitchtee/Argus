from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

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
class WatchlistViewTests(TestCase):
    def setUp(self):
        from django_vite.core.asset_loader import DjangoViteAssetLoader

        DjangoViteAssetLoader._instance = None
        self.user = get_user_model().objects.create_user(
            "user@example.com", password="password"
        )
        self.client.login(username="user@example.com", password="password")
        self.today = timezone.localdate()
        self.show, self.season = self.make_show(
            "My Show",
            "1",
            poster_path="https://example.com/poster.jpg",
        )
        self.episode = self.make_episode(self.show, self.season, 1)

    def tearDown(self):
        from django_vite.core.asset_loader import DjangoViteAssetLoader

        DjangoViteAssetLoader._instance = None
        super().tearDown()

    def make_show(
        self,
        name,
        external_id,
        status=UserShow.Status.TRACKED,
        poster_path=None,
    ):
        show = Show.objects.create(
            name=name,
            external_id=external_id,
            poster_path=poster_path,
        )
        season = Season.objects.create(show=show, season_number=1, name="Season 1")
        UserShow.objects.create(user=self.user, show=show, status=status)
        return show, season

    def make_episode(self, show, season, number, air_date=None):
        return Episode.objects.create(
            show=show,
            season=season,
            season_number=1,
            episode_number=number,
            name=f"Episode {number}",
            air_date=air_date or self.today - timedelta(days=1),
        )

    def tab_url(self, section):
        return reverse("tv-watchlist-tab", kwargs={"section": section})

    def test_requires_authentication(self):
        self.client.logout()

        response = self.client.get(reverse("tv-watchlist"))

        self.assertEqual(response.status_code, 302)
        self.assertIn("/login/", response["Location"])

    def test_full_page_renders_lazy_daisyui_tabs(self):
        response = self.client.get(reverse("tv-watchlist"))

        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertContains(response, "Watchlist")
        for section in ("all", "watching", "completed", "paused", "dropped"):
            self.assertIn(
                self.tab_url(section),
                content,
            )
        self.assertEqual(content.count('hx-target="#tv-watchlist-panel"'), 5)
        self.assertEqual(content.count('hx-swap="innerHTML"'), 5)
        self.assertIn('role="tablist"', content)
        self.assertIn('aria-label="All"', content)
        all_label = content.index('aria-label="All"')
        all_input_start = content.rfind("<input", 0, all_label)
        all_input_end = content.index(">", all_label)
        self.assertIn("checked", content[all_input_start:all_input_end])
        self.assertIn('hx-trigger="load, change"', content)
        self.assertNotIn("My Show", content)

    def test_all_fragment_renders_poster_card_and_detail_link(self):
        response = self.client.get(
            self.tab_url("all"),
            HTTP_HX_REQUEST="true",
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "My Show")
        self.assertContains(response, "https://example.com/poster.jpg")
        self.assertContains(response, "0/1")
        self.assertContains(response, "progress-warning")
        self.assertContains(response, f'href="{reverse("tv-detail", kwargs={"external_id": "1"})}"')
        self.assertNotContains(response, "<html")

    def test_completed_progress_uses_show_status_color(self):
        ended_show, ended_season = self.make_show("Ended Show", "2")
        ended_show.status = "Ended"
        ended_show.save(update_fields=["status"])
        ended_episode = self.make_episode(ended_show, ended_season, 1)
        UserEpisode.objects.create(user=self.user, episode=ended_episode)

        continuing_show, continuing_season = self.make_show("Continuing Show", "3")
        continuing_show.status = "Continuing"
        continuing_show.save(update_fields=["status"])
        continuing_episode = self.make_episode(continuing_show, continuing_season, 1)
        UserEpisode.objects.create(user=self.user, episode=continuing_episode)

        response = self.client.get(
            self.tab_url("all"),
            HTTP_HX_REQUEST="true",
        )

        self.assertContains(response, "1/1")
        self.assertContains(response, "progress-success")
        self.assertContains(response, "progress-info")

    def test_watching_fragment_excludes_completed_show(self):
        completed, completed_season = self.make_show("Completed Show", "2")
        completed_episode = self.make_episode(completed, completed_season, 1)
        UserEpisode.objects.create(user=self.user, episode=completed_episode)

        response = self.client.get(
            self.tab_url("watching"),
            HTTP_HX_REQUEST="true",
        )

        self.assertContains(response, "My Show")
        self.assertNotContains(response, "Completed Show")

    def test_fragment_requires_htmx(self):
        response = self.client.get(
            self.tab_url("all")
        )

        self.assertEqual(response.status_code, 403)

    def test_fragment_rejects_unknown_section(self):
        response = self.client.get(
            self.tab_url("unknown"),
            HTTP_HX_REQUEST="true",
        )

        self.assertEqual(response.status_code, 400)

    def test_empty_section_renders_empty_state(self):
        response = self.client.get(
            self.tab_url("completed"),
            HTTP_HX_REQUEST="true",
        )

        self.assertContains(response, "No shows in this section.")

    def test_missing_poster_renders_daisyui_placeholder(self):
        self.make_show("No Poster", "2", status=UserShow.Status.PAUSED)

        response = self.client.get(
            self.tab_url("paused"),
            HTTP_HX_REQUEST="true",
        )

        self.assertContains(response, "No Poster")
        self.assertContains(response, "fa-tv")
        self.assertNotContains(response, 'src=""')
