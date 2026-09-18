from django.urls import path

from apps.simkl import views


urlpatterns = [
    path("user/simkl/connect/", views.connect, name="simkl_connect"),
    path("user/simkl/callback/", views.callback, name="simkl_callback"),
    path("user/simkl/disconnect/", views.disconnect, name="simkl_disconnect"),
    path("user/simkl/sync/", views.sync, name="simkl_sync"),
    path("discover/", views.discover, name="discover"),
    path("discover/section/<slug:section>/", views.discover_section, name="discover-section"),
    path("simkl/home-trending/", views.home_trending, name="simkl-home-trending"),
    path("simkl/open/<slug:media_type>/<int:simkl_id>/", views.open_item, name="simkl-open"),
    path(
        "simkl/info/<slug:media_type>/<str:external_id>/",
        views.media_info,
        name="simkl-media-info",
    ),
]
