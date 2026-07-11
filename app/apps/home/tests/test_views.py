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
        sidebar = self._sidebar_menu(response)
        home_url = reverse("index")
        search_url = reverse("catalog-search-page")
        calendar_url = reverse("calendar")

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

        for label in ["Home", "Up next", "Upcoming", "Watched"]:
            self.assertIn(f'href="{home_url}"', self._sidebar_link(sidebar, label))

        watchlist_links = [
            link
            for link in re.findall(r"<a\b[^>]*>.*?</a>", sidebar, re.S)
            if ">Watchlist</span>" in link
        ]
        self.assertEqual(len(watchlist_links), 2)
        for link in watchlist_links:
            self.assertIn(f'href="{home_url}"', link)

        self.assertIn(f'href="{search_url}"', self._sidebar_link(sidebar, "Search"))
        self.assertIn(f'href="{calendar_url}"', self._sidebar_link(sidebar, "Calendar"))

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

        movie = Movie.objects.create(provider="tmdb", external_id="1", title="Interstellar")
        UserMovie.objects.create(user=self.user, movie=movie, on_watchlist=True)

        response = self.client.get("/")

        self.assertContains(response, "Interstellar")
        self.assertContains(response, "/movies/1/")
