from django.urls import path

from . import views

urlpatterns = [
    path("tv/up-next/", views.up_next, name="tv-up-next"),
    path(
        "tv/up-next/episodes/<int:episode_id>/watched/",
        views.up_next_episode_watched,
        name="tv-up-next-episode-watched",
    ),
    path("tv/<str:external_id>/", views.show_detail, name="tv-detail"),
    path("tv/<str:external_id>/track/", views.show_track, name="tv-detail-track"),
    path("tv/<str:external_id>/drop/", views.show_drop, name="tv-detail-drop"),
    path("tv/<str:external_id>/pause/", views.show_pause, name="tv-detail-pause"),
    path("tv/<str:external_id>/delete/", views.show_delete, name="tv-detail-delete"),
    path("tv/<str:external_id>/watched/", views.show_watched, name="tv-detail-watched"),
    path(
        "tv/<str:external_id>/seasons/<int:season_id>/watched/",
        views.season_watched,
        name="tv-detail-season-watched",
    ),
    path(
        "tv/<str:external_id>/episodes/<int:episode_id>/watched/",
        views.episode_watched,
        name="tv-detail-episode-watched",
    ),
    path(
        "tv/<str:external_id>/episodes/<int:episode_id>/",
        views.episode_detail,
        name="tv-episode-detail",
    ),
    path(
        "tv/<str:external_id>/episodes/<int:episode_id>/detail-watched/",
        views.episode_detail_watched,
        name="tv-episode-detail-watched",
    ),
    path("tv/home/watchlist/", views.home_watchlist, name="tv-home-watchlist"),
    path(
        "tv/home/watchlist/episodes/<int:episode_id>/watched/",
        views.home_watchlist_episode_watched,
        name="tv-home-watchlist-episode-watched",
    ),
    path("tv/home/upcoming/", views.home_upcoming, name="tv-home-upcoming"),
]
