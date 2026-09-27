import secrets
from datetime import timedelta

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, HttpResponseBadRequest, HttpResponseNotFound
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views.decorators.http import require_GET, require_POST

from apps.catalog.localization import PROVIDER_DEFAULT_LANGUAGES, metadata_language_for_user
from apps.common.decorators.htmx import only_htmx
from apps.common.decorators.user import htmx_login_required
from apps.mdblist import discovery
from apps.mdblist.client import (
    MdblistAuthenticationError,
    MdblistClient,
    MdblistError,
    build_authorize_url,
    pkce_pair,
)
from apps.mdblist.config import oauth_client, oauth_configured, redirect_uri_for
from apps.mdblist.enrichment import load_card
from apps.mdblist.models import MdblistAccount, MdblistSyncIntent
from apps.mdblist.tasks import enqueue_account_sync


OAUTH_STATE_SESSION_KEY = "mdblist_oauth_state"
OAUTH_VERIFIER_SESSION_KEY = "mdblist_oauth_verifier"
OAUTH_REDIRECT_SESSION_KEY = "mdblist_oauth_redirect_uri"
HOME_MEDIA_TYPES = ("movie", "show")
HOME_TIMEFRAMES = ("today", "week")


# -- Account connection ------------------------------------------------------


@login_required
@require_GET
def connect(request):
    if not oauth_configured():
        return HttpResponse(
            "MDBList OAuth is not configured by the server administrator.",
            status=503,
        )
    state = secrets.token_urlsafe(32)
    verifier, challenge = pkce_pair()
    redirect_uri = redirect_uri_for(request)
    request.session[OAUTH_STATE_SESSION_KEY] = state
    request.session[OAUTH_VERIFIER_SESSION_KEY] = verifier
    # The token exchange must repeat the exact URI the consent screen saw.
    request.session[OAUTH_REDIRECT_SESSION_KEY] = redirect_uri
    request.session.modified = True
    return redirect(
        build_authorize_url(
            client_id=settings.MDBLIST_CLIENT_ID,
            redirect_uri=redirect_uri,
            state=state,
            code_challenge=challenge,
        )
    )


@login_required
@require_GET
def callback(request):
    expected_state = request.session.pop(OAUTH_STATE_SESSION_KEY, None)
    verifier = request.session.pop(OAUTH_VERIFIER_SESSION_KEY, None)
    redirect_uri = request.session.pop(OAUTH_REDIRECT_SESSION_KEY, None) or redirect_uri_for(request)
    request.session.modified = True
    received_state = request.GET.get("state", "")
    if (
        not expected_state
        or not verifier
        or not received_state
        or not secrets.compare_digest(expected_state, received_state)
    ):
        return HttpResponseBadRequest("Invalid MDBList OAuth state.")
    if not oauth_configured():
        return HttpResponse(
            "MDBList OAuth is not configured by the server administrator.",
            status=503,
        )
    code = request.GET.get("code", "")
    if not code:
        return HttpResponseBadRequest("MDBList OAuth callback did not include a code.")

    try:
        token = oauth_client().exchange_code(code, redirect_uri, verifier)
        profile = oauth_client(token.access_token).get_user()
    except MdblistError as exc:
        return HttpResponse(f"Unable to connect MDBList: {exc}", status=502)

    _save_account(
        request.user,
        profile,
        auth_method=MdblistAccount.AuthMethod.OAUTH,
        access_token=token.access_token,
        refresh_token=token.refresh_token,
        token_expires_at=(
            timezone.now() + timedelta(seconds=token.expires_in) if token.expires_in else None
        ),
    )
    messages.success(request, _("MDBList account connected. Initial synchronization queued."))
    return redirect(reverse("index"))


@login_required
@require_POST
def connect_key(request):
    """Connect with a personal API key, for servers without an OAuth app."""
    api_key = request.POST.get("api_key", "").strip()
    if not api_key:
        messages.error(request, _("Enter your MDBList API key."))
        return HttpResponse(status=204, headers={"HX-Refresh": "true"})
    try:
        profile = MdblistClient(api_key, api_key=True).get_user()
    except MdblistAuthenticationError:
        messages.error(request, _("MDBList did not accept that API key."))
        return HttpResponse(status=204, headers={"HX-Refresh": "true"})
    except MdblistError as exc:
        messages.error(request, _("Unable to reach MDBList: %(error)s") % {"error": exc})
        return HttpResponse(status=204, headers={"HX-Refresh": "true"})
    _save_account(
        request.user,
        profile,
        auth_method=MdblistAccount.AuthMethod.API_KEY,
        access_token=api_key,
        refresh_token="",
        token_expires_at=None,
    )
    messages.success(request, _("MDBList account connected. Initial synchronization queued."))
    return HttpResponse(status=204, headers={"HX-Refresh": "true"})


def _save_account(user, profile: dict, **credentials) -> MdblistAccount:
    account_defaults = {
        "mdblist_username": str(profile.get("username") or "")[:255],
        "mdblist_user_id": str(profile.get("user_id") or "")[:32],
        "plan": str(profile.get("plan") or "")[:32],
        "is_supporter": bool(profile.get("is_supporter")),
        **credentials,
        "initial_sync_complete": False,
        "activities_cursor": "",
        "last_activities": {},
        "pending_pushes": {},
        "sync_status": MdblistAccount.SyncStatus.OK,
        "last_error": "",
        "last_warning": "",
        "last_seen_at": timezone.now(),
    }
    account = MdblistAccount.objects.filter(user=user).first()
    if account is None:
        account = MdblistAccount.objects.create(user=user, **account_defaults)
    else:
        for name, value in account_defaults.items():
            setattr(account, name, value)
        account.save()
        # New credentials mean a fresh full pull; the old mirror is stale.
        account.library_items.all().delete()
    enqueue_account_sync(account.id)
    return account


@login_required
@require_POST
def disconnect(request):
    MdblistSyncIntent.objects.filter(user=request.user).delete()
    MdblistAccount.objects.filter(user=request.user).delete()
    messages.success(request, _("MDBList account disconnected."))
    return HttpResponse(status=204, headers={"HX-Refresh": "true"})


@login_required
@require_POST
def sync(request):
    account = MdblistAccount.objects.filter(user=request.user).only("id").first()
    if account is None:
        return HttpResponseNotFound("No MDBList account is connected.")
    # A manual sync retries whatever MDBList ignored or could not match before.
    MdblistAccount.objects.filter(id=account.id).update(
        pending_pushes={},
        last_seen_at=timezone.now(),
        updated_at=timezone.now(),
    )
    enqueue_account_sync(account.id)
    messages.success(request, _("MDBList synchronization queued."))
    return HttpResponse(status=204, headers={"HX-Refresh": "true"})


# -- Detail page card --------------------------------------------------------


@only_htmx
@htmx_login_required
@require_GET
def media_info(request, media_type, external_id):
    """The MDBList ratings card of a detail page, fetched on demand."""
    if media_type not in {"movie", "tv"}:
        return HttpResponseNotFound("Unknown media type.")
    default_provider = "tmdb" if media_type == "movie" else "tvdb"
    provider = request.GET.get("provider", default_provider)
    if provider not in PROVIDER_DEFAULT_LANGUAGES:
        provider = default_provider
    card = load_card(
        media_type,
        provider,
        external_id,
        language=metadata_language_for_user(request.user, provider),
        user=request.user,
    )
    return render(request, "mdblist/fragments/media_info.html", {"mdblist": card})


# -- Discovery ---------------------------------------------------------------


DISCOVER_SECTIONS = {
    "streaming-movies": {
        "heading": _("Top Streaming Movies"),
        "link": discovery.list_url("justwatch-streaming-charts", "movies"),
        "timeframes": True,
    },
    "streaming-shows": {
        "heading": _("Top Streaming Shows"),
        "link": discovery.list_url("justwatch-streaming-charts", "shows"),
        "timeframes": True,
    },
    "trending": {
        "heading": _("Trending on MDBList"),
        "link": discovery.list_url("trending"),
        "timeframes": False,
    },
    "popular": {
        "heading": _("Popular on MDBList"),
        "link": discovery.list_url("popular"),
        "timeframes": False,
    },
    "anticipated": {
        "heading": _("Most Anticipated"),
        "link": discovery.list_url("anticipated"),
        "timeframes": False,
    },
}


def discover_sections(user) -> list[dict]:
    """The MDBList sections of the Discover page, recommendations first when
    the viewer has an account of their own."""
    from apps.mdblist.config import user_account

    sections = [{"key": key, **section} for key, section in DISCOVER_SECTIONS.items()]
    if user_account(user) is not None:
        sections.insert(
            0,
            {
                "key": "recommended",
                "heading": _("Recommended for you"),
                "link": discovery.MDBLIST_WEB_URL,
                "timeframes": False,
            },
        )
    return sections


@only_htmx
@htmx_login_required
@require_GET
def discover_section(request, section):
    user = request.user
    timeframe = request.GET.get("timeframe", "today")
    if timeframe not in discovery.PERIODS:
        timeframe = "today"
    hide_seen = request.GET.get("show_seen") != "1"
    subtitle = None
    if section == "recommended":
        recommended, entries = discovery.recommendations(user)
        subtitle = (recommended or {}).get("label")
    elif section in {"streaming-movies", "streaming-shows"}:
        media_type = "movie" if section == "streaming-movies" else "show"
        entries = discovery.streaming_chart(user, media_type, timeframe, hide_seen=hide_seen)
    elif section in DISCOVER_SECTIONS:
        entries = discovery.official_list(user, section, hide_seen=hide_seen)
    else:
        return HttpResponseNotFound("Unknown section.")
    return render(
        request,
        "mdblist/fragments/rail.html",
        {"entries": entries, "section": section, "subtitle": subtitle},
    )


@only_htmx
@htmx_login_required
@require_GET
def home_charts(request):
    media_type = request.GET.get("media_type", "movie")
    if media_type not in HOME_MEDIA_TYPES:
        media_type = "movie"
    timeframe = request.GET.get("timeframe", "today")
    if timeframe not in HOME_TIMEFRAMES:
        timeframe = "today"
    entries = discovery.streaming_chart(request.user, media_type, timeframe, limit=18, hide_seen=True)
    return render(
        request,
        "mdblist/fragments/rail.html",
        {"entries": entries, "section": f"home-{media_type}"},
    )
