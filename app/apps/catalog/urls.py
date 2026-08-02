from django.urls import path

from . import views

urlpatterns = [
    path("search/", views.search_page, name="catalog-search-page"),
    path("search/results/", views.search_results, name="catalog-search-results"),
    path("search/track/", views.track, name="catalog-track"),
    path(
        "media/<str:media_type>/<str:external_id>/artwork/",
        views.media_artwork_preferences,
        name="media-artwork-preferences",
    ),
]
