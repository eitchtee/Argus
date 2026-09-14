from django.utils.cache import patch_vary_headers

# The headers `is_htmx_fragment_request` branches on.
HTMX_VARY_HEADERS = ("HX-Request", "HX-Boosted", "HX-History-Restore-Request")


class HtmxVaryMiddleware:
    """Keeps htmx fragments and full pages apart in the browser cache.

    Pages load their content by requesting their own URL with htmx, so one URL
    answers with both a full page and a bare fragment. Without `Vary`, the
    browser caches whichever came last and restores a discarded tab from the
    fragment -- no <head>, so no styles.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        patch_vary_headers(response, HTMX_VARY_HEADERS)
        return response
