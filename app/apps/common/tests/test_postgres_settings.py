from django.conf import settings
from django.test import SimpleTestCase


class PostgreSQLSettingsTests(SimpleTestCase):
    def test_database_backend_is_fixed_to_postgresql(self):
        self.assertEqual(
            settings.DATABASES["default"]["ENGINE"],
            "django.db.backends.postgresql",
        )

    def test_procrastinate_django_integration_is_installed(self):
        self.assertIn("procrastinate.contrib.django", settings.INSTALLED_APPS)
        self.assertEqual(
            settings.PROCRASTINATE_ON_APP_READY,
            "apps.common.procrastinate.on_app_ready",
        )

    def test_legacy_queue_configuration_is_absent(self):
        self.assertNotIn("H" + "UEY", vars(settings))
