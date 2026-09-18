from django.conf import settings

from apps.simkl.client import SimklClient, SimklError


class SimklConfigurationError(SimklError):
    pass


def is_configured() -> bool:
    """Account sync needs the full OAuth application (id and secret).

    The OAuth callback URL is Argus's own route and is derived from the
    request; ``SIMKL_REDIRECT_URI`` only overrides it.
    """
    return bool(settings.SIMKL_CLIENT_ID and settings.SIMKL_CLIENT_SECRET)


def redirect_uri_for(request) -> str:
    """The callback SIMKL must send the user back to.

    SIMKL compares it byte for byte with the URL registered in the app, so
    the value shown on the settings page is the one to register.
    """
    from django.urls import reverse

    return settings.SIMKL_REDIRECT_URI or request.build_absolute_uri(reverse("simkl_callback"))


def catalog_configured() -> bool:
    """Catalog reads, trending and calendar files only need the client id."""
    return bool(settings.SIMKL_CLIENT_ID)


def build_client(account=None) -> SimklClient:
    if account is not None and not is_configured():
        raise SimklConfigurationError(
            "SIMKL client credentials and redirect URI are not configured "
            "by the server administrator."
        )
    if account is None and not catalog_configured():
        raise SimklConfigurationError(
            "SIMKL client id is not configured by the server administrator."
        )
    return SimklClient(
        account.access_token if account is not None else "",
        client_id=settings.SIMKL_CLIENT_ID,
        client_secret=settings.SIMKL_CLIENT_SECRET,
        app_name=settings.SIMKL_APP_NAME,
        app_version=settings.SIMKL_APP_VERSION,
    )
