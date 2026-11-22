from django.urls import path

from . import people, views

urlpatterns = [
    path("search/", views.search_page, name="catalog-search-page"),
    path("search/results/", views.search_results, name="catalog-search-results"),
    path("search/library/", views.library_search, name="catalog-library-search"),
    path("search/track/", views.track, name="catalog-track"),
    path("search/switch/", views.switch, name="catalog-switch"),
    path("search/watched/", views.watched, name="catalog-watched"),
    path("people/lookup/", people.person_lookup, name="person-lookup"),
    path("people/titles/<str:media_type>/<str:tmdb_id>/", people.person_title_redirect, name="person-title"),
    path("people/<str:person_id>/content/", people.person_detail_content, name="person-detail-content"),
    path("people/<str:person_id>/", people.person_detail, name="person-detail"),
    path(
        "media/<str:media_type>/<str:external_id>/artwork/",
        views.media_artwork_preferences,
        name="media-artwork-preferences",
    ),
    path(
        "media/<str:media_type>/<str:external_id>/rating/",
        views.media_rating,
        name="media-rating",
    ),
]
