from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from apps.mdblist.client import MdblistClient, MdblistError


# OAuth tokens live 30 days; renew them well before that.
TOKEN_REFRESH_MARGIN = timedelta(days=3)


class MdblistConfigurationError(MdblistError):
    pass


def oauth_configured() -> bool:
    """Connecting through OAuth needs the full application (id and secret).

    The callback URL is Argus's own route and is derived from the request;
    ``MDBLIST_REDIRECT_URI`` only overrides it. Without an OAuth app users
    can still connect by pasting their personal API key.
    """
    return bool(settings.MDBLIST_CLIENT_ID and settings.MDBLIST_CLIENT_SECRET)


def redirect_uri_for(request) -> str:
    from django.urls import reverse

    return settings.MDBLIST_REDIRECT_URI or request.build_absolute_uri(reverse("mdblist_callback"))


def catalog_configured() -> bool:
    """Ratings, charts and lists shared by every user need the server API key."""
    return bool(settings.MDBLIST_API_KEY)


def build_client(account=None) -> MdblistClient:
    """A client for ``account``'s own data, or the server's catalog client."""
    if account is None:
        if not catalog_configured():
            raise MdblistConfigurationError(
                "MDBList API key is not configured by the server administrator."
            )
        return MdblistClient(settings.MDBLIST_API_KEY, api_key=True)
    from apps.mdblist.models import MdblistAccount

    if account.auth_method == MdblistAccount.AuthMethod.API_KEY:
        return MdblistClient(account.access_token, api_key=True)
    if not oauth_configured():
        raise MdblistConfigurationError(
            "MDBList OAuth application is not configured by the server administrator."
        )
    client = oauth_client(account.access_token)
    _refresh_if_expiring(account, client)
    return client


def oauth_client(access_token: str = "") -> MdblistClient:
    return MdblistClient(
        access_token,
        client_id=settings.MDBLIST_CLIENT_ID,
        client_secret=settings.MDBLIST_CLIENT_SECRET,
    )


def catalog_client_for(user) -> MdblistClient | None:
    """The server client, or the viewer's own credentials when the server has
    no key of its own; ``None`` when neither is available."""
    if catalog_configured():
        return build_client()
    account = user_account(user)
    if account is None:
        return None
    try:
        return build_client(account)
    except MdblistError:
        return None


def user_account(user):
    from apps.mdblist.models import MdblistAccount

    if user is None or not getattr(user, "is_authenticated", False):
        return None
    cached = getattr(user, "_mdblist_account_cache", False)
    if cached is not False:
        return cached
    account = (
        MdblistAccount.objects.filter(user=user)
        .exclude(sync_status=MdblistAccount.SyncStatus.REAUTHORIZE)
        .first()
    )
    user._mdblist_account_cache = account
    return account


def catalog_available_for(user) -> bool:
    return catalog_configured() or user_account(user) is not None


def _refresh_if_expiring(account, client: MdblistClient) -> None:
    expires_at = account.token_expires_at
    if not account.refresh_token or (
        expires_at is not None and expires_at > timezone.now() + TOKEN_REFRESH_MARGIN
    ):
        return
    refresh_account_token(account, client)


def refresh_account_token(account, client: MdblistClient) -> None:
    """Swap the refresh token for a new pair and persist it."""
    from apps.mdblist.models import MdblistAccount

    token = client.refresh(account.refresh_token)
    account.access_token = token.access_token
    account.refresh_token = token.refresh_token or account.refresh_token
    account.token_expires_at = (
        timezone.now() + timedelta(seconds=token.expires_in) if token.expires_in else None
    )
    MdblistAccount.objects.filter(id=account.id).update(
        access_token=account.access_token,
        refresh_token=account.refresh_token,
        token_expires_at=account.token_expires_at,
        updated_at=timezone.now(),
    )
    client.access_token = account.access_token
