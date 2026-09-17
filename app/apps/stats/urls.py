from django.urls import path

from . import views


urlpatterns = [
    path("stats/", views.stats_page, name="stats-page"),
]
