from datetime import datetime

from django import template
from django.utils import timezone
from django.utils.formats import date_format, time_format

from apps.catalog.localization import (
    date_format_for_user,
    datetime_format_for_user,
    time_format_for_user,
)

register = template.Library()


def _user_from_context(context):
    request = context.get("request")
    user = getattr(request, "user", None)
    if user is not None and getattr(user, "is_authenticated", False):
        return user
    return None


def _local_time(value):
    if isinstance(value, datetime) and timezone.is_aware(value):
        return timezone.localtime(value)
    return value


@register.simple_tag(takes_context=True)
def user_date(context, value):
    if value is None:
        return ""

    value = _local_time(value)
    return date_format(value, date_format_for_user(_user_from_context(context)), use_l10n=False)


@register.simple_tag(takes_context=True)
def user_datetime(context, value):
    if value is None:
        return ""

    value = _local_time(value)
    return date_format(
        value,
        datetime_format_for_user(_user_from_context(context)),
        use_l10n=False,
    )


@register.simple_tag(takes_context=True)
def user_time(context, value):
    if value is None:
        return ""

    value = _local_time(value)
    return time_format(value, time_format_for_user(_user_from_context(context)), use_l10n=False)


@register.simple_tag(takes_context=True)
def user_month(context, value):
    if value is None:
        return ""

    return date_format(_local_time(value), "F Y", use_l10n=False)
