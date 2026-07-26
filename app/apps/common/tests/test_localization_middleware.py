from types import SimpleNamespace

from django.http import HttpResponse
from django.test import RequestFactory, SimpleTestCase, override_settings
from django.utils import timezone, translation

from apps.common.middleware.localization import LocalizationMiddleware


class LocalizationMiddlewareTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.addCleanup(translation.deactivate)
        self.addCleanup(timezone.deactivate)

    def make_request(
        self,
        *,
        language="auto",
        user_timezone="auto",
        cookie=None,
        accept_language="pt-BR, en;q=0.8",
    ):
        request = self.factory.get("/", HTTP_ACCEPT_LANGUAGE=accept_language)
        request.COOKIES = {"mytz": cookie} if cookie else {}
        request.user = SimpleNamespace(
            is_authenticated=True,
            settings=SimpleNamespace(language=language, timezone=user_timezone),
        )
        return request

    def run_middleware(self, request):
        return LocalizationMiddleware(
            lambda _request: HttpResponse(
                f"{translation.get_language()}|{timezone.get_current_timezone_name()}"
            )
        )(request)

    def test_automatic_preferences_use_browser_language_and_timezone_cookie(self):
        response = self.run_middleware(
            self.make_request(cookie="America/Sao_Paulo")
        )

        self.assertEqual(response.content.decode(), "pt-br|America/Sao_Paulo")

    def test_automatic_language_supports_the_full_browser_language_catalog(self):
        response = self.run_middleware(
            self.make_request(accept_language="fr-FR, en;q=0.8")
        )

        self.assertEqual(response.content.decode(), "fr|UTC")

    def test_explicit_preferences_override_browser_defaults(self):
        response = self.run_middleware(
            self.make_request(language="en", user_timezone="UTC", cookie="Asia/Tokyo")
        )

        self.assertEqual(response.content.decode(), "en|UTC")

    @override_settings(TIME_ZONE="UTC")
    def test_invalid_automatic_timezone_falls_back_to_default(self):
        response = self.run_middleware(self.make_request(cookie="not/a-timezone"))

        self.assertEqual(response.content.decode(), "pt-br|UTC")
