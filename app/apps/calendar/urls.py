from django.urls import path

from . import views

urlpatterns = [
    path("calendar/", views.calendar_page, name="calendar"),
    path(
        "calendar/episodes/<int:episode_id>/",
        views.calendar_episode_detail,
        name="calendar-episode-detail",
    ),
    path(
        "calendar/movies/<int:movie_id>/",
        views.calendar_movie_detail,
        name="calendar-movie-detail",
    ),
    path(
        "calendar/feed/<uuid:uuid>.ics",
        views.calendar_feed,
        name="calendar-feed",
    ),
]
