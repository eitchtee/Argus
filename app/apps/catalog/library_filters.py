"""Helpers shared by the TV and movie library pages (watchlist, watched)."""

from django.db.models import prefetch_related_objects

from apps.catalog.localization import metadata_language_for_user, resolve_field


def pick(params, name, choices, default=""):
    """Return ``params[name]`` when it is one of ``choices``, else ``default``."""
    value = params.get(name, default)
    return value if value in choices else default


def pick_sort(params, sort_choices):
    """The requested sort when valid, else the first choice."""
    return pick(params, "sort", dict(sort_choices), sort_choices[0][0])


def attach_genre_names(user, media_items):
    """Stamp localized ``genre_names`` on each item; return every name, sorted."""
    prefetch_related_objects(media_items, "genres")
    names = set()
    for media in media_items:
        language = metadata_language_for_user(user, media.provider)
        media.genre_names = {
            resolve_field(genre, "name", language) for genre in media.genres.all()
        }
        names |= media.genre_names
    return sorted(names, key=str.casefold)


def sorted_nulls_last(items, key, *, reverse=False):
    present = [item for item in items if key(item) is not None]
    missing = [item for item in items if key(item) is None]
    return sorted(present, key=key, reverse=reverse) + missing


def title_key(record):
    return (getattr(record, "title", None) or record.name).casefold()
