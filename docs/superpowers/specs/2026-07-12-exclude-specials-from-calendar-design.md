# Exclude TV Specials from the Calendar

## Objective

Prevent TV episodes from season 0 (specials) from appearing in the calendar month view or calendar feed, while keeping direct calendar episode-detail URLs available.

## Scope

- Apply the exclusion to the shared calendar event listing used by the month view and `.ics` feed.
- Keep `get_calendar_event()` unchanged so a user can still open a direct detail page for a season-0 episode.
- Do not change TV episode storage, provider imports, or other TV pages.

## Design

Add `season_number__gt=0` to the `Episode` queryset in `apps.calendar.events._get_episode_events()`. Both calendar consumers already call `get_calendar_events()`, so this single database-level predicate keeps specials out of both outputs without fetching them first.

No migration or frontend change is required.

## Testing

Add a regression test to the calendar event-service tests with a tracked season-0 episode and a regular episode in the same date range. Assert that only the regular episode is returned. Add view/feed coverage that verifies a season-0 episode is absent from the rendered calendar and generated iCal output while regular episodes remain present.

## Acceptance Criteria

1. Season-0 episodes are absent from `get_calendar_events()` results.
2. Season-0 episodes do not render on `/calendar/`.
3. Season-0 episodes do not appear in the calendar feed endpoint.
4. Episodes from seasons 1 and above continue to appear.
5. Direct season-0 episode-detail lookups remain unchanged.
