import secrets
from datetime import timedelta
from urllib.parse import urlencode

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, HttpResponseBadRequest, HttpResponseNotFound
from django.shortcuts import redirect
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from apps.trakt.client import TraktClient, TraktError
from apps.trakt.models import TraktAccount, TraktSyncIntent
from apps.trakt.tasks import enqueue_account_sync


OAUTH_STATE_SESSION_KEY = "trakt_oauth_state"
AUTHORIZE_URL = "https://trakt.tv/oauth/authorize"


@login_required
@require_GET
def connect(request):
    if not _configured():
        return HttpResponse(
            "Trakt integration is not configured by the server administrator.",
            status=503,
        )

    state = secrets.token_urlsafe(32)
    request.session[OAUTH_STATE_SESSION_KEY] = state
    request.session.modified = True
    query = urlencode(
        {
            "response_type": "code",
            "client_id": settings.TRAKT_CLIENT_ID,
            "redirect_uri": settings.TRAKT_REDIRECT_URI,
            "state": state,
        }
    )
    return redirect(f"{AUTHORIZE_URL}?{query}")


@login_required
@require_GET
def callback(request):
    expected_state = request.session.pop(OAUTH_STATE_SESSION_KEY, None)
    request.session.modified = True
    received_state = request.GET.get("state", "")
    if not expected_state or not received_state or not secrets.compare_digest(
        expected_state,
        received_state,
    ):
        return HttpResponseBadRequest("Invalid Trakt OAuth state.")
    if not _configured():
        return HttpResponse(
            "Trakt integration is not configured by the server administrator.",
            status=503,
        )

    code = request.GET.get("code", "")
    if not code:
        return HttpResponseBadRequest("Trakt OAuth callback did not include a code.")

    client = TraktClient(
        "",
        client_id=settings.TRAKT_CLIENT_ID,
        client_secret=settings.TRAKT_CLIENT_SECRET,
        user_agent=settings.TRAKT_USER_AGENT,
    )
    try:
        token = client.exchange_code(code, settings.TRAKT_REDIRECT_URI)
        authorized_client = TraktClient(
            token.access_token,
            client_id=settings.TRAKT_CLIENT_ID,
            client_secret=settings.TRAKT_CLIENT_SECRET,
            user_agent=settings.TRAKT_USER_AGENT,
        )
        user_settings = authorized_client.get_user_settings()
    except TraktError as exc:
        return HttpResponse(f"Unable to connect Trakt: {exc}", status=502)

    account_defaults = {
        "trakt_username": str(user_settings.get("username") or ""),
        "access_token": token.access_token,
        "refresh_token": token.refresh_token,
        "token_expires_at": timezone.now() + timedelta(seconds=token.expires_in),
        "initial_sync_complete": False,
        "sync_status": TraktAccount.SyncStatus.OK,
        "last_error": "",
    }
    account = TraktAccount.objects.only("id").filter(user=request.user).first()
    if account is None:
        account = TraktAccount.objects.create(user=request.user, **account_defaults)
    else:
        TraktAccount.objects.filter(id=account.id).update(
            **account_defaults,
            updated_at=timezone.now(),
        )
    enqueue_account_sync(account.id)
    messages.success(request, "Trakt.tv account connected. Initial synchronization queued.")
    return redirect(reverse("index"))


@login_required
@require_POST
def disconnect(request):
    TraktSyncIntent.objects.filter(user=request.user).delete()
    TraktAccount.objects.filter(user=request.user).delete()
    messages.success(request, "Trakt.tv account disconnected.")
    return HttpResponse(status=204, headers={"HX-Refresh": "true"})


@login_required
@require_POST
def sync(request):
    account = TraktAccount.objects.filter(user=request.user).only("id").first()
    if account is None:
        return HttpResponseNotFound("No Trakt.tv account is connected.")
    enqueue_account_sync(account.id)
    messages.success(request, "Trakt.tv synchronization queued.")
    return HttpResponse(status=204, headers={"HX-Refresh": "true"})


def _configured() -> bool:
    return bool(
        settings.TRAKT_CLIENT_ID
        and settings.TRAKT_CLIENT_SECRET
        and settings.TRAKT_REDIRECT_URI
    )
