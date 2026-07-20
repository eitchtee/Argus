"""
URL configuration for the Argus project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.2/topics/http/urls/
"""

from django.contrib import admin
from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView


urlpatterns = [
    path("admin/", admin.site.urls),
    path("hijack/", include("hijack.urls")),
    path("__debug__/", include("debug_toolbar.urls")),
    path("__reload__/", include("django_browser_reload.urls")),
    path("", include("pwa.urls")),
    path("api/", include("apps.api.urls")),
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path(
        "api/docs/",
        SpectacularSwaggerView.as_view(url_name="schema"),
        name="swagger-ui",
    ),
    path("auth/", include("allauth.urls")),
    path("", include("apps.common.urls")),
    path("", include("apps.users.urls")),
    path("", include("apps.home.urls")),
    path("", include("apps.catalog.urls")),
    path("", include("apps.movies.urls")),
    path("", include("apps.tv.urls")),
    path("", include("apps.calendar.urls")),
    path("", include("apps.trakt.urls")),
]
