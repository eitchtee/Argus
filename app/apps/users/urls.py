from django.urls import path

from . import views

urlpatterns = [
    path("login/", views.UserLoginView.as_view(), name="login"),
    path("logout/", views.logout_view, name="logout"),
    path(
        "user/session/toggle-theme/",
        views.toggle_theme,
        name="toggle_theme",
    ),
    path(
        "user/settings/",
        views.update_settings,
        name="user_settings",
    ),
]