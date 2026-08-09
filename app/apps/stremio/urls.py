from django.urls import path

from apps.stremio import views


urlpatterns = [
    path("user/stremio/connect/", views.connect, name="stremio_connect"),
    path("user/stremio/complete/", views.complete, name="stremio_complete"),
    path("user/stremio/disconnect/", views.disconnect, name="stremio_disconnect"),
    path("user/stremio/sync/", views.sync, name="stremio_sync"),
]

