def is_htmx_fragment_request(request):
    return bool(request.headers.get("HX-Request")) and not bool(
        request.headers.get("HX-Boosted")
    )
