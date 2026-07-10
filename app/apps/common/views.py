from django.shortcuts import render

from apps.common.decorators.htmx import only_htmx
from apps.common.decorators.user import htmx_login_required
from django.views.decorators.http import require_http_methods


@only_htmx
@htmx_login_required
@require_http_methods(["GET"])
def toasts(request):
    return render(request, "common/fragments/toasts.html")