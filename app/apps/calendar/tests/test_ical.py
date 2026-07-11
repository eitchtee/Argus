from datetime import date, datetime, timezone

from django.test import SimpleTestCase
from icalendar import Calendar

from apps.calendar.events import CalendarEvent
from apps.calendar.ical import render_icalendar


class ICalendarSerializerTests(SimpleTestCase):
    def timed_event(self, **overrides):
        values = {
            "kind": "episode",
            "object_id": 1,
            "external_id": "example",
            "title": "Pilot",
            "subtitle": "S01E01",
            "overview": "An episode overview.",
            "release_date": date(2026, 7, 10),
            "starts_at": datetime(2026, 7, 10, 21, 0, tzinfo=timezone.utc),
            "ends_at": datetime(2026, 7, 10, 22, 0, tzinfo=timezone.utc),
            "runtime": 60,
            "status": "tracked",
            "show_name": "Example Show",
            "network": "Example Network",
            "episode_id": 1,
            "show_id": 1,
            "season_number": 1,
            "episode_number": 1,
        }
        values.update(overrides)
        return CalendarEvent(**values)

    def all_day_event(self, **overrides):
        return self.timed_event(
            starts_at=None,
            ends_at=None,
            runtime=None,
            **overrides,
        )

    def movie_event(self, **overrides):
        values = {
            "kind": "movie",
            "object_id": 1,
            "external_id": "movie-1",
            "title": "Movie Release",
            "subtitle": "Movie",
            "overview": "A movie overview.",
            "release_date": date(2026, 7, 10),
            "starts_at": None,
            "ends_at": None,
            "runtime": 120,
            "status": "tracked",
            "director": "Example Director",
            "genres": ("Drama",),
            "movie_id": 1,
        }
        values.update(overrides)
        return CalendarEvent(**values)

    def test_renders_timed_event_as_utc_with_runtime(self):
        ical = render_icalendar(
            [self.timed_event()],
            now=datetime(2026, 7, 1, tzinfo=timezone.utc),
        )

        parsed = Calendar.from_ical(ical)
        event = next(component for component in parsed.walk("VEVENT"))

        self.assertEqual(
            event.decoded("dtstart"),
            datetime(2026, 7, 10, 21, 0, tzinfo=timezone.utc),
        )
        self.assertEqual(
            event.decoded("dtend"),
            datetime(2026, 7, 10, 22, 0, tzinfo=timezone.utc),
        )
        self.assertEqual(str(event.decoded("uid")), "episode-1@argus")

    def test_renders_date_only_event_with_exclusive_end(self):
        ical = render_icalendar(
            [self.all_day_event(release_date=date(2026, 7, 10))],
            now=datetime(2026, 7, 1, tzinfo=timezone.utc),
        )

        parsed = Calendar.from_ical(ical)
        event = next(component for component in parsed.walk("VEVENT"))

        self.assertEqual(event.decoded("dtstart"), date(2026, 7, 10))
        self.assertEqual(event.decoded("dtend"), date(2026, 7, 11))

    def test_escapes_and_folds_event_text(self):
        ical = render_icalendar([
            self.timed_event(
                title="A, very; long\n title",
                overview="\n".join(["A long overview"] * 20),
            )
        ])

        parsed = Calendar.from_ical(ical)
        event = next(component for component in parsed.walk("VEVENT"))

        self.assertEqual(
            str(event.decoded("summary")),
            "Example Show - S01E01 - A, very; long\n title",
        )
        self.assertLessEqual(max(len(line.encode("utf-8")) for line in ical.splitlines()), 75)

    def test_renders_movie_as_all_day_with_stable_movie_uid(self):
        ical = render_icalendar(
            [self.movie_event(release_date=date(2026, 7, 10))],
            now=datetime(2026, 7, 1, tzinfo=timezone.utc),
        )

        event = next(component for component in Calendar.from_ical(ical).walk("VEVENT"))

        self.assertEqual(event.decoded("dtstart"), date(2026, 7, 10))
        self.assertEqual(event.decoded("dtend"), date(2026, 7, 11))
        self.assertEqual(str(event.decoded("uid")), "movie-1@argus")
        self.assertIn("Movie Release", str(event.decoded("summary")))
