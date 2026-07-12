from django.urls import path

from . import views

urlpatterns = [
    path("movies/watchlist/", views.movie_watchlist, name="movies-watchlist-page"),
    path("movies/<str:external_id>/", views.movie_detail, name="movie-detail"),
    path("movies/<str:external_id>/track/", views.movie_track, name="movie-detail-track"),
    path("movies/<str:external_id>/watched/", views.movie_watched, name="movie-detail-watched"),
]
