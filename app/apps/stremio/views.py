from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ImproperlyConfigured
from django.http import HttpResponse, HttpResponseBadRequest, HttpResponseNotFound
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from apps.stremio.client import StremioAPIError, StremioClient
from apps.stremio.models import StremioAccount, StremioSyncIntent
from apps.stremio.tasks import enqueue_account_sync


LINK_CODE_SESSION_KEY = "stremio_link_code"


@login_required
@require_GET
def connect(request):
    try:
        link = StremioClient("").create_link_code()
    except StremioAPIError as exc:
        return HttpResponse(f"Unable to start Stremio connection: {exc}", status=502)
    request.session[LINK_CODE_SESSION_KEY] = str(link["code"])
    request.session.modified = True
    return render(request, "users/pages/stremio_connect.html", {"link": link})


@login_required
@require_POST
def complete(request):
    code = request.session.get(LINK_CODE_SESSION_KEY)
    if not code:
        return HttpResponseBadRequest("No Stremio link authorization is pending.")
    try:
        auth_key = StremioClient("").read_link_code(code)
    except StremioAPIError as exc:
        return HttpResponse(f"Unable to authorize Stremio: {exc}", status=502)
    if auth_key is None:
        return render(
            request,
            "users/pages/stremio_connect.html",
            {"link": {"code": code}, "pending": True},
            status=202,
        )
    try:
        user_data = StremioClient(auth_key).get_user()
    except StremioAPIError as exc:
        return HttpResponse(f"Unable to validate Stremio account: {exc}", status=502)

    account_defaults = {
        "stremio_user_id": str(user_data.get("_id") or ""),
        "stremio_username": str(user_data.get("username") or user_data.get("email") or ""),
        "auth_key": auth_key,
        "initial_sync_complete": False,
        "library_synced_at": None,
        "sync_status": StremioAccount.SyncStatus.OK,
        "last_error": "",
    }
    account = StremioAccount.objects.filter(user=request.user).only("id").first()
    if account is None:
        account = StremioAccount.objects.create(user=request.user, **account_defaults)
    else:
        StremioAccount.objects.filter(id=account.id).update(
            **account_defaults,
            updated_at=timezone.now(),
        )
    request.session.pop(LINK_CODE_SESSION_KEY, None)
    request.session.modified = True
    enqueue_account_sync(account.id)
    messages.success(request, "Stremio account connected. Initial synchronization queued.")
    return redirect(reverse("index"))


@login_required
@require_POST
def disconnect(request):
    try:
        account = StremioAccount.objects.filter(user=request.user).first()
        if account is not None and account.auth_key:
            StremioClient(account.auth_key).logout()
    except (ImproperlyConfigured, StremioAPIError):
        pass
    StremioSyncIntent.objects.filter(user=request.user).delete()
    StremioAccount.objects.filter(user=request.user).delete()
    messages.success(request, "Stremio account disconnected.")
    return HttpResponse(status=204, headers={"HX-Refresh": "true"})


@login_required
@require_POST
def sync(request):
    account = StremioAccount.objects.filter(user=request.user).only("id").first()
    if account is None:
        return HttpResponseNotFound("No Stremio account is connected.")
    enqueue_account_sync(account.id)
    messages.success(request, "Stremio synchronization queued.")
    return HttpResponse(status=204, headers={"HX-Refresh": "true"})
