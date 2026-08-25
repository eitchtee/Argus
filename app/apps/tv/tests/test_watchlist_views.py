from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.catalog.models import UserMediaArtworkPreference
from apps.catalog.ratings import rate_media
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
        normalized_status=None,
    ):
        show = Show.objects.create(
            name=name,
            external_id=external_id,
            poster_path=poster_path,
            normalized_status=normalized_status,
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

    def test_full_page_renders_filter_form_with_watching_selected(self):
        response = self.client.get(reverse("tv-watchlist"))

        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertTemplateUsed(response, "tv/pages/watchlist.html")
        self.assertContains(response, "Watchlist")
        self.assertNotContains(response, "My Show")
        self.assertContains(response, 'name="condition"')
        self.assertContains(response, 'data-filter-value="watching"')
        self.assertContains(
            response,
            'class="btn btn-primary btn-outline btn-sm rounded-full btn-active"',
        )
        self.assertContains(response, 'hx-get="/tv/watchlist/"')
        self.assertContains(response, 'hx-target="#tv-watchlist-panel"')
        self.assertContains(response, 'hx-trigger="load"')
        self.assertContains(response, 'hx-push-url="true"')
        self.assertContains(response, 'name="normalized_status"')
        self.assertContains(response, 'id="tv-watchlist-normalized-status-Continuing"')
        self.assertContains(response, "Loading watchlist")
        self.assertNotContains(response, 'data-filter-value="all"')
        self.assertNotIn('role="tablist"', content)
        self.assertNotIn('tv-watchlist-tab', content)
        self.assertRegex(
            content,
            r'name="normalized_status"[^>]*disabled',
        )

    def test_query_params_restore_selected_filters(self):
        paused, paused_season = self.make_show(
            "Paused Ended Show",
            "2",
            status=UserShow.Status.PAUSED,
            normalized_status=Show.NormalizedStatus.ENDED,
        )
        self.make_episode(paused, paused_season, 1)

        response = self.client.get(
            reverse("tv-watchlist") + "?condition=paused&normalized_status=Ended"
        )
        content = response.content.decode()

        self.assertContains(
            response,
            'hx-get="/tv/watchlist/?condition=paused&amp;normalized_status=Ended"',
        )
        self.assertRegex(
            content,
            r'class="btn btn-primary btn-outline btn-sm rounded-full btn-active"\s+data-filter-value="paused"',
        )
        self.assertRegex(
            content,
            r'class="btn btn-primary btn-outline btn-sm rounded-full btn-active"\s+data-filter-value="Ended"',
        )
        self.assertNotRegex(
            content,
            r'id="tv-watchlist-normalized-status-Ended"[^>]*disabled',
        )

        fragment_response = self.client.get(
            reverse("tv-watchlist") + "?condition=paused&normalized_status=Ended",
            HTTP_HX_REQUEST="true",
        )
        self.assertContains(fragment_response, "Paused Ended Show")
        self.assertNotContains(fragment_response, "My Show")

    def test_multiple_condition_query_params_render_all_selected_conditions(self):
        completed, completed_season = self.make_show("Completed Show", "2")
        completed_episode = self.make_episode(completed, completed_season, 1)
        UserEpisode.objects.create(user=self.user, episode=completed_episode)

        response = self.client.get(
            reverse("tv-watchlist") + "?condition=watching&condition=completed"
        )
        content = response.content.decode()

        self.assertEqual(
            content.count('class="btn btn-primary btn-outline btn-sm rounded-full btn-active"'),
            2,
        )

        fragment_response = self.client.get(
            reverse("tv-watchlist") + "?condition=watching&condition=completed",
            HTTP_HX_REQUEST="true",
        )
        self.assertContains(fragment_response, "My Show")
        self.assertContains(fragment_response, "Completed Show")

    def test_rated_show_shows_rating_badge_on_grid(self):
        rate_media(self.user, "show", self.show, Decimal("4.0"))

        response = self.client.get(
            reverse("tv-watchlist"), HTTP_HX_REQUEST="true"
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'class="poster-card__rating"')
        self.assertContains(response, ">4.0<")

    def test_unrated_show_has_no_rating_badge_on_grid(self):
        response = self.client.get(
            reverse("tv-watchlist"), HTTP_HX_REQUEST="true"
        )

        self.assertContains(response, "My Show")
        self.assertNotContains(response, 'class="poster-card__rating"')

    def test_htmx_request_returns_only_the_filtered_grid(self):
        completed, completed_season = self.make_show(
            "Completed Show",
            "2",
            normalized_status=Show.NormalizedStatus.ENDED,
        )
        completed_episode = self.make_episode(completed, completed_season, 1)
        UserEpisode.objects.create(user=self.user, episode=completed_episode)

        response = self.client.get(
            reverse("tv-watchlist") + "?condition=completed&normalized_status=Ended",
            HTTP_HX_REQUEST="true",
        )

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "tv/fragments/watchlist_grid.html")
        self.assertContains(response, "Completed Show")
        self.assertNotContains(response, "My Show")
        self.assertNotContains(response, "<html")

    def test_hx_boosted_request_returns_page_shell_with_lazy_grid(self):
        response = self.client.get(
            reverse("tv-watchlist"),
            HTTP_HX_REQUEST="true",
            HTTP_HX_BOOSTED="true",
        )

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "tv/pages/watchlist.html")
        self.assertContains(response, "<html")
        self.assertContains(response, 'id="tv-watchlist-panel"')
        self.assertContains(response, 'hx-trigger="load"')
        self.assertContains(response, 'hx-get="/tv/watchlist/"')
        self.assertNotContains(response, "My Show")

    def test_history_restore_request_returns_page_shell_with_lazy_grid(self):
        response = self.client.get(
            reverse("tv-watchlist"),
            HTTP_HX_REQUEST="true",
            HTTP_HX_HISTORY_RESTORE_REQUEST="true",
        )

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "tv/pages/watchlist.html")
        self.assertContains(response, "<html")
        self.assertContains(response, 'id="tv-watchlist-panel"')

    def test_watchlist_poster_boost_targets_the_page_body(self):
        response = self.client.get(
            reverse("tv-watchlist"),
            HTTP_HX_REQUEST="true",
        )

        self.assertContains(response, "My Show")
        self.assertContains(response, 'hx-boost="true" hx-target="body"')
        self.assertContains(response, 'hx-swap="innerHTML"')

    def test_all_fragment_renders_poster_card_and_detail_link(self):
        response = self.client.get(
            self.tab_url("all"),
            HTTP_HX_REQUEST="true",
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "My Show")
        self.assertContains(response, "https://example.com/poster.jpg")
        self.assertContains(response, "0/1")
        self.assertContains(response, "progress-info")
        self.assertContains(response, f'href="{reverse("tv-detail", kwargs={"external_id": "1"})}"')
        self.assertContains(response, 'hx-boost="true" hx-target="body" hx-swap="innerHTML"')
        self.assertNotContains(response, "<html")

    def test_completed_progress_uses_show_status_color(self):
        ended_show, ended_season = self.make_show("Ended Show", "2")
        ended_show.status = "Ended"
        ended_show.normalized_status = Show.NormalizedStatus.ENDED
        ended_show.save(update_fields=["status", "normalized_status"])
        ended_episode = self.make_episode(ended_show, ended_season, 1)
        UserEpisode.objects.create(user=self.user, episode=ended_episode)

        continuing_show, continuing_season = self.make_show("Continuing Show", "3")
        continuing_show.status = "Continuing"
        continuing_show.normalized_status = Show.NormalizedStatus.CONTINUING
        continuing_show.save(update_fields=["status", "normalized_status"])
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
        self.assertContains(response, 'data-lucide="tv"')
        self.assertNotContains(response, 'src=""')

    def test_watchlist_search_data_contains_translated_and_original_titles(self):
        self.show.original_title = "The Original Show"
        self.show.save(update_fields=["original_title"])

        response = self.client.get(
            self.tab_url("all"),
            HTTP_HX_REQUEST="true",
        )

        self.assertContains(response, 'data-name="My Show The Original Show"')

    def test_watchlist_displays_original_title_for_users_who_enable_it(self):
        self.show.original_title = "The Original Show"
        self.show.save(update_fields=["original_title"])
        UserMediaArtworkPreference.objects.create(
            user=self.user,
            provider="tvdb",
            media_type="tv",
            external_id=self.show.external_id,
            use_original_title=True,
        )

        response = self.client.get(
            self.tab_url("all"),
            HTTP_HX_REQUEST="true",
        )

        self.assertContains(
            response,
            '<h2 class="poster-card__title" title="The Original Show">The Original Show</h2>',
        )
        self.assertContains(response, 'data-name="My Show The Original Show"')
