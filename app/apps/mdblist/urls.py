from django.urls import path

from apps.mdblist import views


urlpatterns = [
    path("user/mdblist/connect/", views.connect, name="mdblist_connect"),
    path("user/mdblist/connect-key/", views.connect_key, name="mdblist_connect_key"),
    path("user/mdblist/callback/", views.callback, name="mdblist_callback"),
    path("user/mdblist/disconnect/", views.disconnect, name="mdblist_disconnect"),
    path("user/mdblist/sync/", views.sync, name="mdblist_sync"),
    path("mdblist/home-charts/", views.home_charts, name="mdblist-home-charts"),
    path(
        "mdblist/discover/<slug:section>/",
        views.discover_section,
        name="mdblist-discover-section",
    ),
    path(
        "mdblist/info/<slug:media_type>/<str:external_id>/",
        views.media_info,
        name="mdblist-media-info",
    ),
]
