from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from django.template import Context, Template
from django.test import SimpleTestCase
from django.utils import timezone, translation


class UserDateTemplateTagTests(SimpleTestCase):
    def test_datetime_is_converted_to_user_timezone_and_format(self):
        user = SimpleNamespace(
            is_authenticated=True,
            settings=SimpleNamespace(
                date_format="d.m.Y",
                datetime_format="d.m.Y H:i",
            ),
        )
        request = SimpleNamespace(user=user)
        value = timezone.make_aware(
            datetime(2025, 1, 20, 15, 30),
            ZoneInfo("UTC"),
        )

        with timezone.override("America/Sao_Paulo"), translation.override("en"):
            rendered = Template(
                "{% load dates %}{% user_date value %}|{% user_datetime value %}|{% user_time value %}"
            ).render(Context({"request": request, "value": value}))

        self.assertEqual(rendered, "20.01.2025|20.01.2025 12:30|12:30")
