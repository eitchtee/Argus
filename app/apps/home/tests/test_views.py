from datetime import date
import re

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse


@override_settings(
    STORAGES={
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {
            "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"
        },
    },
    DJANGO_VITE_DEV_MODE=True,
)
class IndexViewTests(TestCase):
    def setUp(self):
        from django_vite.core.asset_loader import DjangoViteAssetLoader

        DjangoViteAssetLoader._instance = None

        self.user = get_user_model().objects.create_user("user@example.com", password="password")
        self.client.login(username="user@example.com", password="password")

    def tearDown(self):
        from django_vite.core.asset_loader import DjangoViteAssetLoader

        DjangoViteAssetLoader._instance = None

    def test_requires_auth(self):
        self.client.logout()
        response = self.client.get("/")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login/", response["Location"])

    def test_renders_for_authenticated_user(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)

    def _sidebar_menu(self, response):
        content = response.content.decode()
        sidebar_start = content.index('id="sidebar"')
        sidebar_end = content.index("<main", sidebar_start)
        return content[sidebar_start:sidebar_end]

    def _sidebar_link(self, sidebar, label):
        return next(
            (
                link
                for link in re.findall(r"<a\b[^>]*>.*?</a>", sidebar, re.S)
                if f">{label}</span>" in link
            ),
            None,
        )

    def test_sidebar_renders_daisyui_hierarchy_with_boosted_links(self):
        response = self.client.get("/")
        content = response.content.decode()
        sidebar = self._sidebar_menu(response)
        home_url = reverse("index")
        search_url = reverse("catalog-search-page")
        calendar_url = reverse("calendar")
        up_next_url = reverse("tv-up-next")
        upcoming_url = reverse("tv-upcoming")
        watchlist_url = reverse("tv-watchlist")
        movie_watchlist_url = reverse("movies-watchlist-page")
        movie_watched_url = reverse("movies-watched-page")

        self.assertIn('hx-boost="true"', sidebar)
        self.assertIn('class="menu menu-sm', sidebar)
        self.assertGreaterEqual(sidebar.count("<ul"), 3)
        self.assertIn(">Home</span>", sidebar)
        self.assertIn(">Search</span>", sidebar)
        self.assertIn(">TV</span>", sidebar)
        self.assertIn(">Up next</span>", sidebar)
        self.assertIn(">Upcoming</span>", sidebar)
        self.assertIn(">Watchlist</span>", sidebar)
        self.assertIn(">Movies</span>", sidebar)
        self.assertIn(">Watched</span>", sidebar)
        self.assertIn(">Calendar</span>", sidebar)
        self.assertNotIn(">Admin</span>", sidebar)
        self.assertNotIn("Settings", sidebar)
        self.assertNotIn("API Docs", sidebar)
        self.assertEqual(sidebar.count("menu-title"), 2)
        self.assertNotRegex(sidebar, r"<a\b[^>]*>\s*(?:TV|Movies)\s*</a>")

        labels = [
            ">Home</span>",
            ">Search</span>",
            ">TV</span>",
            ">Up next</span>",
            ">Upcoming</span>",
            ">Watchlist</span>",
            ">Movies</span>",
            ">Watched</span>",
            ">Calendar</span>",
        ]
        positions = [sidebar.index(label) for label in labels]
        self.assertEqual(positions, sorted(positions))

        anchors = [
            anchor
            for anchor in re.findall(r"<a\b[^>]*>", sidebar)
            if "sidebar-item" in anchor
        ]
        self.assertEqual(len(anchors), 8)
        for anchor in anchors:
            self.assertIn('hx-boost="true"', anchor)

        self.assertIn(f'href="{home_url}"', self._sidebar_link(sidebar, "Home"))
        self.assertIn(
            f'href="{movie_watched_url}"',
            self._sidebar_link(sidebar, "Watched"),
        )
        self.assertIn(f'href="{up_next_url}"', self._sidebar_link(sidebar, "Up next"))
        self.assertIn(f'href="{upcoming_url}"', self._sidebar_link(sidebar, "Upcoming"))

        watchlist_links = [
            link
            for link in re.findall(r"<a\b[^>]*>.*?</a>", sidebar, re.S)
            if ">Watchlist</span>" in link
        ]
        self.assertEqual(len(watchlist_links), 2)
        self.assertIn(f'href="{watchlist_url}"', watchlist_links[0])
        self.assertIn(f'href="{movie_watchlist_url}"', watchlist_links[1])

        self.assertIn(f'href="{search_url}"', self._sidebar_link(sidebar, "Search"))
        self.assertIn(f'href="{calendar_url}"', self._sidebar_link(sidebar, "Calendar"))
        self.assertEqual(sidebar.count("data-theme-toggle"), 1)
        self.assertIn("swap swap-rotate", sidebar)
        self.assertIn("btn btn-ghost btn-sm btn-circle", sidebar)
        self.assertIn('data-theme-url="/user/session/toggle-theme/"', sidebar)
        self.assertIn('data-theme="argus_dark"', sidebar)
        self.assertIn("data-csrf-token=", sidebar)
        self.assertIn("fa-moon", sidebar)
        self.assertIn("Switch to light mode", sidebar)
        self.assertIn('_="on change', sidebar)
        self.assertIn("fetch my @data-theme-url as JSON", sidebar)
        self.assertIn("method:'POST'", sidebar)
        self.assertIn("setContent", sidebar)
        theme_toggle_start = sidebar.index('id="theme-toggle"')
        input_start = sidebar.index("<input", theme_toggle_start)
        input_end = sidebar.index(">", input_start)
        self.assertIn("checked", sidebar[input_start:input_end])
        self.assertNotIn("data-theme-toggle", content[content.index("<main"):])

        logo_end = sidebar.index("</a>")
        theme_toggle = sidebar.index("data-theme-toggle")
        navigation = sidebar.index("<nav")
        self.assertLess(logo_end, theme_toggle)
        self.assertLess(theme_toggle, navigation)

    def test_sidebar_theme_toggle_reflects_light_session(self):
        session = self.client.session
        session["theme"] = "argus_light"
        session.save()

        response = self.client.get("/")
        content = response.content.decode()
        sidebar = self._sidebar_menu(response)

        self.assertIn('data-theme="argus_light"', content)
        self.assertIn('data-theme="argus_light"', sidebar)
        self.assertIn("fa-sun", sidebar)
        self.assertIn("Switch to dark mode", sidebar)
        theme_toggle_start = sidebar.index('id="theme-toggle"')
        input_start = sidebar.index("<input", theme_toggle_start)
        input_end = sidebar.index(">", input_start)
        self.assertNotIn("checked", sidebar[input_start:input_end])

    def test_tv_watchlist_sidebar_link_is_active(self):
        response = self.client.get(reverse("tv-watchlist"))
        sidebar = self._sidebar_menu(response)

        watchlist_link = self._sidebar_link(sidebar, "Watchlist")

        self.assertIn(f'href="{reverse("tv-watchlist")}"', watchlist_link)
        self.assertIn('class="sidebar-item menu-active"', watchlist_link)

    def test_movie_watchlist_sidebar_link_is_active(self):
        response = self.client.get(reverse("movies-watchlist-page"))
        sidebar = self._sidebar_menu(response)

        watchlist_links = [
            link
            for link in re.findall(r"<a\b[^>]*>.*?</a>", sidebar, re.S)
            if ">Watchlist</span>" in link
        ]
        watchlist_link = watchlist_links[1]

        self.assertIn(f'href="{reverse("movies-watchlist-page")}"', watchlist_link)
        self.assertIn('class="sidebar-item menu-active"', watchlist_link)

    def test_movie_watched_sidebar_link_is_active(self):
        response = self.client.get(reverse("movies-watched-page"))
        sidebar = self._sidebar_menu(response)

        watched_link = self._sidebar_link(sidebar, "Watched")

        self.assertIn(f'href="{reverse("movies-watched-page")}"', watched_link)
        self.assertIn('class="sidebar-item menu-active"', watched_link)

    def test_sidebar_shows_admin_link_for_staff(self):
        self.user.is_staff = True
        self.user.save(update_fields=["is_staff"])

        response = self.client.get("/")
        sidebar = self._sidebar_menu(response)
        admin_url = reverse("admin:index")

        admin_link = self._sidebar_link(sidebar, "Admin")
        self.assertIsNotNone(admin_link)
        self.assertIn(f'href="{admin_url}"', admin_link)
        self.assertIn('hx-boost="true"', admin_link)

    def test_renders_tv_tabs(self):
        response = self.client.get("/")

        self.assertContains(response, 'aria-label="Watchlist"')
        self.assertContains(response, 'aria-label="Upcoming"')
        self.assertContains(response, "/tv/home/watchlist/")
        self.assertContains(response, "/tv/home/upcoming/")

    def test_watchlist_tab_is_checked_by_default(self):
        response = self.client.get("/")
        content = response.content.decode()

        watchlist_input_start = content.index('aria-label="Watchlist"')
        watchlist_tag_start = content.rindex("<input", 0, watchlist_input_start)
        watchlist_tag_end = content.index(">", watchlist_input_start)
        self.assertIn("checked", content[watchlist_tag_start:watchlist_tag_end])

    def test_shows_empty_state_when_no_watchlist_movies(self):
        response = self.client.get("/")

        self.assertContains(response, "Nothing to suggest")

    def test_shows_watch_something_movie(self):
        from apps.movies.models import Movie, UserMovie

        movie = Movie.objects.create(
            provider="tmdb",
            external_id="1",
            title="Interstellar",
            release_date=date(2014, 11, 7),
            poster_path="/interstellar.jpg",
        )
        UserMovie.objects.create(user=self.user, movie=movie, on_watchlist=True)

        response = self.client.get("/")

        self.assertContains(response, "Interstellar")
        self.assertContains(response, "Nov 07, 2014")
        self.assertContains(response, "group card overflow-hidden")
        self.assertContains(response, "/movies/1/")
