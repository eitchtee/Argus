class ProviderError(Exception):
    """Base exception for catalog provider failures."""


class NotFound(ProviderError):
    """Raised when a provider cannot find the requested item."""


class RateLimited(ProviderError):
    """Raised when a provider asks the caller to slow down."""


class AuthError(ProviderError):
    """Raised when provider credentials are missing or rejected."""
