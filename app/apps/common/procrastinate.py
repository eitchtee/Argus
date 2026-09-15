import ctypes
import functools
import gc
import inspect

import procrastinate
from django.db import close_old_connections


_CONNECTION_CLEANUP_WRAPPED = "_argus_connection_cleanup_wrapped"

try:
    _malloc_trim = ctypes.CDLL("libc.so.6").malloc_trim
except (OSError, AttributeError):  # not glibc (Windows, musl)
    _malloc_trim = None


def release_memory():
    """Hand the memory a finished task freed back to the OS.

    Sync tasks run on worker threads, and glibc rarely shrinks a thread's heap
    on its own, so a worker would otherwise stay at the size of the largest
    job it has ever run.
    """
    gc.collect()
    if _malloc_trim is not None:
        _malloc_trim(0)


def _wrap_task_with_django_connection_cleanup(task):
    if getattr(task.func, _CONNECTION_CLEANUP_WRAPPED, False):
        return

    func = task.func
    if inspect.iscoroutinefunction(func):

        @functools.wraps(func)
        async def async_wrapped(*args, **kwargs):
            close_old_connections()
            try:
                return await func(*args, **kwargs)
            finally:
                close_old_connections()
                release_memory()

        wrapped = async_wrapped
    else:

        @functools.wraps(func)
        def sync_wrapped(*args, **kwargs):
            close_old_connections()
            try:
                return func(*args, **kwargs)
            finally:
                close_old_connections()
                release_memory()

        wrapped = sync_wrapped

    setattr(wrapped, _CONNECTION_CLEANUP_WRAPPED, True)
    task.func = wrapped


def on_app_ready(app: procrastinate.App):
    """Wrap registered tasks so Django connections are cleaned up reliably."""
    for task in set(app.tasks.values()):
        _wrap_task_with_django_connection_cleanup(task)
