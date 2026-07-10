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

        if tz_cookie and user_timezone == "auto":
            timezone.activate(zoneinfo.ZoneInfo(tz_cookie))
        elif user_timezone != "auto":
            timezone.activate(zoneinfo.ZoneInfo(user_timezone))
        else:
            timezone.activate(zoneinfo.ZoneInfo("UTC"))

        if user_language and user_language != "auto":
            translation.activate(user_language)
        else:
            detected_language = translation.get_language_from_request(request)
            translation.activate(detected_language)

        return self.get_response(request)