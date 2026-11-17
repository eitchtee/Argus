from django import template
from django.utils.translation import gettext_lazy as _

register = template.Library()

# Full class names spelled out so Tailwind's scanner of app/ generates them.
TOAST_STYLES = {
    "success": {"alert": "alert-success", "icon": "circle-check", "title": _("Success")},
    "warning": {"alert": "alert-warning", "icon": "triangle-alert", "title": _("Warning")},
    "error": {"alert": "alert-error", "icon": "circle-alert", "title": _("Error")},
    "info": {"alert": "alert-info", "icon": "info", "title": _("Info")},
}


@register.filter
def toast_style(tags):
    for level in tags.split():
        if level in TOAST_STYLES:
            return TOAST_STYLES[level]
    return TOAST_STYLES["info"]
