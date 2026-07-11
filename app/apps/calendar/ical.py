from datetime import datetime, timedelta, timezone
from typing import Iterable

from icalendar import Calendar, Event

from .events import CalendarEvent

UTC = timezone.utc


def render_icalendar(
    events: Iterable[CalendarEvent], *, now: datetime | None = None
) -> str:
    timestamp = _as_utc(now or datetime.now(UTC))
    calendar = Calendar()
    calendar.add("prodid", "-//Argus//TV Calendar//EN")
    calendar.add("version", "2.0")
    calendar.add("calscale", "GREGORIAN")
    calendar.add("method", "PUBLISH")
    calendar.add("x-wr-calname", "Argus TV releases")
    calendar.add("x-wr-timezone", "UTC")

    for item in events:
        component = Event()
        component.add("uid", f"{item.kind}-{item.object_id}@argus")
        component.add("dtstamp", timestamp)
        component.add("summary", _summary(item))
        component.add("description", _description(item))
        if item.starts_at is None:
            component.add("dtstart", item.release_date)
            component.add("dtend", item.release_date + timedelta(days=1))
        else:
            component.add("dtstart", _as_utc(item.starts_at))
            if item.ends_at is not None:
                component.add("dtend", _as_utc(item.ends_at))
        calendar.add_component(component)

    return calendar.to_ical().decode("utf-8")


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _summary(event: CalendarEvent) -> str:
    if event.kind == "movie":
        return event.title

    parts = [event.show_name, event.subtitle]
    if event.title:
        parts.append(event.title)
    return " - ".join(parts)


def _description(event: CalendarEvent) -> str:
    lines = []
    if event.overview:
        lines.append(event.overview)
    if event.network:
        lines.append(f"Network: {event.network}")
    if event.director:
        lines.append(f"Director: {event.director}")
    if event.genres:
        lines.append(f"Genres: {', '.join(event.genres)}")
    if event.runtime:
        lines.append(f"Runtime: {event.runtime} minutes")
    lines.append(f"Status: {event.status}")
    return "\n".join(lines)
