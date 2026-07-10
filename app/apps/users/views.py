from apps.common.decorators.htmx import only_htmx
from apps.common.decorators.user import htmx_login_required
from apps.users.forms import LoginForm, UserSettingsForm
from django.contrib import messages
from django.contrib.auth import logout
from django.contrib.auth.views import LoginView
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_http_methods


def logout_view(request):
    logout(request)
    return redirect(reverse("login"))


class UserLoginView(LoginView):
    form_class = LoginForm
    template_name = "users/login.html"
    redirect_authenticated_user = True


@only_htmx
@htmx_login_required
def update_settings(request):
    user_settings = request.user.settings

    if request.method == "POST":
        form = UserSettingsForm(request.POST, instance=user_settings)
        if form.is_valid():
            form.save()
            messages.success(request, _("Your settings have been updated"))
            return HttpResponse(
                status=204,
                headers={"HX-Refresh": "true"},
            )
    else:
        form = UserSettingsForm(instance=user_settings)

    return render(request, "users/fragments/user_settings.html", {"form": form})


@htmx_login_required
@require_http_methods(["GET"])
def toggle_theme(request):
    if not request.session.get("theme"):
        request.session["theme"] = "argus_dark"

    if request.session["theme"] == "argus_dark":
        request.session["theme"] = "argus_light"
    elif request.session["theme"] == "argus_light":
        request.session["theme"] = "argus_dark"
    else:
        request.session["theme"] = "argus_light"

    return HttpResponse(status=204)