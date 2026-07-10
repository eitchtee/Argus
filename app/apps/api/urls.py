from django.urls import path

from apps.catalog.api import search_view
from apps.movies.api import (
    movie_list_view,
    movie_seen_view,
    movie_tier_view,
    movie_track_view,
    movie_watchlist_view,
)

urlpatterns = [
    path("search", search_view, name="catalog-search"),
    path("movies/", movie_list_view, name="movie-list"),
    path("movies/track", movie_track_view, name="movie-track"),
    path("movies/<int:movie_id>/seen", movie_seen_view, name="movie-seen"),
    path("movies/<int:movie_id>/tier", movie_tier_view, name="movie-tier"),
    path(
        "movies/<int:movie_id>/watchlist",
        movie_watchlist_view,
        name="movie-watchlist",
    ),
]
