from functools import wraps

from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.http import HttpResponse
from django.shortcuts import redirect
from django.urls import NoReverseMatch, reverse


def is_superuser(view):
    @wraps(view)
    def _view(request, *args, **kwargs):
        if not request.user.is_superuser:
            raise PermissionDenied

        return view(request, *args, **kwargs)

    return _view


def htmx_login_required(function=None, login_url=None):
    """
    Decorator that checks if the user is logged in.

    Allows overriding the default login URL.

    If the user is not logged in:
    - If "hx-request" is present in the request header, it returns a 200 response
      with a "HX-Redirect" header containing the determined login URL.
    - If "hx-request" is not present, it redirects to the login page normally.
    """

    def decorator(view_func):
        @wraps(view_func)
        def wrapped_view(request, *args, **kwargs):
            if request.user.is_authenticated:
                return view_func(request, *args, **kwargs)

            # Determine the login URL
            resolved_login_url = login_url
            if not resolved_login_url:
                resolved_login_url = settings.LOGIN_URL

            # Try to reverse the URL name if it's not a path
            try:
                if "/" not in resolved_login_url and "." not in resolved_login_url:
                    login_url_path = reverse(resolved_login_url)
                else:
                    login_url_path = resolved_login_url
            except NoReverseMatch:
                login_url_path = resolved_login_url

            redirect_path = f"{login_url_path}?next={request.get_full_path()}"

            if request.headers.get("hx-request"):
                response = HttpResponse()
                response["HX-Redirect"] = login_url_path
                return response
            return redirect(redirect_path)

        return wrapped_view

    if function:
        return decorator(function)
    return decorator