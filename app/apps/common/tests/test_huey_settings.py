from django.conf import settings
from django.test import SimpleTestCase


class HueySettingsTests(SimpleTestCase):
    def test_huey_uses_the_worker_local_clock(self):
        self.assertFalse(settings.HUEY["utc"])
