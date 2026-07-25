from django.test import RequestFactory, SimpleTestCase

from apps.common.htmx import is_htmx_fragment_request


class HtmxRequestTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def test_only_non_boosted_htmx_requests_are_fragments(self):
        fragment = self.factory.get("/page/", HTTP_HX_REQUEST="true")
        boosted = self.factory.get(
            "/page/",
            HTTP_HX_REQUEST="true",
            HTTP_HX_BOOSTED="true",
        )
        ordinary = self.factory.get("/page/")

        self.assertTrue(is_htmx_fragment_request(fragment))
        self.assertFalse(is_htmx_fragment_request(boosted))
        self.assertFalse(is_htmx_fragment_request(ordinary))
