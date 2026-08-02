from django.shortcuts import render

from apps.catalog.artwork import localized_media_records
from apps.common.decorators.user import htmx_login_required
from apps.common.htmx import is_htmx_fragment_request
from apps.movies.services import get_watch_something


@htmx_login_required
def index(request):
    if not is_htmx_fragment_request(request):
        return render(request, "home/pages/index.html")

    context = {
        "watch_something_movies": localized_media_records(
            get_watch_something(request.user, count=6),
            request.user,
        )
    }
    return render(request, "home/fragments/watch_something.html", context)
