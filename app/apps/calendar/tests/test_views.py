import uuid
from datetime import date, time

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from apps.calendar.events import get_calendar_feed
from apps.catalog.models import Genre
from apps.movies.models import Movie, UserMovie
from apps.tv.models import Episode, Season, Show, UserShow


@override_settings(
    STORAGES={
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {
            "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"
        },
    },
    DJANGO_VITE_DEV_MODE=True,
)
class CalendarViewTests(TestCase):
    def setUp(self):
        from django_vite.core.asset_loader import DjangoViteAssetLoader

        DjangoViteAssetLoader._instance = None
        self.user = get_user_model().objects.create_user(
            "user@example.com", password="password"
        )
        self.client.login(username="user@example.com", password="password")

    def tearDown(self):
        from django_vite.core.asset_loader import DjangoViteAssetLoader

        DjangoViteAssetLoader._instance = None

    def make_episode(
        self,
        name,
        status,
        air_date,
        *,
        external_id=None,
        season_number=1,
    ):
        show = Show.objects.create(
            external_id=external_id or name.lower().replace(" ", "-"),
            name=name,
            network="Example Network",
            airs_time=time(21, 0),
        )
        season = Season.objects.create(
            show=show,
            season_number=season_number,
            name="Specials" if season_number == 0 else f"Season {season_number}",
        )
        UserShow.objects.create(user=self.user, show=show, status=status)
        return Episode.objects.create(
            show=show,
            season=season,
            season_number=season_number,
            episode_number=1,
            name="Pilot",
            overview="Summary",
            air_date=air_date,
            runtime=60,
        )

    def make_movie(self, title, *, on_watchlist=True, release_date=None, external_id=None):
        movie = Movie.objects.create(
            provider="tmdb",
            external_id=external_id or title.lower().replace(" ", "-"),
            title=title,
            overview="Summary",
            director="Example Director",
            release_date=release_date,
            runtime=120,
        )
        genre = Genre.objects.create(
            provider="tmdb",
            external_id=f"{movie.external_id}-genre",
            name="Drama",
        )
        movie.genres.add(genre)
        UserMovie.objects.create(
            user=self.user,
            movie=movie,
            on_watchlist=on_watchlist,
        )
        return movie

    def test_calendar_requires_login(self):
        self.client.logout()

        response = self.client.get("/calendar/")

        self.assertEqual(response.status_code, 302)
        self.assertIn("/login/", response["Location"])

    def test_calendar_defaults_to_tracked_and_renders_the_month(self):
        self.make_episode("Tracked", UserShow.Status.TRACKED, date(2026, 7, 10))
        self.make_episode("Paused", UserShow.Status.PAUSED, date(2026, 7, 11))

        response = self.client.get("/calendar/?month=2026-07")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "July 2026")
        self.assertContains(response, "Tracked")
        self.assertNotContains(response, "calendar-event--paused")
        self.assertContains(response, "/calendar/feed/")

    def test_status_query_parameters_preserve_filter_state_and_feed_link(self):
        response = self.client.get("/calendar/?month=2026-07&paused=1&dropped=1")

        self.assertContains(response, '<input class="checkbox checkbox-primary" type="checkbox" name="paused" value="1" checked>')
        self.assertContains(response, '<input class="checkbox checkbox-primary" type="checkbox" name="dropped" value="1" checked>')
        self.assertContains(response, "?tracked=1&amp;paused=1&amp;dropped=1")

    def test_calendar_has_explicit_tv_and_movie_filter_groups(self):
        response = self.client.get(
            "/calendar/?month=2026-07&tracked=0&paused=1&dropped=1&movies=1"
        )

        self.assertContains(response, "TV Shows")
        self.assertContains(response, "Movies")
        self.assertContains(response, '<input class="checkbox checkbox-primary" type="checkbox" name="tracked" value="1">')
        self.assertContains(response, '<input class="checkbox checkbox-primary" type="checkbox" name="paused" value="1" checked>')
        self.assertContains(response, '<input class="checkbox checkbox-primary" type="checkbox" name="dropped" value="1" checked>')
        self.assertContains(response, '<input class="checkbox checkbox-primary" type="checkbox" name="movies" value="1" checked>')
        self.assertContains(
            response,
            "?month=2026-06&amp;tracked=0&amp;paused=1&amp;dropped=1&amp;movies=1",
        )

    def test_calendar_defaults_to_tracked_tv_and_excludes_movies(self):
        response = self.client.get("/calendar/?month=2026-07")

        self.assertContains(response, '<input type="hidden" name="tracked" value="0">')
        self.assertContains(response, '<input class="checkbox checkbox-primary" type="checkbox" name="tracked" value="1" checked>')
        self.assertContains(response, '<input class="checkbox checkbox-primary" type="checkbox" name="paused" value="1">')
        self.assertContains(response, '<input class="checkbox checkbox-primary" type="checkbox" name="dropped" value="1">')
        self.assertContains(response, '<input class="checkbox checkbox-primary" type="checkbox" name="movies" value="1">')

    def test_calendar_and_feed_exclude_special_episodes(self):
        regular = self.make_episode(
            "Regular Calendar Show",
            UserShow.Status.TRACKED,
            date(2026, 7, 10),
        )
        special = self.make_episode(
            "Specials Calendar Show",
            UserShow.Status.TRACKED,
            date(2026, 7, 10),
            season_number=0,
        )
        feed = get_calendar_feed(self.user)

        page_response = self.client.get("/calendar/?month=2026-07")
        feed_response = self.client.get(f"/calendar/feed/{feed.uuid}.ics")
        feed_content = feed_response.content.decode()

        self.assertEqual(page_response.status_code, 200)
        self.assertEqual(feed_response.status_code, 200)
        self.assertContains(page_response, "Regular Calendar Show")
        self.assertNotContains(page_response, "Specials Calendar Show")
        self.assertIn(f"episode-{regular.id}@argus", feed_content)
        self.assertNotIn(f"episode-{special.id}@argus", feed_content)

    def test_calendar_places_empty_state_before_grid_and_subscription_after_grid(self):
        response = self.client.get("/calendar/?month=2026-07")
        content = response.content.decode()

        self.assertLess(content.index('class="calendar-month-bar"'), content.index('class="calendar-filters"'))
        self.assertLess(content.index('class="calendar-filters"'), content.index("No releases in this month"))
        self.assertLess(content.index("No releases in this month"), content.index('class="calendar-scroll"'))
        self.assertLess(content.index('class="calendar-scroll"'), content.index("Subscribe to this calendar"))

    def test_episode_details_are_scoped_to_the_current_user(self):
        episode = self.make_episode(
            "Tracked", UserShow.Status.TRACKED, date(2026, 7, 10)
        )

        response = self.client.get(f"/calendar/episodes/{episode.id}/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Tracked")
        self.assertContains(response, episode.name)
        self.assertContains(response, "Summary")

    def test_episode_details_for_another_users_show_are_not_found(self):
        episode = self.make_episode(
            "Tracked", UserShow.Status.TRACKED, date(2026, 7, 10)
        )
        other_user = get_user_model().objects.create_user("other@example.com")
        UserShow.objects.filter(user=self.user, show=episode.show).update(user=other_user)

        response = self.client.get(f"/calendar/episodes/{episode.id}/")

        self.assertEqual(response.status_code, 404)

    def test_invalid_month_falls_back_to_the_current_month(self):
        response = self.client.get("/calendar/?month=not-a-month")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "calendar-page")

    def test_timed_release_is_placed_on_local_display_date(self):
        self.make_episode("Late UTC", UserShow.Status.TRACKED, date(2026, 7, 10))
        Show.objects.filter(name="Late UTC").update(airs_time=time(2, 0))
        self.client.cookies["mytz"] = "America/Sao_Paulo"

        response = self.client.get("/calendar/?month=2026-07")

        cells = {
            cell["date"]: cell["events"]
            for week in response.context["weeks"]
            for cell in week
        }
        self.assertEqual([event.show_name for event in cells[date(2026, 7, 9)]], ["Late UTC"])
        self.assertEqual(cells[date(2026, 7, 10)], [])

    def test_movie_details_are_scoped_to_watchlisted_movies(self):
        movie = self.make_movie(
            "Tracked Movie",
            on_watchlist=True,
            release_date=date(2026, 7, 10),
        )

        response = self.client.get(f"/calendar/movies/{movie.id}/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Tracked Movie")
        self.assertContains(response, "Summary")
        self.assertContains(response, "Example Director")
        self.assertContains(response, "Drama")

    def test_unwatchlisted_movie_details_return_404(self):
        movie = self.make_movie(
            "Not Tracked",
            on_watchlist=False,
            release_date=date(2026, 7, 10),
        )

        response = self.client.get(f"/calendar/movies/{movie.id}/")

        self.assertEqual(response.status_code, 404)

    def test_movie_feed_is_opt_in(self):
        feed = get_calendar_feed(self.user)
        movie = self.make_movie(
            "Feed Movie",
            on_watchlist=True,
            release_date=date.today(),
        )

        default_response = self.client.get(f"/calendar/feed/{feed.uuid}.ics")
        movie_response = self.client.get(f"/calendar/feed/{feed.uuid}.ics?movies=1")

        self.assertNotIn(f"movie-{movie.id}@argus", default_response.content.decode())
        self.assertIn(f"movie-{movie.id}@argus", movie_response.content.decode())

    def test_movie_events_keep_movie_detail_and_fallback_links(self):
        movie = self.make_movie(
            "Calendar Movie",
            on_watchlist=True,
            release_date=date(2026, 7, 10),
        )

        response = self.client.get("/calendar/?month=2026-07&movies=1")

        self.assertContains(response, f"/calendar/movies/{movie.id}/")
        self.assertContains(response, f"/movies/{movie.external_id}/")

    def test_feed_returns_utc_ical_and_invalid_uuid_is_not_found(self):
        feed = get_calendar_feed(self.user)
        self.make_episode("Tracked", UserShow.Status.TRACKED, date.today())

        response = self.client.get(f"/calendar/feed/{feed.uuid}.ics")
        missing = self.client.get(f"/calendar/feed/{uuid.uuid4()}.ics")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/calendar; charset=utf-8")
        self.assertIn("BEGIN:VCALENDAR", response.content.decode())
        self.assertEqual(missing.status_code, 404)

    def test_feed_is_available_without_login(self):
        feed = get_calendar_feed(self.user)
        self.client.logout()

        response = self.client.get(f"/calendar/feed/{feed.uuid}.ics")

        self.assertEqual(response.status_code, 200)

    def test_sidebar_contains_calendar_entry_and_daisyui_modal(self):
        response = self.client.get("/calendar/?month=2026-07")

        self.assertContains(response, 'href="/calendar/"')
        self.assertContains(response, "Calendar")
        self.assertContains(response, "fa-calendar")
        self.assertContains(response, 'class="modal"')
        self.assertContains(response, 'id="calendar-event-details"')
        self.assertContains(response, 'data-copy-target="#calendar-feed-url"')
