# Cinematic template polish design

## Goal

Polish the existing Django templates while preserving Argus's dark, editorial,
media-focused character. The result must use Tailwind CSS v4 utilities and
DaisyUI semantic theme tokens, with no backend behavior changes.

## Visual system

- Keep `argus_dark` as the default and retain `argus_light` as its equivalent.
- Prefer DaisyUI semantic colors (`base-*`, `primary`, `secondary`, `accent`,
  and status colors) over arbitrary color utilities in templates.
- Introduce a consistent content rhythm: responsive page padding, a readable
  maximum content width, restrained surface contrast, and shared focus/hover
  behavior.
- Preserve the current mono typeface and warm primary accent; use typography,
  spacing, and contrast—not a new brand identity—to improve hierarchy.

## Shared chrome

- Refine the desktop sidebar and mobile navbar with clearer logo treatment,
  active navigation state, and balanced spacing.
- Update the base layout so all pages inherit the same responsive content shell
  and a calmer root background.
- Keep the existing Bootstrap offcanvas behavior; only its presentation changes.

## Templates

- Improve the login screen with a focused authentication panel and better demo
  credential presentation.
- Make home, search results, media cards, and media-detail templates
  poster-forward, with clearer metadata hierarchy and compact action groups.
- Align watchlist, upcoming, episode, form, alert, toast, and empty/loading
  states to the same surface, border, and spacing system.
- Preserve existing URLs, HTMX targets/swaps, translations, template context,
  and form rendering contracts.

## CSS architecture

- Consolidate reusable visual rules in the Tailwind entry stylesheet using
  Tailwind v4 layers and DaisyUI tokens.
- Keep component templates utility-first; add small named component classes
  only where several templates need the same structural treatment.
- Do not add a CSS framework or change the current Vite asset pipeline.

## Verification

- Build the frontend bundle to validate Tailwind v4 and DaisyUI compilation.
- Run the relevant Django template/view tests.
- Inspect changed template output for responsive classes, semantic theme tokens,
  and preserved interactive attributes.
