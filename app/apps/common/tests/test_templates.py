from django.template.loader import get_template
from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase
from django.urls import reverse


class BaseTemplateTests(SimpleTestCase):
    def test_base_layout_mounts_toast_fragment(self):
        source = get_template("layouts/base.html").template.source

        self.assertIn('{% include "common/fragments/toasts.html" %}', source)


class ToastEndpointTests(TestCase):
    def setUp(self):
        user = get_user_model().objects.create_user(
            "user@example.com",
            password="password",
        )
        self.client.login(username="user@example.com", password="password")

    def test_toast_poll_response_does_not_mount_another_poll_trigger(self):
        response = self.client.get(
            reverse("toasts"),
            HTTP_HX_REQUEST="true",
        )

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'id="toasts"')
        self.assertNotContains(response, "hx-get=")
