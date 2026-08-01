from django.urls import path

from . import views


urlpatterns = [
    path("user/import/trakt/", views.trakt_panel, name="import_trakt"),
    path(
        "user/import/trakt/upload/",
        views.trakt_upload,
        name="import_trakt_upload",
    ),
    path(
        "user/import/trakt/status/<int:job_id>/",
        views.trakt_status,
        name="import_trakt_status",
    ),
]
