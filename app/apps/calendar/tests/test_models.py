from django.contrib.auth import get_user_model
from django.db import IntegrityError
from django.test import TestCase

from apps.calendar.models import CalendarFeed


class CalendarFeedModelTests(TestCase):
    def test_feed_uuid_is_stable_and_unique_per_user(self):
        user = get_user_model().objects.create_user("user@example.com")

        first = CalendarFeed.objects.create(user=user)
        first.refresh_from_db()

        self.assertIsNotNone(first.uuid)
        self.assertEqual(CalendarFeed.objects.filter(user=user).count(), 1)
        self.assertTrue(CalendarFeed._meta.get_field("uuid").unique)

        other = get_user_model().objects.create_user("other@example.com")
        second = CalendarFeed.objects.create(user=other)
        self.assertNotEqual(first.uuid, second.uuid)

    def test_user_can_have_only_one_feed(self):
        user = get_user_model().objects.create_user("user@example.com")
        CalendarFeed.objects.create(user=user)

        with self.assertRaises(IntegrityError):
            CalendarFeed.objects.create(user=user)
