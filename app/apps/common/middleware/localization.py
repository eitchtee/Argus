import zoneinfo

from django.utils import timezone, translation

from apps.users.models import UserSettings


class LocalizationMiddleware:
    """Activates the user's preferred language and timezone for each request."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user_language = "auto"
        user_timezone = "auto"

        tz_cookie = request.COOKIES.get("mytz")
        if request.user.is_authenticated:
            if hasattr(request.user, "settings"):
                user_settings = request.user.settings
                user_language = user_settings.language
                user_timezone = user_settings.timezone
            else:
                # Create UserSettings if it doesn't exist
                UserSettings.objects.create(user=request.user)

        configured_timezone = (
            user_timezone if user_timezone and user_timezone != "auto" else tz_cookie
        )
        active_timezone = self._get_timezone(configured_timezone)
        timezone.activate(active_timezone or timezone.get_default_timezone())

        if user_language and user_language != "auto":
            translation.activate(user_language)
        else:
            detected_language = translation.get_language_from_request(request)
            translation.activate(detected_language or translation.get_default_language())

        return self.get_response(request)

    @staticmethod
    def _get_timezone(name):
        if not name or name == "auto":
            return None

        try:
            return zoneinfo.ZoneInfo(name)
        except (TypeError, ValueError, zoneinfo.ZoneInfoNotFoundError):
            return None
