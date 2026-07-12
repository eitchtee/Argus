from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse


class ToggleThemeViewTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            "user@example.com", password="password"
        )
        self.client.login(username="user@example.com", password="password")
        self.url = reverse("toggle_theme")

    def test_requires_authentication(self):
        self.client.logout()

        response = self.client.post(self.url, {"theme": "argus_light"})

        self.assertEqual(response.status_code, 302)
        self.assertIn("/login/", response["Location"])

    def test_rejects_get_requests(self):
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 405)

    def test_saves_requested_light_theme(self):
        response = self.client.post(
            self.url,
            {"theme": "argus_light"},
            HTTP_HX_REQUEST="true",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"theme": "argus_light"})
        self.assertEqual(self.client.session["theme"], "argus_light")

    def test_saves_requested_dark_theme(self):
        self.client.post(self.url, {"theme": "argus_light"})

        response = self.client.post(self.url, {"theme": "argus_dark"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"theme": "argus_dark"})
        self.assertEqual(self.client.session["theme"], "argus_dark")

    def test_rejects_unsupported_theme_without_changing_session(self):
        session = self.client.session
        session["theme"] = "argus_dark"
        session.save()

        response = self.client.post(self.url, {"theme": "not-a-theme"})

        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.client.session["theme"], "argus_dark")
