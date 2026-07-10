# Icon action controls design

## Goal

Replace repetitive text action buttons with compact, accessible icon controls
while preserving every existing Django and HTMX behavior.

## Interaction rules

- Watched state uses native DaisyUI checkboxes for movies, shows, seasons, and
  episodes. Checked means watched; changing the checkbox invokes the existing
  HTMX endpoint and target.
- Tracking uses a bookmark icon. The icon's style communicates its current
  tracked state.
- Dropping a show uses a bookmark-minus icon with warning treatment.
- Deleting a show uses a trash icon with error treatment and retains the
  existing confirmation prompt.
- IMDb and trailer controls remain labelled because their external destinations
  are not self-evident state toggles.

## Accessibility and visual language

- Every icon-only control has a DaisyUI tooltip, visible keyboard focus, and a
  translated `aria-label`.
- Controls remain `btn btn-square` at an appropriate size and retain disabled
  behavior while HTMX requests are in flight.
- Tooltips supplement labels; they do not replace accessible names.

## Scope

- Update movie and TV action fragments, season controls, and episode-detail
  watched controls.
- Preserve existing route names, HTMX attributes, fragment target IDs, and
  confirmation behavior.
- Verify via the current movie, TV, episode-detail, and catalog test modules
  plus the frontend build.
