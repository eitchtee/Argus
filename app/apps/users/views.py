from apps.common.decorators.user import htmx_login_required
from apps.users.forms import LoginForm, UserSettingsForm
from apps.trakt.models import TraktAccount
from apps.stremio.models import StremioAccount
from django.contrib import messages
from django.contrib.auth import logout
from django.contrib.auth.views import LoginView
from django.http import HttpResponse, HttpResponseBadRequest, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_http_methods


SUPPORTED_THEMES = {"argus_dark", "argus_light"}


def logout_view(request):
    logout(request)
    return redirect(reverse("login"))


class UserLoginView(LoginView):
    form_class = LoginForm
    template_name = "users/login.html"
    redirect_authenticated_user = True


@htmx_login_required
@require_http_methods(["GET", "POST"])
def update_settings(request):
    user_settings = request.user.settings

    if request.method == "POST":
        form = UserSettingsForm(request.POST, instance=user_settings)
        if form.is_valid():
            form.save()
            messages.success(request, _("Your settings have been updated"))
            if request.headers.get("HX-Request"):
                return HttpResponse(
                    status=204,
                    headers={"HX-Refresh": "true"},
                )
            return redirect(reverse("user_settings"))
    else:
        form = UserSettingsForm(instance=user_settings)

    trakt_account = (
        TraktAccount.objects.filter(user=request.user)
        .defer("access_token", "refresh_token")
        .first()
    )
    stremio_account = (
        StremioAccount.objects.filter(user=request.user)
        .defer("auth_key")
        .first()
    )
    return render(
        request,
        "users/pages/settings.html",
        {
            "form": form,
            "trakt_account": trakt_account,
            "stremio_account": stremio_account,
        },
    )


@htmx_login_required
@require_http_methods(["POST"])
def toggle_theme(request):
    theme = request.POST.get("theme")
    if theme not in SUPPORTED_THEMES:
        return HttpResponseBadRequest("Unsupported theme.")

    request.session["theme"] = theme
    request.session.modified = True
    return JsonResponse({"theme": theme})
