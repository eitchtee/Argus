import secrets

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
from apps.simkl import trending
from apps.simkl.enrichment import load_card, resolve_argus_url
from apps.simkl.models import build_simkl_url
from apps.simkl.client import SimklClient, SimklError, build_authorize_url
from apps.simkl.config import catalog_configured, is_configured, redirect_uri_for
from apps.simkl.models import SimklAccount, SimklSyncIntent
from apps.simkl.tasks import enqueue_account_sync


OAUTH_STATE_SESSION_KEY = "simkl_oauth_state"
OAUTH_REDIRECT_SESSION_KEY = "simkl_oauth_redirect_uri"
HOME_CATEGORIES = ("movies", "tv")
HOME_TIMEFRAMES = ("today", "week")


# -- Account connection ------------------------------------------------------


@login_required
@require_GET
def connect(request):
    if not is_configured():
        return HttpResponse(
            "SIMKL integration is not configured by the server administrator.",
            status=503,
        )
    state = secrets.token_urlsafe(32)
    redirect_uri = redirect_uri_for(request)
    request.session[OAUTH_STATE_SESSION_KEY] = state
    # The token exchange must repeat the exact URI the consent screen saw.
    request.session[OAUTH_REDIRECT_SESSION_KEY] = redirect_uri
    request.session.modified = True
    return redirect(
        build_authorize_url(
            client_id=settings.SIMKL_CLIENT_ID,
            redirect_uri=redirect_uri,
            state=state,
            app_name=settings.SIMKL_APP_NAME,
            app_version=settings.SIMKL_APP_VERSION,
        )
    )


@login_required
@require_GET
def callback(request):
    expected_state = request.session.pop(OAUTH_STATE_SESSION_KEY, None)
    redirect_uri = request.session.pop(OAUTH_REDIRECT_SESSION_KEY, None) or redirect_uri_for(request)
    request.session.modified = True
    received_state = request.GET.get("state", "")
    if (
        not expected_state
        or not received_state
        or not secrets.compare_digest(expected_state, received_state)
    ):
        return HttpResponseBadRequest("Invalid SIMKL OAuth state.")
    if not is_configured():
        return HttpResponse(
            "SIMKL integration is not configured by the server administrator.",
            status=503,
        )
    code = request.GET.get("code", "")
    if not code:
        return HttpResponseBadRequest("SIMKL OAuth callback did not include a code.")

    client = _client("")
    try:
        token = client.exchange_code(code, redirect_uri)
        profile = _client(token.access_token).get_user_settings()
    except SimklError as exc:
        return HttpResponse(f"Unable to connect SIMKL: {exc}", status=502)

    user_block = profile.get("user") or {}
    account_block = profile.get("account") or {}
    account_defaults = {
        "simkl_username": str(user_block.get("name") or "")[:255],
        "simkl_user_id": str(account_block.get("id") or "")[:32],
        "account_type": str(account_block.get("type") or "")[:16],
        "access_token": token.access_token,
        "initial_sync_complete": False,
        "activities_cursor": "",
        "last_activities": {},
        "pending_pushes": {},
        "sync_status": SimklAccount.SyncStatus.OK,
        "last_error": "",
        "last_warning": "",
        "last_seen_at": timezone.now(),
    }
    account = SimklAccount.objects.only("id").filter(user=request.user).first()
    if account is None:
        account = SimklAccount.objects.create(user=request.user, **account_defaults)
    else:
        SimklAccount.objects.filter(id=account.id).update(
            **account_defaults,
            updated_at=timezone.now(),
        )
        # A fresh token means a fresh full pull; the old mirror is stale.
        account.library_items.all().delete()
    enqueue_account_sync(account.id)
    messages.success(request, _("SIMKL account connected. Initial synchronization queued."))
    return redirect(reverse("index"))


@login_required
@require_POST
def disconnect(request):
    SimklSyncIntent.objects.filter(user=request.user).delete()
    SimklAccount.objects.filter(user=request.user).delete()
    messages.success(request, _("SIMKL account disconnected."))
    return HttpResponse(status=204, headers={"HX-Refresh": "true"})


@login_required
@require_POST
def sync(request):
    account = SimklAccount.objects.filter(user=request.user).only("id").first()
    if account is None:
        return HttpResponseNotFound("No SIMKL account is connected.")
    # A manual sync retries whatever SIMKL ignored or could not match before.
    SimklAccount.objects.filter(id=account.id).update(
        pending_pushes={},
        last_seen_at=timezone.now(),
        updated_at=timezone.now(),
    )
    enqueue_account_sync(account.id)
    messages.success(request, _("SIMKL synchronization queued."))
    return HttpResponse(status=204, headers={"HX-Refresh": "true"})


def _client(access_token: str) -> SimklClient:
    return SimklClient(
        access_token,
        client_id=settings.SIMKL_CLIENT_ID,
        client_secret=settings.SIMKL_CLIENT_SECRET,
        app_name=settings.SIMKL_APP_NAME,
        app_version=settings.SIMKL_APP_VERSION,
    )


# -- Detail page card --------------------------------------------------------


@only_htmx
@htmx_login_required
@require_GET
def media_info(request, media_type, external_id):
    """The SIMKL card of a detail page, fetched on demand.

    Fetching happens here, in the HTMX request, so the page never needs a
    reload to show SIMKL data and untracked titles get it too.
    """
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
    )
    return render(request, "simkl/fragments/media_info.html", {"simkl": card})


@login_required
@require_GET
def open_item(request, media_type, simkl_id):
    """Send a SIMKL-only reference (a recommendation) to its Argus page."""
    # SIMKL labels TV as "tv" in detail records and "show" in sync payloads.
    media_type = {"tv": "show", "shows": "show", "movies": "movie"}.get(media_type, media_type)
    if media_type not in {"movie", "show", "anime"}:
        return HttpResponseNotFound("Unknown media type.")
    target = resolve_argus_url(media_type, simkl_id)
    if target is None:
        # Not in TMDB/TVDB as far as SIMKL knows: the SIMKL page is the best
        # we can offer.
        target = build_simkl_url(media_type, simkl_id)
    return redirect(target)


# -- Discovery ---------------------------------------------------------------


DISCOVER_SECTIONS = {
    "trending-movies": {
        "heading": _("Trending Movies on Simkl"),
        "link": trending.MOST_WATCHED_URLS["movies"],
        "timeframes": True,
    },
    "trending-tv": {
        "heading": _("Trending TV Shows on Simkl"),
        "link": trending.MOST_WATCHED_URLS["tv"],
        "timeframes": True,
    },
    "trending-anime": {
        "heading": _("Trending Anime on Simkl"),
        "link": trending.MOST_WATCHED_URLS["anime"],
        "timeframes": True,
    },
    "premieres": {
        "heading": _("TV Premieres on Simkl"),
        "link": "https://simkl.com/tv/premieres/",
        "timeframes": False,
    },
    "airing-today": {
        "heading": _("Airing Today on Simkl"),
        "link": "https://simkl.com/tv/airing/",
        "timeframes": False,
    },
    "movie-releases": {
        "heading": _("Movie Releases on Simkl"),
        "link": "https://simkl.com/movies/dvd-releases/",
        "timeframes": False,
    },
}


@htmx_login_required
@require_GET
def discover(request):
    from apps.mdblist.config import catalog_available_for
    from apps.mdblist.views import discover_sections as mdblist_discover_sections

    simkl_available = catalog_configured()
    mdblist_sections = (
        mdblist_discover_sections(request.user) if catalog_available_for(request.user) else []
    )
    return render(
        request,
        "simkl/pages/discover.html",
        {
            "available": simkl_available or bool(mdblist_sections),
            "simkl_available": simkl_available,
            "sections": [
                {"key": key, **section} for key, section in DISCOVER_SECTIONS.items()
            ],
            # Personal recommendations lead the page; the rest follow SIMKL.
            "mdblist_top_sections": [s for s in mdblist_sections if s["key"] == "recommended"],
            "mdblist_sections": [s for s in mdblist_sections if s["key"] != "recommended"],
            "timeframes": trending.TIMEFRAMES,
        },
    )


@only_htmx
@htmx_login_required
@require_GET
def discover_section(request, section):
    config = DISCOVER_SECTIONS.get(section)
    if config is None:
        return HttpResponseNotFound("Unknown section.")
    timeframe = request.GET.get("timeframe", "today")
    if timeframe not in trending.TIMEFRAMES:
        timeframe = "today"
    hide_seen = request.GET.get("show_seen") != "1"
    user = request.user

    if section.startswith("trending-"):
        category = {"trending-movies": "movies", "trending-tv": "tv", "trending-anime": "anime"}[section]
        entries = trending.trending_section(
            user,
            category,
            timeframe,
            limit=24,
            hide_seen=hide_seen,
        )
        template = "simkl/fragments/trending_rail.html"
    else:
        if section == "premieres":
            entries = trending.premieres()
        elif section == "airing-today":
            entries = trending.airing_on(timezone.localdate())
        else:
            entries = trending.movie_releases()
        entries = trending.annotate_user_state(user, entries)
        template = "simkl/fragments/calendar_rail.html"

    return render(
        request,
        template,
        {"entries": entries, "section": section, "timeframe": timeframe},
    )


@only_htmx
@htmx_login_required
@require_GET
def home_trending(request):
    category = request.GET.get("category", "movies")
    if category not in HOME_CATEGORIES:
        category = "movies"
    timeframe = request.GET.get("timeframe", "today")
    if timeframe not in HOME_TIMEFRAMES:
        timeframe = "today"
    entries = trending.trending_section(
        request.user,
        category,
        timeframe,
        limit=18,
        hide_seen=True,
    )
    return render(
        request,
        "simkl/fragments/trending_rail.html",
        {"entries": entries, "section": f"home-{category}", "timeframe": timeframe},
    )
