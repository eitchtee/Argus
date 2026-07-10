"""
    threadlocals middleware
    ~~~~~~~~~~~~~~~~~~~~~~~

make the request object everywhere available (e.g. in model instance).

based on: http://code.djangoproject.com/wiki/CookBookThreadlocalsAndUser

Usage:
--------------------------------------------------------------------------
from apps.common.middleware.thread_local import get_current_request, get_current_user

# Get the current request object:
request = get_current_request()

# You can get the current user directly with:
user = get_current_user()
--------------------------------------------------------------------------
"""

from threading import local

from django.utils.deprecation import MiddlewareMixin

_thread_locals = local()


def get_current_request():
    """returns the request object for this thread"""
    return getattr(_thread_locals, "request", None)


def get_current_user():
    """returns the current user, if exist, otherwise returns None"""
    request = get_current_request()
    if request:
        return getattr(request, "user", None)

    return getattr(_thread_locals, "user", None)


def write_current_user(user):
    _thread_locals.user = user


def delete_current_user():
    del _thread_locals.user


class ThreadLocalMiddleware(MiddlewareMixin):
    """Simple middleware that adds the request object in thread local storage."""

    def process_request(self, request):
        _thread_locals.request = request

    def process_response(self, request, response):
        if hasattr(_thread_locals, "request"):
            del _thread_locals.request
        return response

    def process_exception(self, request, exception):
        if hasattr(_thread_locals, "request"):
            del _thread_locals.request