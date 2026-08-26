from django.urls import path

from . import views

urlpatterns = [
    path("calendar/", views.calendar_page, name="calendar"),
    path(
        "calendar/feed/<uuid:uuid>.ics",
        views.calendar_feed,
        name="calendar-feed",
    ),
]
