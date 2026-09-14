from django.http import HttpResponse
from django.test import RequestFactory, SimpleTestCase
from django.utils.cache import has_vary_header

from apps.common.middleware.htmx import HTMX_VARY_HEADERS, HtmxVaryMiddleware


class HtmxVaryMiddlewareTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def test_responses_vary_on_htmx_headers(self):
        response = HtmxVaryMiddleware(lambda _request: HttpResponse())(
            self.factory.get("/page/")
        )

        for header in HTMX_VARY_HEADERS:
            self.assertTrue(has_vary_header(response, header), header)

    def test_existing_vary_headers_are_kept(self):
        def view(_request):
            response = HttpResponse()
            response["Vary"] = "Cookie"
            return response

        response = HtmxVaryMiddleware(view)(self.factory.get("/page/"))

        self.assertTrue(has_vary_header(response, "Cookie"))
        self.assertTrue(has_vary_header(response, "HX-Request"))
