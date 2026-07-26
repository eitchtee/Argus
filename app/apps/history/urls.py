from django.urls import path

from . import views


urlpatterns = [
    path("history/", views.history_page, name="history-page"),
    path(
        "history/movies/<int:movie_id>/undo/",
        views.undo_movie,
        name="history-undo-movie",
    ),
    path(
        "history/episodes/<int:episode_id>/undo/",
        views.undo_episode,
        name="history-undo-episode",
    ),
]
