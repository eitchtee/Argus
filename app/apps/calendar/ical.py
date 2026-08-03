from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from typing import Iterable

from django.utils import translation
from django.utils.translation import gettext as _, ngettext
from icalendar import Calendar, Event

from .events import CalendarEvent

UTC = timezone.utc


def render_icalendar(
    events: Iterable[CalendarEvent],
    *,
    interface_language: str | None = None,
    now: datetime | None = None,
) -> str:
    language_context = (
        translation.override(interface_language)
        if interface_language and interface_language != "auto"
        else nullcontext()
    )
    with language_context:
        timestamp = _as_utc(now or datetime.now(UTC))
        calendar = Calendar()
        calendar.add("prodid", "-//Argus//TV Calendar//EN")
        calendar.add("version", "2.0")
        calendar.add("calscale", "GREGORIAN")
        calendar.add("method", "PUBLISH")
        calendar.add("x-wr-calname", _("Argus TV releases"))
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
        return f"📽️ {event.title}"

    parts = [part for part in (event.show_name, event.subtitle) if part]
    return f"📺 {' '.join(parts)}"


def _description(event: CalendarEvent) -> str:
    lines = []
    if event.kind == "movie":
        if event.overview:
            lines.append(f"📄 {event.overview}")
        if event.runtime:
            lines.append(f"⏳ {_runtime_label(event.runtime)}")
        return "\n".join(lines)

    if event.title:
        lines.append(f"📛 {event.title}")
    if event.overview:
        lines.append(f"📄 {event.overview}")
    if event.runtime:
        lines.append(f"⏳ {_runtime_label(event.runtime)}")
    if event.network:
        lines.append(f"📍 {event.network}")
    return "\n".join(lines)


def _runtime_label(minutes: int) -> str:
    return ngettext("%(num)d minute", "%(num)d minutes", minutes) % {
        "num": minutes,
    }
