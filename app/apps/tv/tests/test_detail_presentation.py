from datetime import date, time, timedelta
from zoneinfo import ZoneInfo

from django.test import SimpleTestCase
from django.utils import timezone

from apps.tv.views import (
    _air_time_context,
    _convert_air_time_to_user_timezone,
    _episode_air_status,
)


class DetailPresentationTests(SimpleTestCase):
    def test_air_time_is_converted_from_provider_timezone_to_active_timezone(self):
        with timezone.override(ZoneInfo("America/Sao_Paulo")):
            converted = _convert_air_time_to_user_timezone(
                time(21, 0),
                "America/New_York",
                date(2026, 7, 25),
            )

        self.assertEqual(converted, time(22, 0))

    def test_invalid_provider_timezone_falls_back_to_utc(self):
        with timezone.override(ZoneInfo("America/Sao_Paulo")):
            converted = _convert_air_time_to_user_timezone(
                time(21, 0),
                "Not/A_Timezone",
                date(2026, 7, 25),
            )

        self.assertEqual(converted, time(18, 0))

    def test_air_time_context_converts_the_airing_date_with_the_time(self):
        with timezone.override(ZoneInfo("Asia/Tokyo")):
            context = _air_time_context(
                time(23, 0),
                "America/New_York",
                date(2026, 7, 25),
            )

        self.assertEqual(context["airs_time"], time(12, 0))
        self.assertEqual(context["airs_date"], date(2026, 7, 26))

    def test_episode_air_status_distinguishes_aired_upcoming_and_tba(self):
        today = date(2026, 7, 25)

        self.assertEqual(_episode_air_status(today - timedelta(days=1), today), "aired")
        self.assertEqual(_episode_air_status(today, today), "aired")
        self.assertEqual(_episode_air_status(today + timedelta(days=1), today), "upcoming")
        self.assertEqual(_episode_air_status(None, today), "tba")
