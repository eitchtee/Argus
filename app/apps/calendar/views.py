import calendar as python_calendar
from collections import defaultdict
from datetime import date, datetime, timedelta
from urllib.parse import urlencode

from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET

from apps.common.decorators.user import htmx_login_required

from .events import (
    filter_query_params,
    get_calendar_event,
    get_calendar_events,
    get_calendar_feed,
    get_feed_window,
    parse_filters,
)
from .ical import render_icalendar
from .models import CalendarFeed


@htmx_login_required
@require_GET
def calendar_page(request):
    filters = parse_filters(request.GET)
    month = _parse_month(request.GET.get("month"))
    weeks = python_calendar.Calendar(firstweekday=python_calendar.MONDAY).monthdatescalendar(
        month.year, month.month
    )
    grid_start = weeks[0][0]
    grid_end = weeks[-1][-1]
    events = get_calendar_events(
        request.user,
        grid_start - timedelta(days=1),
        grid_end + timedelta(days=1),
        filters=filters,
    )
    events_by_date = defaultdict(list)
    for event in events:
        display_date = _display_date(event)
        if grid_start <= display_date <= grid_end:
            events_by_date[display_date].append(event)

    today = timezone.localdate()
    feed = get_calendar_feed(request.user)
    feed_url = request.build_absolute_uri(
        reverse("calendar-feed", kwargs={"uuid": feed.uuid})
    )
    feed_params = urlencode(filter_query_params(filters))
    if feed_params:
        feed_url = f"{feed_url}?{feed_params}"

    context = {
        "month": month,
        "weeks": [
            [
                {
                    "date": cell_date,
                    "is_current_month": cell_date.month == month.month,
                    "is_today": cell_date == today,
                    "is_past": cell_date < today,
                    "events": events_by_date.get(cell_date, []),
                }
                for cell_date in week
            ]
            for week in weeks
        ],
        "has_events": bool(events_by_date),
        "filters": filters,
        "feed_url": feed_url,
        "previous_query": _month_query(_previous_month(month), filters),
        "current_query": _month_query(today.replace(day=1), filters),
        "next_query": _month_query(_next_month(month), filters),
    }
    return render(request, "calendar/pages/index.html", context)


@htmx_login_required
@require_GET
def calendar_episode_detail(request, episode_id):
    event = get_calendar_event(request.user, episode_id)
    if event is None:
        raise Http404

    return render(
        request,
        "calendar/fragments/episode_detail.html",
        {"event": event, "status_label": _status_label(event.status)},
    )


@htmx_login_required
@require_GET
def calendar_movie_detail(request, movie_id):
    event = get_calendar_event(request.user, movie_id, kind="movie")
    if event is None:
        raise Http404

    return render(
        request,
        "calendar/fragments/movie_detail.html",
        {"event": event, "status_label": _status_label(event.status)},
    )


@require_GET
def calendar_feed(request, uuid):
    feed = get_object_or_404(CalendarFeed, uuid=uuid)
    filters = parse_filters(request.GET)
    start_date, end_date = get_feed_window()
    events = get_calendar_events(
        feed.user,
        start_date,
        end_date,
        filters=filters,
    )
    response = HttpResponse(
        render_icalendar(events),
        content_type="text/calendar; charset=utf-8",
    )
    response["Content-Disposition"] = 'inline; filename="argus-calendar.ics"'
    response["Cache-Control"] = "no-cache"
    return response


def _parse_month(value: str | None) -> date:
    if value:
        try:
            parsed = datetime.strptime(value, "%Y-%m")
            return parsed.date().replace(day=1)
        except ValueError:
            pass
    return timezone.localdate().replace(day=1)


def _display_date(event) -> date:
    if event.starts_at is not None:
        return timezone.localtime(event.starts_at).date()
    return event.release_date


def _status_label(status: str) -> str:
    return {
        "tracked": "Tracked",
        "paused": "Paused",
        "dropped": "Dropped",
    }.get(status, status)


def _previous_month(month: date) -> date:
    return (month - timedelta(days=1)).replace(day=1)


def _next_month(month: date) -> date:
    if month.month == 12:
        return month.replace(year=month.year + 1, month=1)
    return month.replace(month=month.month + 1)


def _month_query(month: date, filters) -> str:
    params = {"month": month.strftime("%Y-%m")}
    params.update(filter_query_params(filters))
    return urlencode(params)
