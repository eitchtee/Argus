from django.shortcuts import render

from apps.catalog.localization import LocalizedRecord, metadata_language_for_user
from apps.common.decorators.user import htmx_login_required
from apps.movies.services import get_watch_something


@htmx_login_required
def index(request):
    language = metadata_language_for_user(request.user, "tmdb")
    context = {
        "watch_something_movies": [
            LocalizedRecord(movie, language)
            for movie in get_watch_something(request.user)
        ]
    }
    return render(request, "home/pages/index.html", context)
