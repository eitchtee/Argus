from django.shortcuts import render

from apps.common.decorators.user import htmx_login_required
from apps.movies.services import get_watch_something


@htmx_login_required
def index(request):
    context = {"watch_something_movies": get_watch_something(request.user)}
    return render(request, "home/pages/index.html", context)
