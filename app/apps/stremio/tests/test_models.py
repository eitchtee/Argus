from django.contrib import admin
from django.test import TestCase

from apps.stremio.admin import StremioAccountAdmin
from apps.stremio.models import StremioAccount


class StremioModelTests(TestCase):
    def test_admin_form_excludes_the_decrypted_auth_key(self):
        model_admin = StremioAccountAdmin(StremioAccount, admin.site)

        self.assertIn("auth_key", model_admin.exclude)
