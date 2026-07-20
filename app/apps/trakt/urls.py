from django.urls import path

from apps.trakt import views


urlpatterns = [
    path("user/trakt/connect/", views.connect, name="trakt_connect"),
    path("user/trakt/callback/", views.callback, name="trakt_callback"),
    path("user/trakt/disconnect/", views.disconnect, name="trakt_disconnect"),
    path("user/trakt/sync/", views.sync, name="trakt_sync"),
]
